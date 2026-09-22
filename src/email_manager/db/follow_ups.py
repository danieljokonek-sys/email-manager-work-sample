"""Threads the owner sent last and has not heard back on."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from email_manager.db.core import get_connection, utcnow_iso


def upsert_follow_up(fu: Mapping[str, Any]) -> None:
    """Insert a follow-up or refresh its waiting time (keyed on thread_id)."""
    conn = get_connection()
    now = utcnow_iso()
    conn.execute(
        """INSERT INTO follow_ups
               (thread_id, account_email, subject, recipient, last_sent_date,
                entity_key, days_waiting, status, detected_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
           ON CONFLICT(thread_id) DO UPDATE SET
               days_waiting = excluded.days_waiting,
               updated_at = excluded.updated_at
           WHERE follow_ups.status = 'pending'""",
        (
            fu["thread_id"],
            fu.get("account_email"),
            fu.get("subject"),
            fu.get("recipient"),
            fu.get("last_sent_date"),
            fu.get("entity_key"),
            fu.get("days_waiting", 0),
            now,
            now,
        ),
    )
    conn.commit()


def mark_follow_up_replied(thread_id: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE follow_ups SET status = 'replied', updated_at = ? WHERE thread_id = ?",
        (utcnow_iso(), thread_id),
    )
    conn.commit()


def dismiss_follow_up(follow_up_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE follow_ups SET status = 'dismissed', updated_at = ? WHERE id = ?",
        (utcnow_iso(), follow_up_id),
    )
    conn.commit()
    return cur.rowcount == 1


def get_pending_follow_ups() -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM follow_ups WHERE status = 'pending' ORDER BY days_waiting DESC"
    ).fetchall()
    return [dict(r) for r in rows]
