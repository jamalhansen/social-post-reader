"""Tests for logic.py — run command, review command, source parsing, provider resolution."""

from datetime import date
from unittest.mock import patch, MagicMock

import pytest
import typer
from social_reader import logic
from social_reader.fetcher import SocialPost
from social_reader.logic import SocialReaderError, ProviderSetupError


class TestTypedErrors:
    def test_error_hierarchy(self):
        assert issubclass(ProviderSetupError, SocialReaderError)

    def test_provider_setup_error_message(self):
        err = ProviderSetupError("bad provider")
        assert "bad provider" in str(err)


def test_parse_sources_valid():
    """Valid sources parsed correctly."""
    assert logic._parse_sources("bluesky,mastodon") == ["bluesky", "mastodon"]
    assert logic._parse_sources("bluesky") == ["bluesky"]


def test_parse_sources_invalid():
    """Typer.Exit raised for unknown sources."""
    with pytest.raises(typer.Exit):
        logic._parse_sources("invalid")


@patch("social_reader.logic.fetch_bluesky_posts")
@patch("social_reader.logic.fetch_mastodon_posts")
def test_fetch_all_posts(mock_masto, mock_bsky):
    """Posts fetched from specified sources."""
    mock_bsky.return_value = [MagicMock(spec=SocialPost)]
    mock_masto.return_value = [MagicMock(spec=SocialPost)]

    res = logic._fetch_all_posts(["bluesky", "mastodon"])
    assert len(res) == 2
    mock_bsky.assert_called_once()
    mock_masto.assert_called_once()


@patch("social_reader.logic.resolve_provider", return_value=MagicMock())
@patch("social_reader.logic._fetch_all_posts")
@patch("social_reader.logic.score_posts")
@patch("social_reader.logic.format_digest")
@patch("social_reader.logic.db_store.init_db")
def test_run_command_dry_run(mock_init, mock_format, mock_score, mock_fetch, mock_prov):
    """Dry run prints digest without writing."""
    mock_fetch.return_value = [SocialPost(platform="bluesky", author_handle="u", author_display_name="U", text="Some long post text here", post_url="url", reply_count=0, like_count=0, created_at=date.today().isoformat())]
    mock_score.return_value = []
    mock_format.return_value = "DIGEST_CONTENT"

    # Run the command
    logic.run(dry_run=True, no_obsidian=True)

    mock_fetch.assert_called_once()
    mock_score.assert_called_once()
    mock_format.assert_called_once()


@patch("social_reader.logic.db_store.init_db")
@patch("social_reader.logic.db_store.get_new_candidates")
@patch("typer.prompt")
def test_review_command_empty(mock_prompt, mock_get, mock_init):
    """Review command handles no candidates."""
    mock_get.return_value = []
    with pytest.raises(typer.Exit):
        logic.review()
    mock_prompt.assert_not_called()


@patch("social_reader.logic.db_store.init_db")
@patch("social_reader.logic.db_store.get_status_summary")
def test_status_command(mock_get, mock_init):
    """Status command shows summary."""
    mock_get.return_value = {"new": 5, "replied": 2}
    logic.status()
    mock_get.assert_called_once()


@patch("social_reader.logic.db_store.init_db")
@patch("social_reader.logic.db_store.clear_new_candidates")
def test_clear_command(mock_clear, mock_init):
    """Clear command calls clear_new_candidates."""
    from typer.testing import CliRunner
    runner = CliRunner()
    
    mock_clear.return_value = 10
    
    # Use force to skip confirmation
    result = runner.invoke(logic.app, ["clear", "--force"])
    
    assert result.exit_code == 0
    assert "Done. Cleared 10 candidates." in result.output
    mock_clear.assert_called_once_with(logic.config.STORE_PATH, None)
