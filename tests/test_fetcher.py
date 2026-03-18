"""Tests for fetcher.py — Bluesky and Mastodon post fetchers."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from datetime import datetime, timedelta, timezone

from fetcher import (
    SocialPost,
    _bsky_has_external_link,
    _bsky_post_url,
    _strip_html,
    fetch_bluesky_posts,
    fetch_mastodon_posts,
    filter_posts,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestStripHtml:
    def test_removes_paragraph_tags(self):
        assert _strip_html("<p>Hello world</p>") == "Hello world"

    def test_removes_anchor_tags(self):
        assert _strip_html('<a href="http://x.com">link</a>') == "link"

    def test_decodes_entities(self):
        assert _strip_html("&amp; &lt; &gt; &quot;") == "& < > \""

    def test_collapses_whitespace(self):
        assert _strip_html("<p>one  two</p>   <p>three</p>") == "one two three"

    def test_empty_string(self):
        assert _strip_html("") == ""


class TestBskyHelpers:
    def test_has_external_link_true(self):
        post = {"embed": {"external": {"uri": "https://example.com"}}}
        assert _bsky_has_external_link(post) is True

    def test_has_external_link_false_no_embed(self):
        assert _bsky_has_external_link({}) is False

    def test_has_external_link_false_no_uri(self):
        post = {"embed": {"external": {}}}
        assert _bsky_has_external_link(post) is False

    def test_post_url_builds_correctly(self):
        post = {
            "author": {"handle": "user.bsky.social"},
            "uri": "at://did:plc:abc/app.bsky.feed.post/rkey123",
        }
        url = _bsky_post_url(post)
        assert url == "https://bsky.app/profile/user.bsky.social/post/rkey123"

    def test_post_url_empty_on_missing_data(self):
        assert _bsky_post_url({}) == ""


class TestFetchBlueskyPosts:
    def _mock_response(self, data: dict):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        return resp

    def test_returns_text_posts(self):
        data = json.loads((FIXTURES / "sample_bluesky_posts.json").read_text())
        with patch("fetcher.requests.get", return_value=self._mock_response(data)):
            posts = fetch_bluesky_posts(["localai"])
        # 2 text posts; 1 link post filtered out
        assert len(posts) == 2
        assert all(isinstance(p, SocialPost) for p in posts)
        assert all(p.platform == "bluesky" for p in posts)

    def test_includes_link_posts_when_requested(self):
        data = json.loads((FIXTURES / "sample_bluesky_posts.json").read_text())
        with patch("fetcher.requests.get", return_value=self._mock_response(data)):
            posts = fetch_bluesky_posts(["localai"], include_link_posts=True)
        assert len(posts) == 3

    def test_deduplicates_across_keywords(self):
        data = json.loads((FIXTURES / "sample_bluesky_posts.json").read_text())
        mock = self._mock_response(data)
        with patch("fetcher.requests.get", return_value=mock):
            posts = fetch_bluesky_posts(["localai", "duckdb"])
        # Same fixture returned for both keywords — deduplicated to 2 text posts
        assert len(posts) == 2

    def test_skips_on_network_error(self):
        import requests as req
        with patch("fetcher.requests.get", side_effect=req.RequestException("timeout")):
            posts = fetch_bluesky_posts(["localai"])
        assert posts == []

    def test_empty_keywords_returns_empty(self):
        posts = fetch_bluesky_posts([])
        assert posts == []

    def test_extracts_tags_from_facets(self):
        data = json.loads((FIXTURES / "sample_bluesky_posts.json").read_text())
        with patch("fetcher.requests.get", return_value=self._mock_response(data)):
            posts = fetch_bluesky_posts(["localai"])
        # First post has a #localai tag facet
        assert "localai" in posts[0].tags

    def test_authenticates_when_credentials_provided(self):
        token_resp = MagicMock()
        token_resp.json.return_value = {"accessJwt": "test-token"}
        token_resp.raise_for_status.return_value = None

        data = json.loads((FIXTURES / "sample_bluesky_posts.json").read_text())
        search_resp = self._mock_response(data)

        with patch("fetcher.requests.post", return_value=token_resp):
            with patch("fetcher.requests.get", return_value=search_resp) as mock_get:
                fetch_bluesky_posts(["python"], handle="user.bsky.social", app_password="secret")

        call_kwargs = mock_get.call_args
        headers_passed = call_kwargs.kwargs.get("headers", {})
        assert "Authorization" in headers_passed


class TestFetchMastodonPosts:
    def _mock_response(self, data):
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        return resp

    def test_returns_text_posts(self):
        data = json.loads((FIXTURES / "sample_mastodon_posts.json").read_text())
        with patch("fetcher.requests.get", return_value=self._mock_response(data)):
            posts = fetch_mastodon_posts(["sql"])
        # 2 text posts; 1 link post filtered out
        assert len(posts) == 2
        assert all(p.platform == "mastodon" for p in posts)

    def test_includes_link_posts_when_requested(self):
        data = json.loads((FIXTURES / "sample_mastodon_posts.json").read_text())
        with patch("fetcher.requests.get", return_value=self._mock_response(data)):
            posts = fetch_mastodon_posts(["sql"], include_link_posts=True)
        assert len(posts) == 3

    def test_strips_html_from_content(self):
        data = json.loads((FIXTURES / "sample_mastodon_posts.json").read_text())
        with patch("fetcher.requests.get", return_value=self._mock_response(data)):
            posts = fetch_mastodon_posts(["sql"])
        for p in posts:
            assert "<" not in p.text

    def test_deduplicates_across_instances(self):
        data = json.loads((FIXTURES / "sample_mastodon_posts.json").read_text())
        mock = self._mock_response(data)
        with patch("fetcher.requests.get", return_value=mock):
            posts = fetch_mastodon_posts(["sql"], instances=["mastodon.social", "fosstodon.org"])
        assert len(posts) == 2

    def test_skips_on_network_error(self):
        import requests as req
        with patch("fetcher.requests.get", side_effect=req.RequestException("timeout")):
            posts = fetch_mastodon_posts(["sql"])
        assert posts == []

    def test_empty_keywords_returns_empty(self):
        posts = fetch_mastodon_posts([])
        assert posts == []

    def test_keyword_to_hashtag_strips_spaces(self):
        from fetcher import _keyword_to_hashtag
        assert _keyword_to_hashtag("local ai") == "localai"
        assert _keyword_to_hashtag("duckdb") == "duckdb"
        assert _keyword_to_hashtag("data-engineering") == "dataengineering"


_ENGLISH_TEXT = (
    "Building local-first AI tools with Python and DuckDB on your own hardware "
    "gives you privacy, control, and a great learning experience."
)


def _make_post(handle="user.bsky.social", text=_ENGLISH_TEXT, created_at=None) -> SocialPost:
    if created_at is None:
        created_at = datetime.now(tz=timezone.utc).isoformat()
    return SocialPost(
        platform="bluesky",
        author_handle=handle,
        author_display_name="User",
        text=text.strip(),
        post_url=f"https://bsky.app/profile/{handle}/post/rkey",
        reply_count=3,
        like_count=10,
        created_at=created_at,
    )


class TestFilterPosts:
    def test_keeps_recent_posts(self):
        post = _make_post()
        result = filter_posts([post], since_hours=48)
        assert result == [post]

    def test_drops_old_posts(self):
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=72)).isoformat()
        post = _make_post(created_at=old_ts)
        result = filter_posts([post], since_hours=48)
        assert result == []

    def test_no_age_filter_when_zero(self):
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=200)).isoformat()
        post = _make_post(created_at=old_ts)
        result = filter_posts([post], since_hours=0)
        assert result == [post]

    def test_drops_short_posts(self):
        post = _make_post(text="too short")
        result = filter_posts([post], min_words=10, english_only=False)
        assert result == []

    def test_keeps_posts_at_min_word_threshold(self):
        text = "This is a sample post with exactly ten words here."
        post = _make_post(text=text)
        result = filter_posts([post], min_words=10)
        assert result == [post]

    def test_keeps_unparseable_timestamp(self):
        post = _make_post(created_at="not-a-date")
        result = filter_posts([post], since_hours=1)
        assert result == [post]

    def test_handles_z_suffix_timestamp(self):
        ts = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        post = _make_post(created_at=ts)
        result = filter_posts([post], since_hours=48)
        assert result == [post]

    def test_drops_non_english_when_english_only(self):
        # German — long enough for langdetect to classify reliably, no spaces issue
        german_text = (
            "Dies ist ein Beitrag über den Aufbau von lokalen KI-Werkzeugen mit Python "
            "und DuckDB auf eigener Hardware für Datenschutz und Kontrolle."
        )
        post = _make_post(text=german_text)
        result = filter_posts([post], english_only=True, min_words=0)
        assert result == []

    def test_keeps_english_when_english_only(self):
        post = _make_post()  # default is English text
        result = filter_posts([post], english_only=True)
        assert result == [post]

    def test_keeps_non_english_when_english_only_false(self):
        german_text = (
            "Dies ist ein Beitrag über den Aufbau von lokalen KI-Werkzeugen mit Python "
            "und DuckDB auf eigener Hardware für Datenschutz und Kontrolle."
        )
        post = _make_post(text=german_text)
        result = filter_posts([post], english_only=False, min_words=0)
        assert result == [post]

    def test_english_only_calls_is_english_with_post_text(self):
        post = _make_post(text="word " * 15)
        with patch("fetcher.is_english", return_value=True) as mock_detect:
            filter_posts([post], english_only=True)
        mock_detect.assert_called_once_with(post.text)

    def test_english_only_skips_is_english_when_false(self):
        post = _make_post(text="word " * 15)
        with patch("fetcher.is_english") as mock_detect:
            filter_posts([post], english_only=False)
        mock_detect.assert_not_called()

    def test_english_only_default_is_true(self):
        import inspect
        sig = inspect.signature(filter_posts)
        assert sig.parameters["english_only"].default is True
