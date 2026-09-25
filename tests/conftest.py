"""Shared fixtures for MailPilot tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """Isolated MAILPILOT_DATA_DIR per test."""
    target = tmp_path / "mailpilot_data"
    target.mkdir()
    old = os.environ.get("MAILPILOT_DATA_DIR")
    os.environ["MAILPILOT_DATA_DIR"] = str(target)
    yield target
    if old:
        os.environ["MAILPILOT_DATA_DIR"] = old
    else:
        os.environ.pop("MAILPILOT_DATA_DIR", None)


@pytest.fixture()
def server_env(tmp_path: Path):
    """Start the built-in mail server on free ports; yields (smtp_port, pop_port)."""
    import socket

    from mailpilot.serve.server import MailServer

    def free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    smtp_port, pop_port = free_port(), free_port()
    db_path = tmp_path / "server.db"
    server = MailServer(
        db_path,
        smtp_host="127.0.0.1",
        smtp_port=smtp_port,
        pop_host="127.0.0.1",
        pop_port=pop_port,
        domain="example.test",
        users={"alice@example.test": "alicepw", "bob@example.test": "bobpw"},
    )

    import threading

    def runner() -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.start())
        loop.create_task(server._smtp_server.serve_forever())
        loop.create_task(server._pop_server.serve_forever())
        loop.run_forever()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    import time

    deadline = time.time() + 10
    ready = False
    while time.time() < deadline and not ready:
        ready = True
        for port in (smtp_port, pop_port):
            with socket.socket() as sock:
                sock.settimeout(0.3)
                if sock.connect_ex(("127.0.0.1", port)) != 0:
                    ready = False
        if not ready:
            time.sleep(0.05)
    yield {"smtp_port": smtp_port, "pop_port": pop_port, "server": server, "db_path": db_path}
    server.stop()
    time.sleep(0.1)
