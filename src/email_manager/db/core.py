"""Connection handling and versioned schema migrations.

One connection per process, opened lazily against a configurable path so tests
can point it at a temporary file. Migrations are numbered and recorded in
SQLite's ``PRAGMA user_version``; each runs in its own transaction and any
failure raises instead of being swallowed, so a half-applied migration is
visible immediately rather than surfacing later as a confusing "no such column".
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from email_manager import paths

log = logging.getLogger(__name__)

_db_path: Path | None = None
_conn: sqlite3.Connection | None = None


def configure(path: Path | None) -> None:
    """Point the tracker database at ``path`` (or back at the default when None).

    Closes any open connection. Tests call this with a temporary path.
    """
    global _db_path
    close()
    _db_path = Path(path) if path is not None else None


def configured_path() -> Path | None:
    """The explicit path set by :func:`configure`, or None for the default location."""
    return _db_path


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def get_connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = _db_path or paths.tracker_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit mode: the module issues its own BEGIN/COMMIT where a
        # multi-statement write must be atomic, and nothing else is left in a
        # half-open implicit transaction.
        _conn = sqlite3.connect(str(path), isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def utcnow_iso() -> str:
    """Timestamp string used for created/updated columns (UTC, no offset).

    SQLite's ``date('now')`` is UTC, so stored timestamps compare correctly
    against it without any conversion.
    """
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def assert_identifier(name: str, allowed: Iterable[str]) -> str:
    """Guard for the few places a table or column name is interpolated into SQL.

    Every such site passes the name through here with its allowlist, so the
    safety check sits next to the f-string it protects.
    """
    if name not in set(allowed):
        raise ValueError(f"Refusing to use {name!r} as an SQL identifier")
    return name


# ── Schema ──────────────────────────────────────────────────────────────────

_BASELINE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS emails (
        id TEXT PRIMARY KEY,
        thread_id TEXT,
        account_email TEXT,
        sender TEXT,
        recipients TEXT,
        subject TEXT,
        body_snippet TEXT,
        date TEXT,
        labels TEXT,
        entity_key TEXT,
        processed INTEGER DEFAULT 0,
        gmail_labeled INTEGER DEFAULT 0,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS agreements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email_id TEXT REFERENCES emails(id),
        entity_key TEXT,
        summary TEXT NOT NULL,
        parties TEXT,
        terms TEXT,
        status TEXT DEFAULT 'active',
        source_date TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS deadlines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email_id TEXT REFERENCES emails(id),
        entity_key TEXT,
        description TEXT NOT NULL,
        due_date TEXT,
        priority TEXT DEFAULT 'medium',
        status TEXT DEFAULT 'pending',
        source_date TEXT,
        merged_into INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS financial_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email_id TEXT REFERENCES emails(id),
        entity_key TEXT,
        direction TEXT NOT NULL,
        counterparty TEXT,
        amount REAL,
        currency TEXT DEFAULT 'USD',
        description TEXT,
        due_date TEXT,
        status TEXT DEFAULT 'pending',
        source_date TEXT,
        merged_into INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS action_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email_id TEXT REFERENCES emails(id),
        entity_key TEXT,
        description TEXT NOT NULL,
        assigned_to TEXT,
        due_date TEXT,
        priority TEXT DEFAULT 'medium',
        status TEXT DEFAULT 'pending',
        source_date TEXT,
        merged_into INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS sync_state (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS calendar_events (
        event_id TEXT PRIMARY KEY,
        calendar_id TEXT,
        calendar_name TEXT,
        account_email TEXT,
        title TEXT,
        description TEXT,
        location TEXT,
        start_date TEXT,
        start_datetime TEXT,
        end_date TEXT,
        end_datetime TEXT,
        all_day INTEGER DEFAULT 0,
        attendees TEXT,
        status TEXT DEFAULT 'confirmed',
        entity_key TEXT,
        fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        notes TEXT,
        entity_key TEXT DEFAULT 'personal',
        due_date TEXT,
        priority TEXT DEFAULT 'medium',
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS follow_ups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT UNIQUE NOT NULL,
        account_email TEXT,
        subject TEXT,
        recipient TEXT,
        last_sent_date TEXT,
        entity_key TEXT,
        days_waiting INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

"""

_BASELINE_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_emails_entity ON emails(entity_key);
    CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date);
    CREATE INDEX IF NOT EXISTS idx_emails_account ON emails(account_email);
    CREATE INDEX IF NOT EXISTS idx_emails_processed ON emails(processed);
    CREATE INDEX IF NOT EXISTS idx_deadlines_due ON deadlines(due_date);
    CREATE INDEX IF NOT EXISTS idx_deadlines_status ON deadlines(status);
    CREATE INDEX IF NOT EXISTS idx_financial_status ON financial_items(status);
    CREATE INDEX IF NOT EXISTS idx_financial_direction ON financial_items(direction);
    CREATE INDEX IF NOT EXISTS idx_action_status ON action_items(status);
    CREATE INDEX IF NOT EXISTS idx_cal_start ON calendar_events(start_datetime, start_date);
    CREATE INDEX IF NOT EXISTS idx_cal_account ON calendar_events(account_email);
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
    CREATE INDEX IF NOT EXISTS idx_followup_status ON follow_ups(status);
"""

# Columns that older databases (created before schema versioning) may lack.
_LEGACY_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("emails", "thread_id", "TEXT"),
    ("emails", "date", "TEXT"),
    ("emails", "entity_key", "TEXT"),
    ("emails", "account_email", "TEXT"),
    ("emails", "gmail_labeled", "INTEGER DEFAULT 0"),
    ("deadlines", "merged_into", "INTEGER"),
    ("financial_items", "merged_into", "INTEGER"),
    ("action_items", "merged_into", "INTEGER"),
)

_TABLES = (
    "emails",
    "agreements",
    "deadlines",
    "financial_items",
    "action_items",
    "sync_state",
    "calendar_events",
    "tasks",
    "follow_ups",
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    assert_identifier(table, _TABLES)
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migration_1_baseline(conn: sqlite3.Connection) -> None:
    """Create every table, backfill columns older databases lack, then build the indexes."""
    for statement in _BASELINE_SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)
    for table, column, definition in _LEGACY_COLUMNS:
        if column not in _columns(conn, table):
            assert_identifier(table, _TABLES)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    for statement in _BASELINE_INDEXES.split(";"):
        if statement.strip():
            conn.execute(statement)


def _migration_2_normalize_calendar_datetimes(conn: sqlite3.Connection) -> None:
    """Rewrite timezone-suffixed event datetimes as local naive ISO strings.

    Earlier versions deleted such rows on every start-up, which silently wiped
    every Outlook event (Graph returns 27-character timestamps). Normalising in
    place keeps the data and makes the store the single point of truth for the
    format; see :func:`email_manager.db.calendar.normalize_datetime`.
    """
    from email_manager.db.calendar import normalize_datetime

    rows = conn.execute(
        "SELECT event_id, start_datetime, end_datetime FROM calendar_events "
        "WHERE length(start_datetime) > 19 OR length(end_datetime) > 19"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE calendar_events SET start_datetime = ?, end_datetime = ? WHERE event_id = ?",
            (
                normalize_datetime(row["start_datetime"]),
                normalize_datetime(row["end_datetime"]),
                row["event_id"],
            ),
        )


MIGRATIONS: tuple[tuple[int, Callable[[sqlite3.Connection], None]], ...] = (
    (1, _migration_1_baseline),
    (2, _migration_2_normalize_calendar_datetimes),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


def init_db() -> None:
    """Create or upgrade the schema. Safe to call on every start-up; cheap when current."""
    conn = get_connection()
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    for version, migrate in MIGRATIONS:
        if version <= current:
            continue
        log.info("Applying tracker.db migration %d (%s)", version, migrate.__name__)
        try:
            conn.execute("BEGIN")
            migrate(conn)
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            log.error("Migration %d failed", version, exc_info=True)
            raise
