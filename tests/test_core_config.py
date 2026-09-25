"""Tests for config management."""

from __future__ import annotations

from pathlib import Path

import pytest

from fengtang.core.config import (
    Account,
    Config,
    add_account,
    load_config,
    save_config,
)
from fengtang.core.errors import ConfigError


class TestAccount:
    def test_preset_gmail(self) -> None:
        acct = Account(name="work", email="me@gmail.com")
        acct.apply_preset("gmail")
        assert acct.imap_host == "imap.gmail.com"
        assert acct.smtp_port == 465
        assert acct.pop_host == "pop.gmail.com"
        assert acct.auth == "xoauth2"

    def test_preset_unknown_raises(self) -> None:
        acct = Account(name="x", email="a@b.c")
        with pytest.raises(ConfigError):
            acct.apply_preset("nonexistent-provider")

    def test_validate_rejects_bad_email(self) -> None:
        with pytest.raises(ConfigError):
            Account(name="x", email="not-an-email").validate()

    def test_validate_rejects_bad_auth(self) -> None:
        with pytest.raises(ConfigError):
            Account(name="x", email="a@b.c", auth="magic").validate()


class TestConfigPersistence:
    def test_round_trip(self, tmp_path: Path) -> None:
        config = Config(data_dir=str(tmp_path))
        add_account(config, "main", "a@example.com", password="pw", provider="gmail")
        path = save_config(config)
        loaded = load_config(path)
        assert loaded.accounts[0].imap_host == "imap.gmail.com"
        assert loaded.accounts[0].password == "pw"
        assert loaded.default_account == "main"

    def test_load_missing_file_gives_empty(self, tmp_path: Path) -> None:
        loaded = load_config(tmp_path / "nope.json")
        assert loaded.accounts == []

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(bad)

    def test_permissions_restricted(self, tmp_path: Path) -> None:
        import os

        config = Config(data_dir=str(tmp_path))
        path = save_config(config)
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600 or os.name != "posix"

    def test_get_account_default(self, tmp_path: Path) -> None:
        config = Config(data_dir=str(tmp_path))
        add_account(config, name="one", email="one@x.com")
        add_account(config, name="two", email="two@x.com")
        assert config.get_account("two").email == "two@x.com"
        config.default_account = "one"
        assert config.get_account().name == "one"

    def test_get_account_ambiguous(self, tmp_path: Path) -> None:
        config = Config(data_dir=str(tmp_path))
        add_account(config, name="one", email="one@x.com")
        add_account(config, name="two", email="two@x.com")
        config.default_account = ""
        with pytest.raises(ConfigError):
            config.get_account()
