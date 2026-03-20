"""Tests for logic.py — run command, review command, source parsing, provider resolution."""

from datetime import date
from unittest.mock import patch, MagicMock

import pytest
import typer
from social_reader import logic
from social_reader.fetcher import SocialPost


def test_parse_sources_valid():
    """Valid sources parsed correctly."""
    assert logic._parse_sources("bluesky,mastodon") == ["bluesky", "mastodon"]
    assert logic._parse_sources("bluesky") == ["bluesky"]


def test_parse_sources_invalid():
    """Typer.Exit raised for unknown sources."""
    with pytest.raises(typer.Exit):
        logic._parse_sources("invalid")


def test_get_provider_valid():
    """Correct provider instantiated."""
    with patch("social_reader.logic.PROVIDERS", {"mock": MagicMock()}):
        logic._get_provider("mock", "model123")
        logic.PROVIDERS["mock"].assert_called_once_with(model="model123")


def test_get_provider_invalid():
    """Typer.Exit raised for unknown provider."""
    with patch("social_reader.logic.PROVIDERS", {"mock": MagicMock()}):
        with pytest.raises(typer.Exit):
            logic._get_provider("other", None)


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


@patch("social_reader.logic._get_provider")
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
