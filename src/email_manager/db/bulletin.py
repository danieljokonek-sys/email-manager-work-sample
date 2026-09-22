"""The board: what goes on today's digest, and the duplicate-merge support for it.

The emailed brief is a read-only bulletin board. Nothing is ever checked off,
so every item has to age off on its own. The rules, in days:

* dated items (deadlines, action items, money, tasks) show from
  ``overdue_grace_days`` after their date passes (so a miss is still visible
  for a few mornings) through ``days_ahead`` into the future;
* undated action/money items show for ``undated_ttl_days`` after they were
  first seen (source_date, falling back to created_at), then drop;
* undated manual tasks show for ``TASK_UNDATED_TTL_DAYS`` after they were added;
* waiting-for-reply threads show only while the sent-folder scan keeps
  re-detecting them (updated within ``FOLLOW_UP_STALE_DAYS``) and they have
  been waiting between ``follow_up_min_days`` and ``FOLLOW_UP_MAX_DAYS``.

Only what is on the board is sent to Claude, which is what keeps the digest
call small. Rows are trimmed to the fields the writer needs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from email_manager.db.calendar import get_upcoming_events
from email_manager.db.core import assert_identifier, get_connection
from email_manager.db.items import _owner_clause

BULLETIN_DAYS_AHEAD = 14
BULLETIN_OVERDUE_GRACE_DAYS = 3
BULLETIN_UNDATED_TTL_DAYS = 7
TASK_UNDATED_TTL_DAYS = 14
FOLLOW_UP_MIN_DAYS = 3
FOLLOW_UP_MAX_DAYS = 21
FOLLOW_UP_STALE_DAYS = 2

_EVENT_FIELDS = (
    "title",
    "start_date",
    "start_datetime",
    "end_datetime",
    "all_day",
    "location",
    "calendar_name",
    "entity_key",
)
_DEADLINE_FIELDS = ("id", "entity_key", "description", "due_date", "priority")
_ACTION_FIELDS = ("id", "entity_key", "description", "due_date", "priority", "source_date")
_FINANCIAL_FIELDS = (
    "id",
    "entity_key",
    "direction",
    "counterparty",
    "amount",
    "currency",
    "description",
    "due_date",
    "source_date",
)
_TASK_FIELDS = ("id", "entity_key", "title", "notes", "due_date", "priority")
_FOLLOW_UP_FIELDS = ("id", "entity_key", "subject", "recipient", "days_waiting", "last_sent_date")


def _pick(row: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Keep only the listed keys, dropping empty values, to shrink the prompt."""
    return {k: row[k] for k in keys if row.get(k) not in (None, "", "[]", 0)}


def get_bulletin_items(
    days_ahead: int = BULLETIN_DAYS_AHEAD,
    overdue_grace_days: int = BULLETIN_OVERDUE_GRACE_DAYS,
    undated_ttl_days: int = BULLETIN_UNDATED_TTL_DAYS,
    follow_up_min_days: int = FOLLOW_UP_MIN_DAYS,
    owner_name: str = "",
    exclude_recipients: tuple[str, ...] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Return everything that belongs on today's board, trimmed for the prompt.

    ``exclude_recipients`` is the owner's own addresses: a sent thread whose
    recipient is one of them is a note-to-self, not a reply being waited on.
    """
    conn = get_connection()
    ahead = f"+{int(days_ahead)} days"
    grace = f"-{int(overdue_grace_days)} days"
    ttl = f"-{int(undated_ttl_days)} days"
    task_ttl = f"-{int(TASK_UNDATED_TTL_DAYS)} days"
    stale = f"-{int(FOLLOW_UP_STALE_DAYS)} days"

    events = [_pick(e, _EVENT_FIELDS) for e in get_upcoming_events(days_ahead=days_ahead)]

    deadlines = [
        _pick(dict(r), _DEADLINE_FIELDS)
        for r in conn.execute(
            """SELECT id, entity_key, description, due_date, priority
               FROM deadlines
               WHERE status = 'pending' AND due_date IS NOT NULL
                 AND date(due_date) BETWEEN date('now', ?) AND date('now', ?)
               ORDER BY due_date ASC""",
            (grace, ahead),
        ).fetchall()
    ]

    owner_sql, owner_params = _owner_clause(owner_name)
    action_items = [
        _pick(dict(r), _ACTION_FIELDS)
        for r in conn.execute(
            f"""SELECT id, entity_key, description, due_date, priority, source_date
                FROM action_items
                WHERE status = 'pending'
                  AND {owner_sql}
                  AND ((due_date IS NOT NULL
                        AND date(due_date) BETWEEN date('now', ?) AND date('now', ?))
                       OR (due_date IS NULL
                           AND date(COALESCE(source_date, created_at)) >= date('now', ?)))
                ORDER BY due_date IS NULL, due_date ASC,
                         CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END""",
            (*owner_params, grace, ahead, ttl),
        ).fetchall()
    ]

    financial_items = [
        _pick(dict(r), _FINANCIAL_FIELDS)
        for r in conn.execute(
            """SELECT id, entity_key, direction, counterparty, amount, currency,
                      description, due_date, source_date
               FROM financial_items
               WHERE status = 'pending'
                 AND ((due_date IS NOT NULL
                       AND date(due_date) BETWEEN date('now', ?) AND date('now', ?))
                      OR (due_date IS NULL
                          AND date(COALESCE(source_date, created_at)) >= date('now', ?)))
               ORDER BY due_date IS NULL, due_date ASC, direction""",
            (grace, ahead, ttl),
        ).fetchall()
    ]

    tasks = [
        _pick(dict(r), _TASK_FIELDS)
        for r in conn.execute(
            """SELECT id, entity_key, title, notes, due_date, priority
               FROM tasks
               WHERE status = 'pending'
                 AND ((due_date IS NOT NULL
                       AND date(due_date) BETWEEN date('now', ?) AND date('now', ?))
                      OR (due_date IS NULL AND date(created_at) >= date('now', ?)))
               ORDER BY due_date IS NULL, due_date ASC,
                        CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END""",
            (grace, ahead, task_ttl),
        ).fetchall()
    ]

    follow_ups = [
        _pick(dict(r), _FOLLOW_UP_FIELDS)
        for r in conn.execute(
            """SELECT id, entity_key, subject, recipient, days_waiting, last_sent_date
               FROM follow_ups
               WHERE status = 'pending'
                 AND days_waiting BETWEEN ? AND ?
                 AND datetime(substr(updated_at, 1, 19)) >= datetime('now', ?)
               ORDER BY days_waiting DESC""",
            (int(follow_up_min_days), FOLLOW_UP_MAX_DAYS, stale),
        ).fetchall()
    ]
    if exclude_recipients:
        own = tuple(a.lower() for a in exclude_recipients if a)
        follow_ups = [
            fu
            for fu in follow_ups
            if not any(addr in (fu.get("recipient") or "").lower() for addr in own)
        ]

    return {
        "events": events,
        "deadlines": deadlines,
        "action_items": action_items,
        "financial_items": financial_items,
        "tasks": tasks,
        "follow_ups": follow_ups,
    }


# ── Duplicate reconciliation ────────────────────────────────────────────────

# Tables the reconciler may touch, the columns it reads, and the fields it may
# overwrite on a surviving row. The identifier allowlist lives here, next to
# the only f-strings that use it.
RECONCILE_TABLES: tuple[str, ...] = ("financial_items", "action_items", "deadlines")

_RECONCILE_COLUMNS: dict[str, str] = {
    "financial_items": (
        "id, entity_key, direction, counterparty, amount, currency, "
        "description, due_date, source_date, created_at"
    ),
    "action_items": (
        "id, entity_key, description, assigned_to, due_date, priority, source_date, created_at"
    ),
    "deadlines": "id, entity_key, description, due_date, priority, source_date, created_at",
}

_MERGEABLE_FIELDS: dict[str, frozenset[str]] = {
    "financial_items": frozenset(
        {"counterparty", "amount", "currency", "description", "due_date", "source_date"}
    ),
    "action_items": frozenset(
        {"description", "assigned_to", "due_date", "priority", "source_date"}
    ),
    "deadlines": frozenset({"description", "due_date", "priority", "source_date"}),
}


def get_reconcilable_items(
    board: Mapping[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Full rows for the board items, grouped by type, for the reconciler.

    Only items that will actually be displayed are candidates for dedup: that
    is where duplicates are visible, and it keeps the reconcile prompt small.
    """
    conn = get_connection()
    out: dict[str, list[dict[str, Any]]] = {}
    for table in RECONCILE_TABLES:
        ids = [int(i["id"]) for i in board.get(table, []) if i.get("id") is not None]
        if not ids:
            out[table] = []
            continue
        cols = _RECONCILE_COLUMNS[assert_identifier(table, RECONCILE_TABLES)]
        placeholders = ",".join("?" * len(ids))
        out[table] = [
            dict(r)
            for r in conn.execute(
                f"SELECT {cols} FROM {table} WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        ]
    return out


def apply_item_merge(
    item_type: str,
    canonical_id: int,
    duplicate_ids: list[int],
    canonical_fields: Mapping[str, Any],
) -> int:
    """Fold ``duplicate_ids`` into ``canonical_id`` for one item type.

    The canonical row is updated with the synthesised fields (only allowed
    columns are touched); each duplicate is marked status='merged' with
    merged_into pointing at the survivor. Nothing is deleted, so the merge is
    reversible. Returns the number of rows marked merged.
    """
    table = assert_identifier(item_type, RECONCILE_TABLES)
    allowed = _MERGEABLE_FIELDS[table]
    dupes = [d for d in duplicate_ids if d != canonical_id]
    if not dupes:
        return 0
    conn = get_connection()
    sets = {k: v for k, v in canonical_fields.items() if k in allowed and v is not None}
    try:
        conn.execute("BEGIN")
        if sets:
            cols = ", ".join(f"{assert_identifier(k, allowed)} = ?" for k in sets)
            conn.execute(
                f"UPDATE {table} SET {cols} WHERE id = ?",
                (*sets.values(), canonical_id),
            )
        placeholders = ",".join("?" * len(dupes))
        cur = conn.execute(
            f"UPDATE {table} SET status = 'merged', merged_into = ? "
            f"WHERE id IN ({placeholders}) AND status = 'pending'",
            (canonical_id, *dupes),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return int(cur.rowcount)


# ── Horizon view (CLI) ──────────────────────────────────────────────────────


def get_horizon_data(days_ahead: int, owner_name: str) -> dict[str, list[dict[str, Any]]]:
    """Upcoming events plus every pending item, for the CLI horizon view."""
    events = get_upcoming_events(days_ahead=days_ahead)
    conn = get_connection()
    owner_sql, owner_params = _owner_clause(owner_name, alias="a")

    action_items = [
        dict(r)
        for r in conn.execute(
            f"""SELECT a.*, e.subject AS email_subject
                FROM action_items a LEFT JOIN emails e ON a.email_id = e.id
                WHERE a.status = 'pending' AND {owner_sql}
                ORDER BY a.due_date ASC NULLS LAST""",
            owner_params,
        ).fetchall()
    ]
    financial_items = [
        dict(r)
        for r in conn.execute(
            """SELECT f.*, e.subject AS email_subject
               FROM financial_items f LEFT JOIN emails e ON f.email_id = e.id
               WHERE f.status = 'pending'
               ORDER BY f.due_date ASC NULLS LAST"""
        ).fetchall()
    ]
    deadlines = [
        dict(r)
        for r in conn.execute(
            """SELECT d.*, e.subject AS email_subject
               FROM deadlines d LEFT JOIN emails e ON d.email_id = e.id
               WHERE d.status = 'pending'
               ORDER BY d.due_date ASC"""
        ).fetchall()
    ]
    follow_ups = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM follow_ups WHERE status = 'pending' ORDER BY days_waiting DESC"
        ).fetchall()
    ]
    tasks = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending' ORDER BY due_date ASC NULLS LAST"
        ).fetchall()
    ]
    return {
        "events": events,
        "action_items": action_items,
        "financial_items": financial_items,
        "deadlines": deadlines,
        "follow_ups": follow_ups,
        "tasks": tasks,
    }
