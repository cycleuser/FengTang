"""Minimal pure-Python DNS client (MX/A lookups) — no dnspython, no system tools.

Reads the system nameservers from /etc/resolv.conf (POSIX) and falls back to a
small list of public resolvers. Implements just enough of RFC 1035 to resolve MX
records (with A-record fallback per RFC 5321 §5.1), including name compression.
"""

from __future__ import annotations

import random
import socket
import struct
from pathlib import Path

DEFAULT_NAMESERVERS = ("223.5.5.5", "119.29.29.29", "8.8.8.8", "1.1.1.1")

TYPE_A = 1
TYPE_MX = 15
CLASS_IN = 1


def system_nameservers() -> list[str]:
    """Return configured nameservers (POSIX resolv.conf), else public defaults."""
    servers: list[str] = []
    try:
        for line in Path("/etc/resolv.conf").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("nameserver"):
                parts = line.split()
                if len(parts) >= 2 and parts[1] not in servers:
                    servers.append(parts[1])
    except OSError:
        pass
    return servers or list(DEFAULT_NAMESERVERS)


def _encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.rstrip(".").split("."):
        if any(ord(c) > 127 for c in label):
            encoded = label.encode("idna")
        else:
            encoded = label.encode("ascii")
        if len(encoded) > 63:
            raise ValueError(f"DNS label too long: {label!r}")
        out.append(len(encoded))
        out += encoded
    out.append(0)
    return bytes(out)


def _decode_name(packet: bytes, offset: int, depth: int = 0) -> tuple[str, int]:
    """Decode a (possibly compressed) DNS name; returns (name, next_offset)."""
    if depth > 10:
        raise ValueError("DNS compression loop")
    labels: list[str] = []
    while True:
        if offset >= len(packet):
            raise ValueError("truncated DNS name")
        length = packet[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:  # compression pointer
            if offset + 2 > len(packet):
                raise ValueError("truncated pointer")
            pointer = struct.unpack("!H", packet[offset : offset + 2])[0] & 0x3FFF
            suffix, _ = _decode_name(packet, pointer, depth + 1)
            labels.append(suffix)
            offset += 2
            break
        offset += 1
        labels.append(packet[offset : offset + length].decode("ascii", "replace"))
        offset += length
    return ".".join(labels), offset


def _raw_query(name: str, qtype: int, timeout: float = 6.0) -> bytes:
    query_id = random.randint(0, 0xFFFF)
    header = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    question = _encode_name(name) + struct.pack("!HH", qtype, CLASS_IN)
    packet = header + question

    errors: list[str] = []
    for server in system_nameservers():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(packet, (server, 53))
                data, _ = sock.recvfrom(8192)
        except OSError as exc:
            errors.append(f"{server}: {exc}")
            continue
        if len(data) < 12:
            errors.append(f"{server}: short response")
            continue
        resp_id = struct.unpack("!H", data[:2])[0]
        if resp_id != query_id:
            errors.append(f"{server}: id mismatch")
            continue
        return data
    raise OSError("DNS query failed: " + "; ".join(errors))


def _parse_answers(packet: bytes) -> list[tuple[int, int]]:
    """Return [(rtype, rdata_offset), ...] for all answer records."""
    _id, _flags, _qd, ancount, _ns, _ar = struct.unpack("!HHHHHH", packet[:12])
    offset = 12
    try:
        _qname, offset = _decode_name(packet, offset)
    except ValueError:
        return []
    offset += 4  # QTYPE + QCLASS
    answers: list[tuple[int, int]] = []
    for _ in range(ancount):
        try:
            _name, offset = _decode_name(packet, offset)
            if offset + 10 > len(packet):
                break
            rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", packet[offset : offset + 10])
            offset += 10
            answers.append((rtype, offset))
            offset += rdlength
        except (ValueError, struct.error):
            break
    return answers


def resolve_mx(domain: str) -> list[str]:
    """Return MX hosts for `domain`, ordered by preference (lowest first).

    Falls back to the domain itself (implicit MX, RFC 5321 §5.1) when DNS has no
    MX records.
    """
    try:
        packet = _raw_query(domain, TYPE_MX)
    except OSError:
        return [domain]
    hosts: list[tuple[int, str]] = []
    for rtype, rdata_offset in _parse_answers(packet):
        if rtype != TYPE_MX or rdata_offset + 3 > len(packet):
            continue
        preference = struct.unpack("!H", packet[rdata_offset : rdata_offset + 2])[0]
        try:
            exchange, _ = _decode_name(packet, rdata_offset + 2)
        except ValueError:
            continue
        if exchange and exchange != ".":
            hosts.append((preference, exchange.rstrip(".")))
    if not hosts:
        return [domain]
    hosts.sort(key=lambda item: item[0])
    return [host for _pref, host in hosts]


def resolve_a(host: str) -> list[str]:
    """Return IPv4 addresses for `host`."""
    try:
        packet = _raw_query(host, TYPE_A)
    except OSError:
        return []
    ips: list[str] = []
    for rtype, rdata_offset in _parse_answers(packet):
        if rtype == TYPE_A and rdata_offset + 4 <= len(packet):
            ips.append(socket.inet_ntoa(packet[rdata_offset : rdata_offset + 4]))
    return ips


def resolve_mx_with_ips(domain: str) -> list[tuple[str, str | None]]:
    """MX hosts paired with a resolved IPv4 address (best-effort)."""
    out: list[tuple[str, str | None]] = []
    for host in resolve_mx(domain):
        ips = resolve_a(host)
        out.append((host, ips[0] if ips else None))
    return out
