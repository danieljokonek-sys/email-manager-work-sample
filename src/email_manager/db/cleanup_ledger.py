"""The cleanup ledger: which emails have already been classified and acted on.

Kept in its own file (``data/organized.db``) because it is a seen-set with a
different lifecycle from the briefing data: it is safe to clear it to force a
full reclassification without touching anything the briefing depends on.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from email_manager import paths

_db_path: Path | None = None
_conn: sqlite3.Connection | None = None


def configure(path: Path | None) -> None:
    global _db_path
    close()
    _db_path = Path(path) if path is not None else None


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def get_connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = _db_path or paths.cleanup_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS organized (
                   email_id      TEXT PRIMARY KEY,
                   account_email TEXT,
                   category      TEXT,
                   action        TEXT,
                   confidence    TEXT,
                   reason        TEXT,
                   organized_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        _conn.commit()
    return _conn


def already_organized(email_ids: Iterable[str]) -> set[str]:
    ids = list(email_ids)
    if not ids:
        return set()
    conn = get_connection()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT email_id FROM organized WHERE email_id IN ({placeholders})", ids
    ).fetchall()
    return {r[0] for r in rows}


def record_organized(account_email: str, results: Mapping[str, Mapping[str, Any]]) -> None:
    """Record the outcome for each email so it is never re-fetched or re-billed."""
    conn = get_connection()
    conn.executemany(
        "INSERT OR REPLACE INTO organized (email_id, account_email, category, action, confidence, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                email_id,
                account_email,
                info.get("category"),
                info.get("action"),
                info.get("confidence"),
                info.get("reason"),
            )
            for email_id, info in results.items()
        ],
    )
    conn.commit()


def forget(email_ids: Iterable[str]) -> None:
    """Drop emails from the ledger so the next cleanup reclassifies them."""
    conn = get_connection()
    conn.executemany("DELETE FROM organized WHERE email_id = ?", [(i,) for i in email_ids])
    conn.commit()


def summary() -> list[tuple[str, str, int]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT category, action, COUNT(*) FROM organized GROUP BY category, action ORDER BY COUNT(*) DESC"
    ).fetchall()
    return [(r[0], r[1], int(r[2])) for r in rows]


def total() -> int:
    conn = get_connection()
    return int(conn.execute("SELECT COUNT(*) FROM organized").fetchone()[0])
