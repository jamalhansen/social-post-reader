"""Picks, verdicts and the scorer's few-shot examples (2026-10-07)."""

import sqlite3

import pytest

from social_reader import scorer
from social_reader import store as db_store


def _seed(path: str, rows: list[tuple[str, float, str]]) -> None:
    """rows: (text, score, date). URLs are derived so each row is unique."""
    db_store.init_db(path)
    with sqlite3.connect(path) as conn:
        for i, (text, score, day) in enumerate(rows):
            conn.execute(
                "INSERT INTO candidates (platform, author_handle, post_url, text, score, angle, date) "
                "VALUES ('bluesky', ?, ?, ?, ?, '', ?)",
                (f"user{i}", f"https://bsky.app/p/{i}", text, score, day),
            )


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "s.db")
    _seed(
        path,
        [
            ("juniors and AI", 0.9, "2026-10-07"),
            ("learning to code in 2026", 0.8, "2026-10-07"),
            ("ollama on a laptop", 0.7, "2026-10-07"),
            ("a fourth, lower one", 0.6, "2026-10-07"),
            ("yesterday's pick", 0.85, "2026-10-06"),
        ],
    )
    return path


def test_mark_picks_takes_the_top_n_of_that_day_only(store):
    picks = db_store.mark_picks("2026-10-07", 3, store)
    assert [p["text"] for p in picks] == ["juniors and AI", "learning to code in 2026", "ollama on a laptop"]
    assert db_store.get_picks("2026-10-06", store) == []
    # re-running with a smaller N drops the extra pick rather than accumulating
    assert len(db_store.mark_picks("2026-10-07", 1, store)) == 1


def test_pending_is_recent_unrated_picks_in_fetch_order(store):
    db_store.mark_picks("2026-10-07", 3, store)
    db_store.mark_picks("2026-10-06", 1, store)
    pending = db_store.verdict_pending(store, limit=10, days=3650)
    assert [p["text"] for p in pending][:3] == ["juniors and AI", "learning to code in 2026", "ollama on a laptop"]
    assert pending[-1]["text"] == "yesterday's pick"
    db_store.set_verdict(pending[0]["id"], "reply", store, "I have a story about this")
    assert len(db_store.verdict_pending(store, limit=10, days=3650)) == 3


def test_set_verdict_by_id_and_url_fragment_and_rejects_unknown(store):
    db_store.mark_picks("2026-10-07", 3, store)
    row = db_store.set_verdict("bsky.app/p/1", "dismiss", store)
    assert (row["human_verdict"], row["human_note"]) == ("dismiss", None)
    assert row["verdict_at"] is not None
    with pytest.raises(ValueError):
        db_store.set_verdict(row["id"], "maybe", store)
    with pytest.raises(LookupError):
        db_store.set_verdict("bsky.app", "keep", store)  # matches every row


def test_stats_count_agreement_with_the_picks(store):
    db_store.mark_picks("2026-10-07", 2, store)  # 0.9 and 0.8 are picks; 0.7 is not
    db_store.set_verdict("p/0", "reply", store)  # pick, positive: agreed
    db_store.set_verdict("p/1", "dismiss", store)  # pick, negative: disagreed
    db_store.set_verdict("p/2", "keep", store)  # not a pick, positive: disagreed
    st = db_store.verdict_stats(store)
    assert (st["rated"], st["agreed"]) == (3, 1)
    assert st["verdicts"] == {"reply": 1, "keep": 1, "dismiss": 1}
    assert st["bands"]["0.9"] == {"rated": 1, "positive": 1, "agreed": 1}


def test_examples_put_jamals_verdicts_first_and_fill_with_unrated_picks(store):
    db_store.mark_picks("2026-10-07", 3, store)
    db_store.set_verdict("p/2", "reply", store, "my daily driver")
    db_store.set_verdict("p/3", "dismiss", store)
    ex = db_store.examples(store, n_positive=3, n_negative=3)
    assert [r["text"] for r in ex["reply"]] == ["ollama on a laptop"]
    assert ex["reply"][0]["human"] and ex["reply"][0]["note"] == "my daily driver"
    # keep side: no human keeps, so unrated picks fill the remaining 2 slots
    assert {r["text"] for r in ex["keep"]} == {"juniors and AI", "learning to code in 2026"}
    assert all(not r["human"] for r in ex["keep"])
    # dismiss side: his dismissal first, then unrated non-picks (yesterday's never became a pick)
    assert [r["text"] for r in ex["dismiss"]] == ["a fourth, lower one", "yesterday's pick"]
    assert [r["human"] for r in ex["dismiss"]] == [True, False]


def test_examples_are_empty_not_an_error_without_data(tmp_path):
    assert db_store.examples(str(tmp_path / "none.db")) == {"reply": [], "keep": [], "dismiss": []}
    assert scorer.format_examples(None) == ""
    assert scorer.format_examples({"reply": [], "keep": [], "dismiss": []}) == ""


def test_format_examples_marks_his_verdicts_and_notes():
    block = scorer.format_examples(
        {
            "reply": [{"text": "juniors and AI", "note": "I have a story", "human": True}],
            "keep": [{"text": "an unrated pick", "note": None, "human": False}],
            "dismiss": [],
        }
    )
    assert "WANTED TO REPLY TO" in block and '"juniors and AI" [theirs] -- their note: "I have a story"' in block
    assert '"an unrated pick"' in block and 'an unrated pick" [theirs]' not in block
    assert "NOT WORTH SEEING" not in block
    system = scorer._SYSTEM_TEMPLATE.format(profile="me", examples=block)
    assert system.index('{"score": 0.75') < system.index("WANTED TO REPLY TO")
