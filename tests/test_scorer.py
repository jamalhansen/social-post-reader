"""Tests for scorer.py — post scoring and digest formatting."""

import pytest

from fetcher import SocialPost
from scorer import ScoredPost, _parse_response, format_digest, score_post, score_posts


def _make_post(
    text="The context window is the new RAM.",
    handle="simonw.bsky.social",
    platform="bluesky",
    reply_count=5,
) -> SocialPost:
    return SocialPost(
        platform=platform,
        author_handle=handle,
        author_display_name="Simon Willison",
        text=text,
        post_url=f"https://bsky.app/profile/{handle}/post/rkey",
        reply_count=reply_count,
        like_count=42,
        created_at="2026-03-17T08:00:00.000Z",
    )


class MockProvider:
    """Returns a canned JSON response for scoring."""

    def __init__(self, score=0.8, angle="Mention local-first-common's provider abstraction."):
        self._score = score
        self._angle = angle

    def complete(self, system: str, user: str) -> str:
        return f'{{"score": {self._score}, "angle": "{self._angle}"}}'


class TestParseResponse:
    def test_plain_json(self):
        result = _parse_response('{"score": 0.7, "angle": "test"}')
        assert result == {"score": 0.7, "angle": "test"}

    def test_strips_json_code_fence(self):
        raw = '```json\n{"score": 0.6, "angle": "hello"}\n```'
        result = _parse_response(raw)
        assert result["score"] == 0.6

    def test_strips_plain_code_fence(self):
        raw = '```\n{"score": 0.5, "angle": "world"}\n```'
        result = _parse_response(raw)
        assert result["score"] == 0.5

    def test_raises_on_invalid_json(self):
        with pytest.raises(Exception):
            _parse_response("not json")


class TestScorePost:
    def test_returns_scored_post(self):
        post = _make_post()
        provider = MockProvider(score=0.75, angle="Bring up provider abstraction.")
        result = score_post(post, "I write about local AI.", provider)
        assert isinstance(result, ScoredPost)
        assert result.score == pytest.approx(0.75)
        assert "provider" in result.angle

    def test_clears_angle_below_threshold(self):
        post = _make_post()
        provider = MockProvider(score=0.3, angle="some angle")
        result = score_post(post, "profile", provider)
        assert result.angle == ""

    def test_clamps_score_to_range(self):
        post = _make_post()

        class HighProvider:
            def complete(self, s, u):
                return '{"score": 1.5, "angle": "x"}'

        result = score_post(post, "profile", HighProvider())
        assert result.score == 1.0

    def test_returns_zero_on_provider_error(self):
        post = _make_post()

        class BrokenProvider:
            def complete(self, s, u):
                raise RuntimeError("API down")

        result = score_post(post, "profile", BrokenProvider())
        assert result.score == 0.0
        assert result.angle == ""

    def test_truncates_long_post_text(self):
        """Provider receives at most 1000 chars of post text."""
        long_text = "x" * 2000
        post = _make_post(text=long_text)
        received_texts = []

        class CapturingProvider:
            def complete(self, system, user):
                received_texts.append(user)
                return '{"score": 0.5, "angle": ""}'

        score_post(post, "profile", CapturingProvider())
        assert len(received_texts[0]) < 1500  # well under 2000


class TestScorePosts:
    def test_filters_below_threshold(self):
        posts = [_make_post(handle=f"user{i}.bsky.social") for i in range(3)]
        scores = [0.3, 0.6, 0.8]

        class SequentialProvider:
            def __init__(self):
                self._idx = 0

            def complete(self, s, u):
                score = scores[self._idx % len(scores)]
                self._idx += 1
                return f'{{"score": {score}, "angle": "angle {score}"}}'

        results = score_posts(posts, "profile", SequentialProvider(), threshold=0.5)
        assert len(results) == 2
        assert all(r.score >= 0.5 for r in results)

    def test_sorts_by_score_descending(self):
        posts = [_make_post(handle=f"u{i}.bsky.social") for i in range(2)]
        scores = [0.6, 0.9]

        class SequentialProvider:
            def __init__(self):
                self._idx = 0

            def complete(self, s, u):
                score = scores[self._idx % len(scores)]
                self._idx += 1
                return f'{{"score": {score}, "angle": ""}}'

        results = score_posts(posts, "profile", SequentialProvider(), threshold=0.5)
        assert results[0].score >= results[1].score

    def test_empty_input_returns_empty(self):
        results = score_posts([], "profile", MockProvider())
        assert results == []


class TestFormatDigest:
    def _make_scored(self, score=0.8, angle="Mention local-first-common.") -> ScoredPost:
        return ScoredPost(post=_make_post(), score=score, angle=angle)

    def test_includes_header(self):
        digest = format_digest([self._make_scored()], "2026-03-17")
        assert "## Reply Candidates — 2026-03-17" in digest

    def test_includes_author_handle(self):
        digest = format_digest([self._make_scored()], "2026-03-17")
        assert "@simonw.bsky.social" in digest

    def test_includes_angle(self):
        digest = format_digest([self._make_scored(angle="Bring up DuckDB.")], "2026-03-17")
        assert "Bring up DuckDB." in digest

    def test_no_angle_when_empty(self):
        scored = ScoredPost(post=_make_post(), score=0.7, angle="")
        digest = format_digest([scored], "2026-03-17")
        assert "Your angle" not in digest

    def test_respects_max_posts(self):
        candidates = [self._make_scored() for _ in range(10)]
        digest = format_digest(candidates, "2026-03-17", max_posts=3)
        assert digest.count("@simonw.bsky.social") == 3

    def test_empty_list_produces_no_candidates_message(self):
        digest = format_digest([], "2026-03-17")
        assert "No candidates" in digest

    def test_truncates_long_text(self):
        long_text = "word " * 50
        scored = ScoredPost(post=_make_post(text=long_text), score=0.8, angle="angle")
        digest = format_digest([scored], "2026-03-17")
        assert "…" in digest
