"""Fetched emails, processing flags, and sync bookkeeping."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from email_manager.db.core import get_connection, utcnow_iso

BODY_SNIPPET_MAX_CHARS = 2000


def store_email(email: Mapping[str, Any]) -> bool:
    """Insert an email if it is new. Returns True when a row was written."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT OR IGNORE INTO emails
           (id, thread_id, account_email, sender, recipients, subject,
            body_snippet, date, labels, entity_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            email["id"],
            email.get("thread_id"),
            email.get("account_email", ""),
            email.get("sender"),
            json.dumps(email.get("recipients", [])),
            email.get("subject"),
            (email.get("body_snippet") or "")[:BODY_SNIPPET_MAX_CHARS],
            email.get("date"),
            json.dumps(email.get("labels", [])),
            email.get("entity_key"),
        ),
    )
    conn.commit()
    return cur.rowcount == 1


def set_email_entity(email_id: str, entity_key: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE emails SET entity_key = ? WHERE id = ?", (entity_key, email_id))
    conn.commit()


def mark_email_processed(email_id: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE emails SET processed = 1 WHERE id = ?", (email_id,))
    conn.commit()


def get_unprocessed_emails(limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM emails WHERE processed = 0 ORDER BY date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_unlabeled_emails(limit: int = 200) -> list[dict[str, Any]]:
    """Processed emails with an entity that have not been labeled in the mailbox yet."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, account_email, entity_key FROM emails
           WHERE processed = 1 AND gmail_labeled = 0
             AND entity_key IS NOT NULL AND entity_key != 'personal'
           ORDER BY date DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_emails_labeled(email_ids: list[str]) -> None:
    if not email_ids:
        return
    conn = get_connection()
    placeholders = ",".join("?" * len(email_ids))
    conn.execute(f"UPDATE emails SET gmail_labeled = 1 WHERE id IN ({placeholders})", email_ids)
    conn.commit()


def get_sync_state(key: str) -> str | None:
    conn = get_connection()
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_sync_state(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, utcnow_iso()),
    )
    conn.commit()
