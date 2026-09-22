"""Calendar events pulled from Google Calendar or Microsoft Graph."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from email_manager.db.core import get_connection


def normalize_datetime(value: str | None) -> str | None:
    """Return a local, timezone-naive ISO string (``YYYY-MM-DDTHH:MM:SS``) or None.

    Providers hand back different shapes: Google sends offsets, Graph sends
    seven fractional digits. Everything is normalised here, at the store, so
    the queries can compare strings safely.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        # Graph's "2026-09-22T17:00:00.0000000" has more precision than fromisoformat accepts.
        try:
            dt = datetime.fromisoformat(value[:26])
        except ValueError:
            return value[:19] if len(value) >= 19 else value
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat(timespec="seconds")


def _attendees(value: Any) -> list[str]:
    if isinstance(value, str):
        return [a.strip() for a in value.split(",") if a.strip()]
    if isinstance(value, Iterable):
        return [str(a) for a in value if a]
    return []


def store_calendar_events(events: Iterable[Mapping[str, Any]]) -> int:
    """Upsert events. Returns how many rows were written."""
    conn = get_connection()
    count = 0
    for ev in events:
        conn.execute(
            """INSERT OR REPLACE INTO calendar_events
               (event_id, calendar_id, calendar_name, account_email,
                title, description, location,
                start_date, start_datetime, end_date, end_datetime,
                all_day, attendees, status, entity_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ev["event_id"],
                ev.get("calendar_id"),
                ev.get("calendar_name"),
                ev.get("account_email"),
                ev.get("title"),
                ev.get("description", ""),
                ev.get("location", ""),
                ev.get("start_date"),
                normalize_datetime(ev.get("start_datetime")),
                ev.get("end_date"),
                normalize_datetime(ev.get("end_datetime")),
                1 if ev.get("all_day") else 0,
                json.dumps(_attendees(ev.get("attendees"))),
                ev.get("status", "confirmed"),
                ev.get("entity_key"),
            ),
        )
        count += 1
    conn.commit()
    return count


def get_upcoming_events(days_ahead: int = 14) -> list[dict[str, Any]]:
    """Events starting within the next ``days_ahead`` days, soonest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM calendar_events
           WHERE status != 'cancelled'
             AND (
               (start_datetime IS NOT NULL
                AND start_datetime >= strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')
                AND start_datetime <= strftime('%Y-%m-%dT%H:%M:%S', 'now', '+' || ? || ' days', 'localtime'))
               OR
               (start_date IS NOT NULL AND start_datetime IS NULL
                AND start_date >= date('now', 'localtime')
                AND start_date <= date('now', '+' || ? || ' days', 'localtime'))
             )
           ORDER BY COALESCE(start_datetime, start_date) ASC""",
        (int(days_ahead), int(days_ahead)),
    ).fetchall()
    return [dict(r) for r in rows]
