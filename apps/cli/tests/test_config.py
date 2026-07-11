"""Tests for iris_cli.config — config loading, saving, and client kwargs."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

import iris_cli.config as config_mod


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect CONFIG_DIR and CONFIG_PATH to a temp directory for every test."""
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")


class TestLoadConfig:
    """load_config creates defaults, auto-generates session_id, reads existing."""

    def test_creates_defaults_when_no_file(self, tmp_path: Path) -> None:
        cfg = config_mod.load_config()
        assert cfg["base_url"] == "http://localhost:8000"
        assert cfg["token"] is None
        # Config file should have been written
        assert (tmp_path / "config.json").exists()

    def test_auto_generates_session_id(self) -> None:
        cfg = config_mod.load_config()
        # Should be a valid UUID4
        parsed = uuid.UUID(cfg["session_id"])
        assert parsed.version == 4

    def test_reads_existing_config(self, tmp_path: Path) -> None:
        existing = {
            "base_url": "https://api.example.com",
            "session_id": "existing-uuid",
            "token": "tok_abc",
        }
        (tmp_path / "config.json").write_text(json.dumps(existing))

        cfg = config_mod.load_config()
        assert cfg["base_url"] == "https://api.example.com"
        assert cfg["session_id"] == "existing-uuid"
        assert cfg["token"] == "tok_abc"

    def test_existing_config_not_overwritten_when_complete(self, tmp_path: Path) -> None:
        existing = {
            "base_url": "https://prod.example.com",
            "session_id": "keep-this-id",
            "token": "keep-this-token",
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(existing))
        mtime_before = config_path.stat().st_mtime_ns

        cfg = config_mod.load_config()

        # File should not have been rewritten since all keys present
        assert config_path.stat().st_mtime_ns == mtime_before
        assert cfg == existing

    def test_backfills_missing_keys(self, tmp_path: Path) -> None:
        partial: dict[str, Any] = {"base_url": "http://custom:9000"}
        (tmp_path / "config.json").write_text(json.dumps(partial))

        cfg = config_mod.load_config()
        assert cfg["base_url"] == "http://custom:9000"
        # session_id should have been auto-generated
        assert cfg["session_id"] is not None
        uuid.UUID(cfg["session_id"])  # validates format
        assert "token" in cfg


class TestSaveConfig:
    """save_config writes valid JSON."""

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        data = {"base_url": "http://localhost:8000", "session_id": "abc", "token": None}
        config_mod.save_config(data)

        raw = (tmp_path / "config.json").read_text()
        loaded = json.loads(raw)
        assert loaded == data

    def test_creates_parent_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        nested = tmp_path / "deep" / "nested"
        monkeypatch.setattr(config_mod, "CONFIG_DIR", nested)
        monkeypatch.setattr(config_mod, "CONFIG_PATH", nested / "config.json")

        config_mod.save_config({"test": True})
        assert (nested / "config.json").exists()


class TestGetClientKwargs:
    """get_client_kwargs returns the right keys."""

    def test_returns_expected_keys(self) -> None:
        kwargs = config_mod.get_client_kwargs()
        assert set(kwargs.keys()) == {"base_url", "session_id", "token"}

    def test_values_match_config(self, tmp_path: Path) -> None:
        existing = {
            "base_url": "https://api.test.com",
            "session_id": "test-session",
            "token": "tok_test",
        }
        (tmp_path / "config.json").write_text(json.dumps(existing))

        kwargs = config_mod.get_client_kwargs()
        assert kwargs["base_url"] == "https://api.test.com"
        assert kwargs["session_id"] == "test-session"
        assert kwargs["token"] == "tok_test"
