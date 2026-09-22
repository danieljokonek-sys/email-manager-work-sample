"""Cross-reference pending items with upcoming calendar events.

Used by the ``horizon`` CLI view. An item is shown under an event when it
shares the event's entity, or when it is dated within a few days of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

ACTION_WINDOW_DAYS = 5
DEADLINE_WINDOW_DAYS = 5
TASK_WINDOW_DAYS = 7


def event_date(event: Mapping[str, Any]) -> date | None:
    raw = event.get("start_date") or (event.get("start_datetime") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _within(ev_date: date | None, due: str | None, window: int) -> bool:
    if ev_date is None or not due:
        return False
    try:
        return abs((ev_date - date.fromisoformat(due)).days) <= window
    except ValueError:
        return False


def items_for_event(
    event: Mapping[str, Any], data: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, list[Mapping[str, Any]]]:
    """Pending items that relate to one event, grouped by kind."""
    entity = event.get("entity_key")
    ev_date = event_date(event)

    def same_entity(item: Mapping[str, Any]) -> bool:
        return bool(entity) and item.get("entity_key") == entity

    return {
        "deadlines": [
            i
            for i in data.get("deadlines", [])
            if same_entity(i) or _within(ev_date, i.get("due_date"), DEADLINE_WINDOW_DAYS)
        ],
        "financial": [i for i in data.get("financial_items", []) if same_entity(i)],
        "actions": [
            i
            for i in data.get("action_items", [])
            if same_entity(i) or _within(ev_date, i.get("due_date"), ACTION_WINDOW_DAYS)
        ],
        "tasks": [
            i
            for i in data.get("tasks", [])
            if same_entity(i) or _within(ev_date, i.get("due_date"), TASK_WINDOW_DAYS)
        ],
        "follow_ups": [i for i in data.get("follow_ups", []) if same_entity(i)],
    }
