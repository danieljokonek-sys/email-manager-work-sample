from datetime import date, timedelta
from pathlib import Path

import pytest

from email_manager import db
from email_manager.db.calendar import normalize_datetime
from email_manager.db.core import assert_identifier
from email_manager.schemas import ActionItem, Deadline, EmailExtraction, FinancialItem


def _d(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# ── migrations ──────────────────────────────────────────────────────────────


def test_fresh_database_is_at_current_schema_version(home: Path) -> None:
    conn = db.get_connection()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "emails",
        "deadlines",
        "financial_items",
        "action_items",
        "calendar_events",
        "tasks",
        "follow_ups",
    } <= tables


def test_init_db_is_idempotent(home: Path) -> None:
    db.init_db()
    db.init_db()
    assert db.get_connection().execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_legacy_database_is_upgraded_in_place(tmp_path: Path) -> None:
    """A pre-versioning database (missing columns, tz-suffixed events) migrates without data loss."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE emails (id TEXT PRIMARY KEY, thread_id TEXT, sender TEXT, recipients TEXT, subject TEXT,
            body_snippet TEXT, date TEXT, labels TEXT, entity_key TEXT, processed INTEGER DEFAULT 0, fetched_at TEXT);
        CREATE TABLE deadlines (id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT, entity_key TEXT,
            description TEXT NOT NULL, due_date TEXT, priority TEXT DEFAULT 'medium', status TEXT DEFAULT 'pending',
            source_date TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE calendar_events (event_id TEXT PRIMARY KEY, calendar_id TEXT, calendar_name TEXT,
            account_email TEXT, title TEXT, description TEXT, location TEXT, start_date TEXT, start_datetime TEXT,
            end_date TEXT, end_datetime TEXT, all_day INTEGER DEFAULT 0, attendees TEXT, status TEXT DEFAULT 'confirmed',
            entity_key TEXT, fetched_at TEXT);
        INSERT INTO calendar_events (event_id, start_datetime, end_datetime, status)
            VALUES ('e1', '2026-09-22T17:00:00.0000000', '2026-09-22T18:00:00.0000000', 'confirmed');
        INSERT INTO emails (id, subject) VALUES ('m1', 'hi');
        """
    )
    conn.commit()
    conn.close()

    db.configure(path)
    try:
        db.init_db()
        conn = db.get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(emails)")}
        assert {"account_email", "gmail_labeled"} <= cols
        assert "merged_into" in {r[1] for r in conn.execute("PRAGMA table_info(deadlines)")}
        row = conn.execute(
            "SELECT start_datetime FROM calendar_events WHERE event_id='e1'"
        ).fetchone()
        assert row[0] == "2026-09-22T17:00:00"  # normalised, not deleted
        assert conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1
    finally:
        db.configure(None)


def test_identifier_guard_rejects_unknown_names() -> None:
    assert assert_identifier("deadlines", ("deadlines",)) == "deadlines"
    with pytest.raises(ValueError):
        assert_identifier("deadlines; DROP TABLE emails", ("deadlines",))


# ── emails and extractions ──────────────────────────────────────────────────


def test_store_email_is_idempotent(home: Path) -> None:
    assert db.store_email({"id": "m1", "subject": "a", "recipients": ["x"]}) is True
    assert db.store_email({"id": "m1", "subject": "changed"}) is False
    assert len(db.get_unprocessed_emails()) == 1


def test_store_extractions_marks_processed_and_dedups(home: Path) -> None:
    db.store_email({"id": "m1", "subject": "invoice"})
    extraction = EmailExtraction(
        email_id="m1",
        entity_key="studio",
        summary="s",
        deadlines=[Deadline(entity_key="studio", description="Pay invoice", due_date=_d(3))],
        financial_items=[
            FinancialItem(entity_key="studio", direction="payable", amount=10, description="Inv")
        ],
        action_items=[ActionItem(entity_key="studio", description="Reply")],
    )
    assert db.store_extractions("m1", extraction) == 3
    assert db.get_unprocessed_emails() == []
    # Re-analysing the same email must not duplicate its items.
    assert db.store_extractions("m1", extraction) == 0
    assert db.get_connection().execute("SELECT COUNT(*) FROM deadlines").fetchone()[0] == 1


def test_sync_state_round_trip(home: Path) -> None:
    assert db.get_sync_state("k") is None
    db.set_sync_state("k", "v")
    assert db.get_sync_state("k") == "v"


# ── calendar ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected_len"),
    [
        ("2026-09-22T17:00:00.0000000", 19),  # Graph precision
        ("2026-09-22T17:00:00+00:00", 19),  # offset -> local naive
        ("2026-09-22T17:00:00", 19),
        (None, 0),
    ],
)
def test_normalize_datetime(raw: str | None, expected_len: int) -> None:
    out = normalize_datetime(raw)
    assert (len(out) if out else 0) == expected_len
    if out:
        assert "+" not in out and "." not in out


def test_store_calendar_events_normalises_and_keeps_attendees_as_list(home: Path) -> None:
    db.store_calendar_events(
        [
            {
                "event_id": "e1",
                "title": "Call",
                "start_datetime": f"{_d(1)}T10:00:00.0000000",
                "attendees": "a@x.com, b@y.com",
            },
            {"event_id": "e2", "title": "Past", "start_datetime": f"{_d(-5)}T10:00:00"},
        ]
    )
    upcoming = db.get_upcoming_events(days_ahead=7)
    assert [e["event_id"] for e in upcoming] == ["e1"]
    assert upcoming[0]["attendees"] == '["a@x.com", "b@y.com"]'


# ── bulletin window rules ───────────────────────────────────────────────────


def _deadline(desc: str, due: str | None) -> None:
    db.get_connection().execute(
        "INSERT INTO deadlines (entity_key, description, due_date) VALUES ('studio', ?, ?)",
        (desc, due),
    )


def _action(desc: str, due: str | None, source: str | None, assigned: str | None = None) -> None:
    db.get_connection().execute(
        "INSERT INTO action_items (entity_key, description, due_date, source_date, assigned_to) VALUES ('studio', ?, ?, ?, ?)",
        (desc, due, source, assigned),
    )


def test_dated_items_show_from_grace_window_through_horizon(home: Path) -> None:
    _deadline("too old", _d(-4))
    _deadline("overdue but in grace", _d(-2))
    _deadline("today", _d(0))
    _deadline("edge of horizon", _d(14))
    _deadline("beyond horizon", _d(15))
    board = db.get_bulletin_items(days_ahead=14, overdue_grace_days=3)
    assert [d["description"] for d in board["deadlines"]] == [
        "overdue but in grace",
        "today",
        "edge of horizon",
    ]


def test_undated_items_age_off_after_ttl(home: Path) -> None:
    _action("fresh", None, _d(-2))
    _action("stale", None, _d(-8))
    board = db.get_bulletin_items(undated_ttl_days=7, owner_name="Sam")
    assert [a["description"] for a in board["action_items"]] == ["fresh"]


def test_action_items_assigned_to_someone_else_stay_off_the_board(home: Path) -> None:
    _action("mine by name", None, _d(-1), "Sam Rivera")
    _action("mine by owner", None, _d(-1), "owner")
    _action("unassigned", None, _d(-1), None)
    _action("theirs", None, _d(-1), "Priya")
    board = db.get_bulletin_items(owner_name="Sam")
    assert {a["description"] for a in board["action_items"]} == {
        "mine by name",
        "mine by owner",
        "unassigned",
    }


def test_tasks_undated_ttl_is_two_weeks(home: Path) -> None:
    fresh = db.add_task("fresh")
    db.get_connection().execute(
        "INSERT INTO tasks (title, created_at) VALUES ('old', ?)", (f"{_d(-15)}T00:00:00",)
    )
    board = db.get_bulletin_items()
    assert [t["id"] for t in board["tasks"]] == [fresh]


def test_follow_ups_require_recent_detection_and_min_wait(home: Path) -> None:
    db.upsert_follow_up(
        {"thread_id": "t1", "subject": "young", "recipient": "a@x.com", "days_waiting": 1}
    )
    db.upsert_follow_up(
        {"thread_id": "t2", "subject": "ripe", "recipient": "b@x.com", "days_waiting": 5}
    )
    db.upsert_follow_up(
        {"thread_id": "t3", "subject": "ancient", "recipient": "c@x.com", "days_waiting": 40}
    )
    db.upsert_follow_up(
        {
            "thread_id": "t4",
            "subject": "note to self",
            "recipient": "sam@example.com",
            "days_waiting": 6,
        }
    )
    db.get_connection().execute(
        "UPDATE follow_ups SET updated_at = ? WHERE thread_id = 't2'", (f"{_d(-1)}T00:00:00",)
    )
    board = db.get_bulletin_items(exclude_recipients=("sam@example.com",))
    assert [f["subject"] for f in board["follow_ups"]] == ["ripe"]
    # A follow-up not re-detected within two days drops off.
    db.get_connection().execute(
        "UPDATE follow_ups SET updated_at = ? WHERE thread_id = 't2'", (f"{_d(-3)}T00:00:00",)
    )
    assert db.get_bulletin_items(exclude_recipients=("sam@example.com",))["follow_ups"] == []


def test_board_rows_are_trimmed_to_prompt_fields(home: Path) -> None:
    _deadline("d", _d(1))
    row = db.get_bulletin_items()["deadlines"][0]
    assert set(row) <= {"id", "entity_key", "description", "due_date", "priority"}
    assert "created_at" not in row


# ── reconciliation ──────────────────────────────────────────────────────────


def test_apply_item_merge_keeps_duplicates_reversible(home: Path) -> None:
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO financial_items (direction, counterparty, amount, description, due_date) VALUES ('receivable','A',10,'inv',?)",
        (_d(1),),
    )
    conn.execute(
        "INSERT INTO financial_items (direction, counterparty, amount, description, due_date) VALUES ('receivable','A',12,'inv reminder',?)",
        (_d(1),),
    )
    merged = db.apply_item_merge(
        "financial_items", 2, [1], {"amount": 12, "status": "hacked", "description": "Invoice A"}
    )
    assert merged == 1
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM financial_items")}
    assert rows[1]["status"] == "merged" and rows[1]["merged_into"] == 2
    assert (
        rows[2]["description"] == "Invoice A" and rows[2]["status"] == "pending"
    )  # disallowed field ignored
    assert len(db.get_bulletin_items()["financial_items"]) == 1


def test_apply_item_merge_rejects_unknown_table(home: Path) -> None:
    with pytest.raises(ValueError):
        db.apply_item_merge("emails", 1, [2], {})


def test_get_reconcilable_items_only_returns_board_items(home: Path) -> None:
    _deadline("on board", _d(1))
    _deadline("off board", _d(60))
    board = db.get_bulletin_items()
    items = db.get_reconcilable_items(board)
    assert [d["description"] for d in items["deadlines"]] == ["on board"]
    assert items["financial_items"] == []


# ── status summary and horizon ──────────────────────────────────────────────


def test_status_summary_counts_owner_actions_only(home: Path) -> None:
    _action("mine", None, _d(0), "owner")
    _action("theirs", None, _d(0), "Priya")
    summary = db.get_status_summary("Sam")
    assert summary["pending_actions"] == 1
    assert summary["total_emails"] == 0


def test_horizon_data_returns_every_pending_kind(home: Path) -> None:
    db.store_calendar_events(
        [{"event_id": "e1", "title": "x", "start_date": _d(2), "all_day": True}]
    )
    _deadline("d", _d(2))
    data = db.get_horizon_data(days_ahead=7, owner_name="Sam")
    assert {"events", "deadlines", "financial_items", "action_items", "tasks", "follow_ups"} <= set(
        data
    )
    assert len(data["events"]) == 1 and len(data["deadlines"]) == 1
