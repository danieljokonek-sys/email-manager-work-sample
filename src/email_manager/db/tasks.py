"""Manual tasks: things the owner adds by hand that are not tied to an email."""

from __future__ import annotations

from typing import Any

from email_manager.db.core import get_connection, utcnow_iso


def add_task(
    title: str,
    notes: str | None = None,
    entity_key: str = "personal",
    due_date: str | None = None,
    priority: str = "medium",
) -> int:
    conn = get_connection()
    now = utcnow_iso()
    cur = conn.execute(
        """INSERT INTO tasks (title, notes, entity_key, due_date, priority, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, notes, entity_key, due_date, priority, now, now),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def get_pending_tasks() -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM tasks WHERE status = 'pending'
           ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    due_date ASC NULLS LAST, created_at ASC"""
    ).fetchall()
    return [dict(r) for r in rows]


def complete_task(task_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ? AND status = 'pending'",
        (utcnow_iso(), task_id),
    )
    conn.commit()
    return cur.rowcount == 1


def delete_task(task_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    return cur.rowcount == 1
