"""SQLite store for tracking reply candidates."""

from local_first_common import db

from .scorer import ScoredPost

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT NOT NULL,
    author_handle TEXT NOT NULL,
    post_url     TEXT NOT NULL UNIQUE,
    text         TEXT NOT NULL,
    score        REAL NOT NULL,
    angle        TEXT,
    date         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'new',
    replied_at   TIMESTAMP,
    search_term  TEXT
);
"""


def init_db(path: str) -> None:
    """Create the candidates table if it doesn't exist."""
    db.init_db(path, _CREATE_TABLE)
    # Migration: add search_term for existing DBs that predate this column.
    import sqlite3

    with sqlite3.connect(path) as conn:
        for column in (
            "search_term TEXT",
            # 2026-10-07: the day's top picks and Jamal's verdicts on them (see verdict_*).
            "pick INTEGER NOT NULL DEFAULT 0",
            "human_verdict TEXT",
            "human_note TEXT",
            "verdict_at TIMESTAMP",
        ):
            try:
                conn.execute(f"ALTER TABLE candidates ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass  # column already exists


def upsert_candidate(scored: ScoredPost, date: str, path: str) -> None:
    """Insert a scored post; ignore if the URL was already stored."""
    p = scored.post
    with db.get_db_cursor(path) as cur:
        if cur is None:  # get_db_cursor yields None when the file is missing; callers init_db first
            raise FileNotFoundError(f"candidate store not initialized: {path}")
        cur.execute(
            """
            INSERT OR IGNORE INTO candidates
                (platform, author_handle, post_url, text, score, angle, date, search_term)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (p.platform, p.author_handle, p.post_url, p.text, scored.score, scored.angle, date, p.search_term),
        )
        cur.connection.commit()


def get_new_candidates(date: str, path: str) -> list[dict]:
    """Return all 'new' candidates for a given date, ordered by score desc."""
    with db.get_db_cursor(path) as cur:
        if cur is None:
            return []
        cur.execute(
            "SELECT * FROM candidates WHERE date = ? AND status = 'new' ORDER BY score DESC",
            (date,),
        )
        return [dict(r) for r in cur.fetchall()]


def mark_candidate(post_url: str, status: str, path: str) -> None:
    """Update the status of a candidate ('replied' | 'skipped')."""
    db.mark_status(
        path,
        "candidates",
        "post_url",
        post_url,
        "status",
        status,
        timestamp_col="replied_at" if status == "replied" else None,
    )


def get_status_summary(path: str) -> dict[str, int]:
    """Return counts per status across all candidates."""
    with db.get_db_cursor(path) as cur:
        if cur is None:
            return {}
        cur.execute("SELECT status, COUNT(*) as cnt FROM candidates GROUP BY status")
        return {row[0]: row[1] for row in cur.fetchall()}


def is_seen(post_url: str, path: str) -> bool:
    """Return True if the post URL is already in the store."""
    return db.is_seen(path, "candidates", "post_url", post_url)


def clear_new_candidates(path: str, date_str: str | None = None) -> int:
    """Mark all 'new' candidates as 'skipped'. If date_str is provided, only clear that date."""
    with db.get_db_cursor(path) as cur:
        if cur is None:
            return 0
        if date_str:
            cur.execute(
                "UPDATE candidates SET status = 'skipped' WHERE status = 'new' AND date = ?",
                (date_str,),
            )
        else:
            cur.execute("UPDATE candidates SET status = 'skipped' WHERE status = 'new'")

        count = cur.rowcount
        cur.connection.commit()
        return count


# --- Picks and verdicts (2026-10-07) ------------------------------------------
#
# `run --top N` marks the N best candidates of the day as picks: what the model
# chose. Jamal rates picks blind with /rate-replies: `reply` (keep, and he wants
# to answer it), `keep`, or `dismiss`. His verdicts are what the scorer learns
# from (examples()) and the measure of how often it agrees with him (verdict_stats()).

VERDICTS = ("reply", "keep", "dismiss")
_POSITIVE = ("reply", "keep")


def _rows(path: str, sql: str, params: tuple = ()) -> list[dict]:
    with db.get_db_cursor(path) as cur:
        if cur is None:
            return []
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def mark_picks(date: str, n: int, path: str) -> list[dict]:
    """Make the day's N highest-scoring candidates its picks (and nothing else)."""
    with db.get_db_cursor(path) as cur:
        if cur is None:
            raise FileNotFoundError(f"candidate store not initialized: {path}")
        cur.execute("UPDATE candidates SET pick = 0 WHERE date = ?", (date,))
        cur.execute(
            "UPDATE candidates SET pick = 1 WHERE id IN "
            "(SELECT id FROM candidates WHERE date = ? ORDER BY score DESC, id LIMIT ?)",
            (date, n),
        )
        cur.connection.commit()
    return get_picks(date, path)


def get_picks(date: str, path: str) -> list[dict]:
    return _rows(path, "SELECT * FROM candidates WHERE date = ? AND pick = 1 ORDER BY score DESC, id", (date,))


def verdict_pending(path: str, limit: int = 6, days: int = 7) -> list[dict]:
    """Recent picks Jamal hasn't rated, newest day first, in fetch order (not score order,
    so the order gives nothing away)."""
    return _rows(
        path,
        "SELECT * FROM candidates WHERE pick = 1 AND human_verdict IS NULL "
        "AND date >= date('now', ?) ORDER BY date DESC, id LIMIT ?",
        (f"-{days} days", limit),
    )


def resolve_candidate(ref: str | int, path: str) -> dict:
    """One candidate by id, exact URL, or a fragment of its URL. Raises LookupError otherwise."""
    if isinstance(ref, int) or str(ref).isdigit():
        rows = _rows(path, "SELECT * FROM candidates WHERE id = ?", (int(ref),))
    else:
        rows = _rows(path, "SELECT * FROM candidates WHERE post_url = ?", (ref,))
        if not rows:
            rows = _rows(path, "SELECT * FROM candidates WHERE post_url LIKE ?", (f"%{ref}%",))
    if len(rows) != 1:
        raise LookupError(f"{len(rows)} candidates match {ref!r}; be more specific")
    return rows[0]


def set_verdict(ref: str | int, verdict: str, path: str, note: str | None = None) -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, not {verdict!r}")
    row = resolve_candidate(ref, path)
    with db.get_db_cursor(path) as cur:
        if cur is None:
            raise FileNotFoundError(f"candidate store not initialized: {path}")
        cur.execute(
            "UPDATE candidates SET human_verdict = ?, human_note = ?, verdict_at = datetime('now') WHERE id = ?",
            (verdict, note or None, row["id"]),
        )
        cur.connection.commit()
    return resolve_candidate(row["id"], path)


def verdict_stats(path: str) -> dict:
    """How often the model's picks match Jamal's verdicts, overall and by score band."""
    rows = _rows(path, "SELECT score, pick, human_verdict FROM candidates WHERE human_verdict IS NOT NULL")
    bands: dict[str, dict[str, int]] = {}
    agreed = 0
    counts = dict.fromkeys(VERDICTS, 0)
    for r in rows:
        counts[r["human_verdict"]] += 1
        ok = bool(r["pick"]) == (r["human_verdict"] in _POSITIVE)
        agreed += ok
        band = f"{int(r['score'] * 10) / 10:.1f}"
        b = bands.setdefault(band, {"rated": 0, "positive": 0, "agreed": 0})
        b["rated"] += 1
        b["positive"] += r["human_verdict"] in _POSITIVE
        b["agreed"] += ok
    return {"rated": len(rows), "agreed": agreed, "verdicts": counts, "bands": dict(sorted(bands.items()))}


def examples(path: str, n_positive: int = 6, n_negative: int = 6, max_chars: int = 200) -> dict[str, list[dict]]:
    """Few-shot examples for the scorer: {'reply': [...], 'keep': [...], 'dismiss': [...]},
    each {text, note, human}. Jamal's verdicts come first and override what the model did;
    unrated picks fill the keep side, unrated non-picks the dismiss side, so the window is
    only as machine-decided as it has to be. Never raises; empty lists without data."""
    sql = (
        "SELECT text, human_note AS note, human_verdict IS NOT NULL AS human FROM candidates "
        "WHERE human_verdict = ? OR (human_verdict IS NULL AND pick = ?) "
        "ORDER BY human DESC, COALESCE(verdict_at, date) DESC, id DESC LIMIT ?"
    )
    out: dict[str, list[dict]] = {"reply": [], "keep": [], "dismiss": []}
    try:
        out["reply"] = _rows(path, sql, ("reply", -1, n_positive))
        out["keep"] = _rows(path, sql, ("keep", 1, max(0, n_positive - len(out["reply"]))))
        out["dismiss"] = _rows(path, sql, ("dismiss", 0, n_negative))
    except Exception:  # noqa: BLE001 - examples are a nicety; scoring must not fail without them
        return {"reply": [], "keep": [], "dismiss": []}
    for side in out.values():
        for r in side:
            r["text"] = " ".join(r["text"].split())[:max_chars]
            r["human"] = bool(r["human"])
    return out
