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
    replied_at   TIMESTAMP
);
"""

def init_db(path: str) -> None:
    """Create the candidates table if it doesn't exist."""
    db.init_db(path, _CREATE_TABLE)

def upsert_candidate(scored: ScoredPost, date: str, path: str) -> None:
    """Insert a scored post; ignore if the URL was already stored."""
    p = scored.post
    with db.get_db_cursor(path) as cur:
        cur.execute(
            """
            INSERT OR IGNORE INTO candidates
                (platform, author_handle, post_url, text, score, angle, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (p.platform, p.author_handle, p.post_url, p.text, scored.score, scored.angle, date),
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
    db.mark_status(path, "candidates", "post_url", post_url, "status", status, 
                   timestamp_col="replied_at" if status == "replied" else None)

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
