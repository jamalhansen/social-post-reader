"""Tests for store.py — SQLite candidate tracking."""

from social_reader.fetcher import SocialPost
from social_reader.scorer import ScoredPost
from social_reader.store import (
    clear_new_candidates,
    get_new_candidates,
    get_status_summary,
    init_db,
    is_seen,
    mark_candidate,
    upsert_candidate,
)


def _make_scored(
    post_url="https://bsky.app/profile/user.bsky.social/post/rkey1",
    score=0.75,
    angle="Bring up local-first-common.",
) -> ScoredPost:
    post = SocialPost(
        platform="bluesky",
        author_handle="user.bsky.social",
        author_display_name="User",
        text="Local LLMs are changing everything.",
        post_url=post_url,
        reply_count=5,
        like_count=30,
        created_at="2026-03-17T08:00:00.000Z",
    )
    return ScoredPost(post=post, score=score, angle=angle)


class TestInitDb:
    def test_creates_table(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        import sqlite3
        with sqlite3.connect(db) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "candidates" in tables

    def test_idempotent(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        init_db(db)  # should not raise


class TestUpsertAndIsSeen:
    def test_upsert_and_is_seen(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        scored = _make_scored()
        upsert_candidate(scored, "2026-03-17", db)
        assert is_seen(scored.post.post_url, db) is True

    def test_is_seen_returns_false_for_unknown(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        assert is_seen("https://unknown.url", db) is False

    def test_upsert_ignores_duplicate(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        scored = _make_scored()
        upsert_candidate(scored, "2026-03-17", db)
        upsert_candidate(scored, "2026-03-17", db)  # should not raise or duplicate
        candidates = get_new_candidates("2026-03-17", db)
        assert len(candidates) == 1


class TestGetNewCandidates:
    def test_returns_new_for_date(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        upsert_candidate(_make_scored(post_url="https://url1"), "2026-03-17", db)
        upsert_candidate(_make_scored(post_url="https://url2"), "2026-03-17", db)
        candidates = get_new_candidates("2026-03-17", db)
        assert len(candidates) == 2

    def test_does_not_return_other_dates(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        upsert_candidate(_make_scored(), "2026-03-16", db)
        candidates = get_new_candidates("2026-03-17", db)
        assert candidates == []

    def test_sorted_by_score_desc(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        upsert_candidate(_make_scored(post_url="https://low", score=0.5), "2026-03-17", db)
        upsert_candidate(_make_scored(post_url="https://high", score=0.9), "2026-03-17", db)
        candidates = get_new_candidates("2026-03-17", db)
        assert candidates[0]["score"] >= candidates[1]["score"]


class TestMarkCandidate:
    def test_mark_replied(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        scored = _make_scored()
        upsert_candidate(scored, "2026-03-17", db)
        mark_candidate(scored.post.post_url, "replied", db)
        candidates = get_new_candidates("2026-03-17", db)
        assert candidates == []  # no longer 'new'

    def test_mark_skipped(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        scored = _make_scored()
        upsert_candidate(scored, "2026-03-17", db)
        mark_candidate(scored.post.post_url, "skipped", db)
        candidates = get_new_candidates("2026-03-17", db)
        assert candidates == []


class TestGetStatusSummary:
    def test_counts_by_status(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        upsert_candidate(_make_scored(post_url="https://u1"), "2026-03-17", db)
        upsert_candidate(_make_scored(post_url="https://u2"), "2026-03-17", db)
        mark_candidate("https://u1", "replied", db)
        summary = get_status_summary(db)
        assert summary.get("replied") == 1
        assert summary.get("new") == 1

    def test_empty_db_returns_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        init_db(db)
        assert get_status_summary(db) == {}


class TestClear:
    def test_clear_all(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        upsert_candidate(_make_scored(post_url="https://u1"), "2026-03-17", db_path)
        upsert_candidate(_make_scored(post_url="https://u2"), "2026-03-18", db_path)
        
        count = clear_new_candidates(db_path)
        assert count == 2
        
        summary = get_status_summary(db_path)
        assert summary.get("new") is None
        assert summary.get("skipped") == 2

    def test_clear_by_date(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        upsert_candidate(_make_scored(post_url="https://u1"), "2026-03-17", db_path)
        upsert_candidate(_make_scored(post_url="https://u2"), "2026-03-18", db_path)
        
        count = clear_new_candidates(db_path, date_str="2026-03-17")
        assert count == 1
        
        summary = get_status_summary(db_path)
        assert summary.get("new") == 1
        assert summary.get("skipped") == 1
