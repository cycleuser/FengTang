"""Portable account bundles: export/import credentials between machines.

Only account configuration is exported (email, auth settings, passwords,
OAuth tokens) — never the message database. Bundles are JSON; they may be
encrypted with a passphrase using only the standard library.

Encryption construction (encrypt-then-MAC, stdlib only):
  key material = PBKDF2-HMAC-SHA256(passphrase, salt, 200_000 iters, 64 bytes)
  enc_key, mac_key = key[:32], key[32:]
  keystream    = HMAC-SHA256(enc_key, nonce || counter) for counter = 0,1,2,...
  ciphertext   = plaintext XOR keystream
  tag          = HMAC-SHA256(mac_key, header || nonce || ciphertext)

This is a sound composition of standard primitives; it is not a substitute for
age/GPG, but it means no third-party dependency is needed to move credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fengtang.core.config import Account, Config
from fengtang.core.errors import ConfigError

BUNDLE_MAGIC = "fengtang-account-bundle"
BUNDLE_VERSION = 1
_PBKDF2_ITERS = 200_000


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive_keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    material = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, _PBKDF2_ITERS, 64)
    return material[:32], material[32:]


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    chunks: list[bytes] = []
    produced = 0
    counter = 0
    while produced < length:
        block = hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        chunks.append(block)
        produced += len(block)
        counter += 1
    return b"".join(chunks)[:length]


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream, strict=True))


def _encrypt(plaintext: bytes, passphrase: str) -> dict[str, Any]:
    salt = os.urandom(16)
    nonce = os.urandom(16)
    enc_key, mac_key = _derive_keys(passphrase, salt)
    ciphertext = _xor(plaintext, _keystream(enc_key, nonce, len(plaintext)))
    mac = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).hexdigest()
    return {
        "magic": BUNDLE_MAGIC,
        "version": BUNDLE_VERSION,
        "encrypted": True,
        "kdf": f"pbkdf2-hmac-sha256:{_PBKDF2_ITERS}",
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
        "mac": mac,
    }


def _decrypt(bundle: dict[str, Any], passphrase: str) -> bytes:
    try:
        salt = _unb64(bundle["salt"])
        nonce = _unb64(bundle["nonce"])
        ciphertext = _unb64(bundle["ciphertext"])
        mac = str(bundle["mac"])
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"Malformed encrypted bundle: {exc}") from exc
    enc_key, mac_key = _derive_keys(passphrase, salt)
    expected = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise ConfigError("Wrong passphrase or corrupted bundle (MAC mismatch)")
    return _xor(ciphertext, _keystream(enc_key, nonce, len(ciphertext)))


def export_accounts(
    config: Config,
    path: Path,
    passphrase: str | None = None,
) -> Path:
    """Write the account configuration to `path`; returns the path written."""
    payload: dict[str, Any] = {
        "magic": BUNDLE_MAGIC,
        "version": BUNDLE_VERSION,
        "exported_at": int(time.time()),
        "default_account": config.default_account,
        "accounts": [asdict(a) for a in config.accounts],
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    bundle = (
        _encrypt(raw, passphrase)
        if passphrase
        else {
            **payload,
            "encrypted": False,
        }
    )

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    tmp.replace(target)
    return target


def read_bundle(path: Path, passphrase: str | None = None) -> dict[str, Any]:
    """Read a bundle (decrypting if needed); returns the inner payload."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ConfigError(f"Bundle not found: {source}")
    try:
        bundle = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid bundle JSON: {exc}") from exc
    if bundle.get("magic") != BUNDLE_MAGIC:
        raise ConfigError(f"Not a {BUNDLE_MAGIC} file: {source}")
    if bundle.get("encrypted"):
        if not passphrase:
            raise ConfigError("Bundle is encrypted; a passphrase is required")
        payload: dict[str, Any] = json.loads(_decrypt(bundle, passphrase).decode("utf-8"))
        return payload
    payload = dict(bundle)
    return payload


def import_accounts(
    path: Path,
    passphrase: str | None = None,
    merge: bool = True,
    config: Config | None = None,
) -> tuple[Config, int]:
    """Import accounts from a bundle into a config.

    With `merge=True` accounts with the same name are replaced; otherwise the
    existing list is cleared first. Pass `config` to target a specific Config
    (defaults to the user's config). Returns (updated Config, accounts imported).
    """
    from fengtang.core.config import load_config, save_config

    payload = read_bundle(path, passphrase)
    incoming = [Account(**item) for item in payload.get("accounts", [])]

    if config is None:
        config = load_config()
    if merge:
        names = {a.name for a in incoming}
        config.accounts = [a for a in config.accounts if a.name not in names] + incoming
    else:
        config.accounts = list(incoming)
    if payload.get("default_account"):
        config.default_account = payload["default_account"]
    elif not config.default_account and config.accounts:
        config.default_account = config.accounts[0].name

    save_config(config)
    return config, len(incoming)
