"""SQLite store for tracking reply candidates.

Optionally used to close the loop — did you reply? Which posts were surfaced?
Defaults to the shared local-first coordination DB (~/.local-first/local-first.db)
so candidates are queryable by Claude via the SQLite MCP server alongside
thread_triage and other coordination data.

Schema:
    candidates(id, platform, author_handle, post_url, text, score, angle,
               date, status, replied_at)

status values: 'new' | 'replied' | 'skipped'
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from .scorer import ScoredPost

logger = logging.getLogger(__name__)

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
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(_CREATE_TABLE)
        conn.commit()


def upsert_candidate(scored: ScoredPost, date: str, path: str) -> None:
    """Insert a scored post; ignore if the URL was already stored."""
    p = scored.post
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO candidates
                (platform, author_handle, post_url, text, score, angle, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (p.platform, p.author_handle, p.post_url, p.text, scored.score, scored.angle, date),
        )
        conn.commit()


def get_new_candidates(date: str, path: str) -> list[dict]:
    """Return all 'new' candidates for a given date, ordered by score desc."""
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM candidates WHERE date = ? AND status = 'new' ORDER BY score DESC",
            (date,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_candidate(post_url: str, status: str, path: str) -> None:
    """Update the status of a candidate ('replied' | 'skipped')."""
    replied_at = datetime.now().isoformat() if status == "replied" else None
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE candidates SET status = ?, replied_at = ? WHERE post_url = ?",
            (status, replied_at, post_url),
        )
        conn.commit()


def get_status_summary(path: str) -> dict[str, int]:
    """Return counts per status across all candidates."""
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM candidates GROUP BY status"
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def is_seen(post_url: str, path: str) -> bool:
    """Return True if the post URL is already in the store."""
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM candidates WHERE post_url = ?", (post_url,)
        ).fetchone()
    return row is not None
