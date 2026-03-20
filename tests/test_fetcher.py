"""Tests for fetcher.py — fetch_bluesky_posts, fetch_mastodon_posts, filter_posts."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from social_reader.fetcher import SocialPost, fetch_bluesky_posts, fetch_mastodon_posts, filter_posts


def _make_post(
    text="Some interesting thoughts about DuckDB and local-first design that definitely exceed the ten word limit.",
    platform="bluesky",
    created_at=None,
    has_external_link=False,
    tags=None,
) -> SocialPost:
    if created_at is None:
        created_at = datetime.now(tz=timezone.utc).isoformat()
    return SocialPost(
        platform=platform,
        author_handle="user.bsky.social",
        author_display_name="Test User",
        text=text,
        post_url="https://bsky.app/profile/user.bsky.social/post/rkey1",
        reply_count=2,
        like_count=10,
        created_at=created_at,
        has_external_link=has_external_link,
        tags=tags or [],
    )


def _bsky_raw_post(text="Hello DuckDB", has_embed=False, tags=None):
    """Build a raw Bluesky API post dict."""
    record = {
        "text": text,
        "createdAt": datetime.now(tz=timezone.utc).isoformat(),
        "facets": [],
    }
    if tags:
        record["facets"] = [
            {"features": [{"$type": "app.bsky.richtext.facet#tag", "tag": t}]}
            for t in tags
        ]
    post = {
        "author": {"handle": "user.bsky.social", "displayName": "Test User"},
        "record": record,
        "replyCount": 1,
        "likeCount": 5,
    }
    if has_embed:
        post["embed"] = {"$type": "app.bsky.embed.external#view"}
    return post


def _mastodon_raw_status(text="<p>Hello DuckDB from Mastodon</p>", has_card=False):
    """Build a raw Mastodon API status dict."""
    return {
        "url": "https://mastodon.social/@user/123",
        "content": text,
        "account": {"acct": "user", "display_name": "Masto User"},
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "replies_count": 0,
        "favourites_count": 3,
        "tags": [{"name": "duckdb"}],
        "card": {"url": "https://example.com"} if has_card else None,
        "_instance": "mastodon.social",
    }


# ── fetch_bluesky_posts ──────────────────────────────────────────────────────

class TestFetchBlueSkyPosts:
    def test_returns_empty_for_no_keywords(self):
        result = fetch_bluesky_posts([])
        assert result == []

    def test_returns_posts_from_api(self):
        raw = [_bsky_raw_post("Interesting thoughts about Python and SQL")]
        with (
            patch("social_reader.fetcher.bluesky.fetch_posts", return_value=raw),
            patch("social_reader.fetcher.bluesky.has_external_link", return_value=False),
            patch("social_reader.fetcher.bluesky.get_post_url", return_value="https://bsky.app/profile/user.bsky.social/post/abc"),
        ):
            result = fetch_bluesky_posts(["python"])
        assert len(result) == 1
        assert result[0].platform == "bluesky"
        assert result[0].text == "Interesting thoughts about Python and SQL"

    def test_skips_link_posts_by_default(self):
        raw = [_bsky_raw_post("Article share")]
        with (
            patch("social_reader.fetcher.bluesky.fetch_posts", return_value=raw),
            patch("social_reader.fetcher.bluesky.has_external_link", return_value=True),
            patch("social_reader.fetcher.bluesky.get_post_url", return_value="https://bsky.app/test"),
        ):
            result = fetch_bluesky_posts(["python"])
        assert result == []

    def test_includes_link_posts_when_flag_set(self):
        raw = [_bsky_raw_post("Article share")]
        with (
            patch("social_reader.fetcher.bluesky.fetch_posts", return_value=raw),
            patch("social_reader.fetcher.bluesky.has_external_link", return_value=True),
            patch("social_reader.fetcher.bluesky.get_post_url", return_value="https://bsky.app/test"),
        ):
            result = fetch_bluesky_posts(["python"], include_link_posts=True)
        assert len(result) == 1

    def test_extracts_hashtag_facets(self):
        raw = [_bsky_raw_post("DuckDB is great", tags=["duckdb", "sql"])]
        with (
            patch("social_reader.fetcher.bluesky.fetch_posts", return_value=raw),
            patch("social_reader.fetcher.bluesky.has_external_link", return_value=False),
            patch("social_reader.fetcher.bluesky.get_post_url", return_value="https://bsky.app/test"),
        ):
            result = fetch_bluesky_posts(["duckdb"])
        assert "duckdb" in result[0].tags
        assert "sql" in result[0].tags

    def test_authenticates_when_credentials_provided(self):
        with (
            patch("social_reader.fetcher.bluesky.get_auth_token", return_value="tok123") as mock_auth,
            patch("social_reader.fetcher.bluesky.fetch_posts", return_value=[]),
        ):
            fetch_bluesky_posts(["python"], handle="me.bsky.social", app_password="secret")
        mock_auth.assert_called_once_with("me.bsky.social", "secret")

    def test_skips_auth_without_credentials(self):
        with (
            patch("social_reader.fetcher.bluesky.get_auth_token") as mock_auth,
            patch("social_reader.fetcher.bluesky.fetch_posts", return_value=[]),
        ):
            fetch_bluesky_posts(["python"])
        mock_auth.assert_not_called()


# ── fetch_mastodon_posts ─────────────────────────────────────────────────────

class TestFetchMastodonPosts:
    def test_returns_empty_for_no_keywords(self):
        result = fetch_mastodon_posts([])
        assert result == []

    def test_returns_posts_from_api(self):
        raw = [_mastodon_raw_status("<p>DuckDB is fast and easy to use</p>")]
        with patch("social_reader.fetcher.mastodon.fetch_posts", return_value=raw):
            result = fetch_mastodon_posts(["duckdb"])
        assert len(result) == 1
        assert result[0].platform == "mastodon"

    def test_strips_html_from_content(self):
        raw = [_mastodon_raw_status("<p>Hello <strong>DuckDB</strong> fans</p>")]
        with patch("social_reader.fetcher.mastodon.fetch_posts", return_value=raw):
            result = fetch_mastodon_posts(["duckdb"])
        assert "<p>" not in result[0].text
        assert "<strong>" not in result[0].text
        assert "DuckDB" in result[0].text

    def test_skips_link_posts_by_default(self):
        raw = [_mastodon_raw_status(has_card=True)]
        with patch("social_reader.fetcher.mastodon.fetch_posts", return_value=raw):
            result = fetch_mastodon_posts(["duckdb"])
        assert result == []

    def test_includes_link_posts_when_flag_set(self):
        raw = [_mastodon_raw_status("<p>A post with a link card</p>", has_card=True)]
        with patch("social_reader.fetcher.mastodon.fetch_posts", return_value=raw):
            result = fetch_mastodon_posts(["duckdb"], include_link_posts=True)
        assert len(result) == 1

    def test_skips_posts_without_url(self):
        raw = [{"url": "", "content": "<p>No URL</p>", "account": {}, "created_at": "", "replies_count": 0, "favourites_count": 0, "tags": [], "card": None, "_instance": "mastodon.social"}]
        with patch("social_reader.fetcher.mastodon.fetch_posts", return_value=raw):
            result = fetch_mastodon_posts(["duckdb"])
        assert result == []

    def test_skips_posts_with_empty_text(self):
        raw = [_mastodon_raw_status("<p></p>")]
        with patch("social_reader.fetcher.mastodon.fetch_posts", return_value=raw):
            result = fetch_mastodon_posts(["duckdb"])
        assert result == []

    def test_author_handle_includes_instance(self):
        raw = [_mastodon_raw_status("<p>DuckDB content for testing handle</p>")]
        raw[0]["account"]["acct"] = "user123"
        raw[0]["_instance"] = "fosstodon.org"
        with patch("social_reader.fetcher.mastodon.fetch_posts", return_value=raw):
            result = fetch_mastodon_posts(["duckdb"])
        assert result[0].author_handle == "user123@fosstodon.org"

    def test_extracts_tags_from_status(self):
        raw = [_mastodon_raw_status("<p>DuckDB post with tags</p>")]
        raw[0]["tags"] = [{"name": "duckdb"}, {"name": "python"}]
        with patch("social_reader.fetcher.mastodon.fetch_posts", return_value=raw):
            result = fetch_mastodon_posts(["duckdb"])
        assert "duckdb" in result[0].tags
        assert "python" in result[0].tags


# ── filter_posts ─────────────────────────────────────────────────────────────

class TestFilterPosts:
    def test_keeps_recent_english_long_post(self):
        post = _make_post("This is a detailed post about DuckDB and Python SQL programming concepts.")
        result = filter_posts([post])
        assert len(result) == 1

    def test_drops_posts_below_min_words(self):
        post = _make_post("Too short")
        result = filter_posts([post], min_words=10)
        assert result == []

    def test_keeps_posts_meeting_min_words(self):
        post = _make_post("One two three four five six seven eight nine ten words here")
        result = filter_posts([post], min_words=10)
        assert len(result) == 1

    def test_drops_posts_older_than_since_hours(self):
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=100)
        post = _make_post(created_at=old_time.isoformat())
        result = filter_posts([post], since_hours=48)
        assert result == []

    def test_keeps_posts_within_since_hours(self):
        recent_time = datetime.now(tz=timezone.utc) - timedelta(hours=10)
        post = _make_post(created_at=recent_time.isoformat())
        result = filter_posts([post], since_hours=48)
        assert len(result) == 1

    def test_keeps_posts_with_bad_timestamp(self):
        post = _make_post(created_at="not-a-date")
        result = filter_posts([post], since_hours=48)
        assert len(result) == 1  # fail open

    def test_no_age_filter_when_since_hours_zero(self):
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=365)
        post = _make_post(created_at=old_time.isoformat())
        result = filter_posts([post], since_hours=0)
        assert len(result) == 1

    def test_z_suffix_timestamp_handled(self):
        recent = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        post = _make_post(created_at=recent)
        result = filter_posts([post], since_hours=48)
        assert len(result) == 1

    def test_english_only_drops_non_english(self):
        post = _make_post("Привет мир это тест на русском языке для DuckDB системы данных")
        with patch("social_reader.fetcher.is_english", return_value=False):
            result = filter_posts([post], english_only=True)
        assert result == []

    def test_english_only_false_keeps_all(self):
        post = _make_post("Texte en français sur DuckDB et Python programmation avancée pour tous les développeurs")
        result = filter_posts([post], english_only=False)
        assert len(result) == 1
