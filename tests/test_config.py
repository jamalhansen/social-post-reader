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
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("social_reader.config._settings", {"store": "~/my.db"})
    ):
        expected = os.path.expanduser("~/my.db")
        assert config._resolve_db_path() == expected


def test_resolve_db_path_sync_dir():
    """DB path resolved from sync directory if it exists."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("social_reader.config._settings", {}),
        patch("pathlib.Path.home", return_value=Path("/mock/home")),
        patch("pathlib.Path.exists", return_value=True)
    ):
        res = config._resolve_db_path()
        assert "social-post-reader.db" in res
        assert "sync/social-reader" in res.replace("\\", "/")


def test_resolve_db_path_fallback():
    """DB path fallback to shared .local-first directory."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("social_reader.config._settings", {}),
        patch("pathlib.Path.exists", return_value=False)
    ):
        res = config._resolve_db_path()
        assert ".local-first" in res.replace("\\", "/")
        assert "local-first.db" in res


def test_load_toml_not_found():
    """Empty dict returned if config file doesn't exist."""
    with patch("pathlib.Path.exists", return_value=False):
        assert config._load_toml() == {}


def test_load_toml_success():
    """TOML loaded if config file exists."""
    mock_content = b'[social]\nkeywords=["test"]'
    import io
    buf = io.BytesIO(mock_content)
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("builtins.open", return_value=buf)
    ):
        res = config._load_toml()
        assert res["social"]["keywords"] == ["test"]
