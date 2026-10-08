"""Tests for config.py — loading TOML, environment variable overrides, DB path resolution."""

import os
from pathlib import Path
from unittest.mock import patch

from social_reader import config


def test_resolve_db_path_env_var():
    """DB path resolved from environment variable."""
    with patch.dict(os.environ, {"SOCIAL_POST_READER_STORE": "/tmp/test.db"}):
        assert config._resolve_db_path() == "/tmp/test.db"


def test_resolve_db_path_settings():
    """DB path resolved from settings (TOML)."""
    with patch.dict(os.environ, {}, clear=True), patch("social_reader.config._settings", {"store": "~/my.db"}):
        expected = os.path.expanduser("~/my.db")
        assert config._resolve_db_path() == expected


def test_resolve_db_path_sync_dir():
    """DB path resolved from sync directory if it exists."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("social_reader.config._settings", {}),
        patch("pathlib.Path.home", return_value=Path("/mock/home")),
        patch("pathlib.Path.exists", return_value=True),
    ):
        res = config._resolve_db_path()
        assert "social-post-reader.db" in res
        assert "sync/social-reader" in res.replace("\\", "/")


def test_resolve_db_path_fallback():
    """DB path fallback to shared .local-first directory."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("social_reader.config._settings", {}),
        patch("pathlib.Path.exists", return_value=False),
    ):
        res = config._resolve_db_path()
        assert ".local-first" in res.replace("\\", "/")
        assert "local-first.db" in res


def test_config_comes_from_the_fleet_config_dir(tmp_path, monkeypatch):
    """Settings are read from ~/.config/local-first/social-post-reader.toml (kept in
    personal-infra); a missing file leaves the built-in defaults in place."""
    import importlib

    from local_first_common import config as lfc_config

    monkeypatch.setattr(lfc_config, "CONFIG_DIR", tmp_path)
    try:
        importlib.reload(config)
        assert "python" in config.KEYWORDS  # defaults
        (tmp_path / "social-post-reader.toml").write_text(
            '[social]\nkeywords = ["test"]\n[profile]\ndescription = "me"\n'
        )
        importlib.reload(config)
        assert config.KEYWORDS == ["test"] and config.PROFILE == "me"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
