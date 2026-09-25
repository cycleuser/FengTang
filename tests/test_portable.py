"""Tests for encrypted account export/import bundles."""

from __future__ import annotations

from pathlib import Path

import pytest

from fengtang.core.config import Config, add_account, load_config, save_config
from fengtang.core.errors import ConfigError
from fengtang.core.portable import export_accounts, import_accounts, read_bundle


def _cfg(tmp_path: Path) -> Config:
    config = Config(data_dir=str(tmp_path))
    add_account(config, "work", "work@example.com", password="secret", provider="gmail")
    add_account(config, "home", "home@example.com", password="pw2", smtp_host="smtp.example.com")
    config.default_account = "work"
    save_config(config)
    return config


class TestPortable:
    def test_plaintext_roundtrip(self, tmp_path: Path) -> None:
        config = _cfg(tmp_path)
        bundle = export_accounts(config, tmp_path / "b.fgbundle", passphrase=None)
        payload = read_bundle(bundle)
        assert payload["encrypted"] is False
        assert {a["name"] for a in payload["accounts"]} == {"work", "home"}
        assert payload["default_account"] == "work"

    def test_encrypted_roundtrip(self, tmp_path: Path) -> None:
        config = _cfg(tmp_path)
        bundle = export_accounts(config, tmp_path / "b.fgbundle", passphrase="hunter2")
        payload = read_bundle(bundle, "hunter2")
        assert {a["name"] for a in payload["accounts"]} == {"work", "home"}
        assert payload["accounts"][0]["password"] in ("secret", "pw2")

    def test_encrypted_needs_passphrase(self, tmp_path: Path) -> None:
        config = _cfg(tmp_path)
        bundle = export_accounts(config, tmp_path / "b.fgbundle", passphrase="x")
        with pytest.raises(ConfigError):
            read_bundle(bundle)

    def test_wrong_passphrase_rejected(self, tmp_path: Path) -> None:
        config = _cfg(tmp_path)
        bundle = export_accounts(config, tmp_path / "b.fgbundle", passphrase="right")
        with pytest.raises(ConfigError):
            read_bundle(bundle, "wrong")

    def test_import_merges_and_replaces(self, tmp_path: Path) -> None:
        config = _cfg(tmp_path)
        bundle = export_accounts(config, tmp_path / "b.fgbundle", passphrase="p")
        # Wipe local config, then import back.
        empty = Config(data_dir=str(tmp_path))
        save_config(empty)
        imported, count = import_accounts(bundle, "p", config=empty)
        assert count == 2
        assert {a.name for a in imported.accounts} == {"work", "home"}
        assert imported.default_account == "work"

    def test_import_merge_keeps_others(self, tmp_path: Path) -> None:
        config = _cfg(tmp_path)
        bundle = export_accounts(config, tmp_path / "b.fgbundle", passphrase="p")
        # Add a third local account, then re-import: work/home replaced, third kept.
        local = load_config(Config(data_dir=str(tmp_path)).config_path())
        add_account(local, "local", "local@example.com")
        save_config(local, local.config_path())
        imported, count = import_accounts(bundle, "p", merge=True, config=local)
        assert count == 2
        assert {a.name for a in imported.accounts} == {"work", "home", "local"}

    def test_bad_magic_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.fgbundle"
        bad.write_text('{"magic": "nope"}', encoding="utf-8")
        with pytest.raises(ConfigError):
            read_bundle(bad)
