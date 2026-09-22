"""Items Claude extracted from email: agreements, deadlines, money, action items."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from email_manager.db.core import get_connection
from email_manager.schemas import EmailExtraction


def _owner_clause(owner_name: str, alias: str = "") -> tuple[str, tuple[str, ...]]:
    """SQL fragment selecting action items that belong to the owner.

    Items assigned to nobody, to "owner", or to a name containing the owner's
    first name are the owner's. With no owner name configured the name match is
    skipped rather than matching everything.
    """
    col = f"{alias}.assigned_to" if alias else "assigned_to"
    clause = f"({col} IS NULL OR lower({col}) = 'owner'"
    params: tuple[str, ...] = ()
    if owner_name:
        clause += f" OR lower({col}) LIKE '%' || lower(?) || '%'"
        params = (owner_name,)
    return clause + ")", params


def _already_stored(conn: sqlite3.Connection, table: str, email_id: str, text: str) -> bool:
    # Re-analysing an email must not duplicate its items. The column name is
    # fixed per table below; nothing user-controlled reaches the SQL text.
    column = {"agreements": "summary"}.get(table, "description")
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE email_id = ? AND {column} = ? LIMIT 1",
        (email_id, text),
    ).fetchone()
    return row is not None


def store_extractions(email_id: str, extraction: EmailExtraction) -> int:
    """Store everything extracted from one email and mark it processed.

    Runs in one transaction so a failure part-way leaves the email unprocessed
    and nothing half-written. Returns the number of items inserted.
    """
    conn = get_connection()
    inserted = 0
    try:
        conn.execute("BEGIN")
        for agreement in extraction.agreements:
            if _already_stored(conn, "agreements", email_id, agreement.summary):
                continue
            conn.execute(
                """INSERT INTO agreements (email_id, entity_key, summary, parties, terms, source_date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    email_id,
                    agreement.entity_key,
                    agreement.summary,
                    json.dumps(agreement.parties),
                    agreement.terms,
                    agreement.source_date,
                ),
            )
            inserted += 1

        for deadline in extraction.deadlines:
            if _already_stored(conn, "deadlines", email_id, deadline.description):
                continue
            conn.execute(
                """INSERT INTO deadlines (email_id, entity_key, description, due_date, priority, source_date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    email_id,
                    deadline.entity_key,
                    deadline.description,
                    deadline.due_date,
                    deadline.priority,
                    deadline.source_date,
                ),
            )
            inserted += 1

        for item in extraction.financial_items:
            if _already_stored(conn, "financial_items", email_id, item.description or ""):
                continue
            conn.execute(
                """INSERT INTO financial_items
                   (email_id, entity_key, direction, counterparty, amount, currency,
                    description, due_date, source_date, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    email_id,
                    item.entity_key,
                    item.direction,
                    item.counterparty,
                    item.amount,
                    item.currency,
                    item.description,
                    item.due_date,
                    item.source_date,
                    item.status,
                ),
            )
            inserted += 1

        for action in extraction.action_items:
            if _already_stored(conn, "action_items", email_id, action.description):
                continue
            conn.execute(
                """INSERT INTO action_items
                   (email_id, entity_key, description, assigned_to, due_date, priority, source_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    email_id,
                    action.entity_key,
                    action.description,
                    action.assigned_to,
                    action.due_date,
                    action.priority,
                    action.source_date,
                ),
            )
            inserted += 1

        conn.execute("UPDATE emails SET processed = 1 WHERE id = ?", (email_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return inserted


def get_pending_deadlines(days_ahead: int = 14) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT d.*, e.subject AS email_subject, e.sender AS email_sender
           FROM deadlines d
           LEFT JOIN emails e ON d.email_id = e.id
           WHERE d.status = 'pending'
             AND d.due_date IS NOT NULL
             AND d.due_date <= date('now', '+' || ? || ' days')
           ORDER BY d.due_date ASC""",
        (int(days_ahead),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_pending_financial(
    direction: str | None = None, max_age_days: int = 7
) -> list[dict[str, Any]]:
    """Pending money items for the CLI status view.

    An item first seen more than ``max_age_days`` ago drops off unless it still
    carries a future due date. The emailed board uses its own window rules in
    :mod:`email_manager.db.bulletin`.
    """
    conn = get_connection()
    age_param = f"-{int(max_age_days)} days"
    sql = """SELECT f.*, e.subject AS email_subject, e.sender AS email_sender
             FROM financial_items f
             LEFT JOIN emails e ON f.email_id = e.id
             WHERE f.status = 'pending'
               AND (date(COALESCE(f.source_date, f.created_at)) >= date('now', ?)
                    OR (f.due_date IS NOT NULL AND date(f.due_date) >= date('now')))"""
    params: list[Any] = [age_param]
    if direction:
        sql += " AND f.direction = ?"
        params.append(direction)
    sql += " ORDER BY f.direction, f.due_date ASC NULLS LAST"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_active_agreements() -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.*, e.subject AS email_subject, e.sender AS email_sender
           FROM agreements a
           LEFT JOIN emails e ON a.email_id = e.id
           WHERE a.status = 'active'
           ORDER BY a.created_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_pending_actions(owner_name: str) -> list[dict[str, Any]]:
    conn = get_connection()
    clause, params = _owner_clause(owner_name, alias="a")
    rows = conn.execute(
        f"""SELECT a.*, e.subject AS email_subject, e.sender AS email_sender
            FROM action_items a
            LEFT JOIN emails e ON a.email_id = e.id
            WHERE a.status = 'pending' AND {clause}
            ORDER BY a.priority DESC, a.due_date ASC NULLS LAST""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_status_summary(owner_name: str) -> dict[str, Any]:
    """Counts for the ``status`` command."""
    conn = get_connection()
    owner_sql, owner_params = _owner_clause(owner_name)

    def scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
        return conn.execute(sql, params).fetchone()[0]

    return {
        "total_emails": scalar("SELECT COUNT(*) FROM emails"),
        "unprocessed_emails": scalar("SELECT COUNT(*) FROM emails WHERE processed = 0"),
        "active_agreements": scalar("SELECT COUNT(*) FROM agreements WHERE status = 'active'"),
        "pending_deadlines": scalar("SELECT COUNT(*) FROM deadlines WHERE status = 'pending'"),
        "money_owed_to_you": scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM financial_items "
            "WHERE direction = 'receivable' AND status = 'pending'"
        ),
        "money_you_owe": scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM financial_items "
            "WHERE direction = 'payable' AND status = 'pending'"
        ),
        "pending_actions": scalar(
            f"SELECT COUNT(*) FROM action_items WHERE status = 'pending' AND {owner_sql}",
            owner_params,
        ),
        "upcoming_events_7d": scalar(
            """SELECT COUNT(*) FROM calendar_events
               WHERE status != 'cancelled'
                 AND ((start_datetime IS NOT NULL
                       AND start_datetime >= strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')
                       AND start_datetime <= strftime('%Y-%m-%dT%H:%M:%S', 'now', '+7 days', 'localtime'))
                   OR (start_date IS NOT NULL AND start_datetime IS NULL
                       AND start_date >= date('now', 'localtime')
                       AND start_date <= date('now', '+7 days', 'localtime')))"""
        ),
        "pending_tasks": scalar("SELECT COUNT(*) FROM tasks WHERE status = 'pending'"),
        "follow_ups_waiting": scalar("SELECT COUNT(*) FROM follow_ups WHERE status = 'pending'"),
        "stale_actions": scalar(
            "SELECT COUNT(*) FROM action_items WHERE status = 'pending' "
            "AND created_at <= datetime('now', '-3 days')"
        ),
    }
