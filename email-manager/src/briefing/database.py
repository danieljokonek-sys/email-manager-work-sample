"""
SQLite storage for tracked communications, agreements, deadlines, and finances.
"""
import sqlite3
import json
from datetime import datetime, date
from pathlib import Path
from typing import Optional


_APP_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = _APP_DIR / "data" / "tracker.db"

_conn: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    """Return a shared database connection (reused across calls)."""
    global _conn
    if _conn is not None:
        return _conn
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(DB_PATH))
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db():
    conn = get_connection()
    # Step 1: create core tables using only original columns (safe for existing DBs)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            thread_id TEXT,
            sender TEXT,
            recipients TEXT,
            subject TEXT,
            body_snippet TEXT,
            date TEXT,
            labels TEXT,
            entity_key TEXT,
            processed INTEGER DEFAULT 0,
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_emails_entity ON emails(entity_key);
        CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date);
        CREATE INDEX IF NOT EXISTS idx_deadlines_due ON deadlines(due_date);
        CREATE INDEX IF NOT EXISTS idx_deadlines_status ON deadlines(status);
        CREATE INDEX IF NOT EXISTS idx_financial_status ON financial_items(status);
        CREATE INDEX IF NOT EXISTS idx_financial_direction ON financial_items(direction);
        CREATE INDEX IF NOT EXISTS idx_action_status ON action_items(status);
    """)
    conn.commit()

    # Step 2: migrate — adds new columns and new tables to existing DBs
    _migrate(conn)
    # Connection is reused (singleton) — do not close


def _migrate(conn: sqlite3.Connection):
    """Add columns/tables that may be missing from older database versions."""
    existing_email = {row[1] for row in conn.execute("PRAGMA table_info(emails)")}
    migrations = [
        ("emails", "account_email", "TEXT"),
        ("emails", "is_sms_forward", "INTEGER DEFAULT 0"),
        ("emails", "gmail_labeled", "INTEGER DEFAULT 0"),
    ]
    for table, col, col_def in migrations:
        if col not in existing_email:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")

    # Add snoozed_until to all item tables
    snooze_tables = ["action_items", "deadlines", "financial_items", "agreements",
                     "tasks", "follow_ups"]
    for table in snooze_tables:
        try:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "snoozed_until" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN snoozed_until TEXT")
        except Exception:
            pass

    # Clear calendar events stored with old timezone-offset format (e.g. +00:00 or -07:00).
    # UTC-naive ISO datetimes are exactly 19 chars: '2026-04-08T17:00:00'
    # Anything longer has a timezone suffix and must be re-fetched.
    try:
        conn.execute(
            "DELETE FROM calendar_events WHERE start_datetime IS NOT NULL AND length(start_datetime) > 19"
        )
    except Exception:
        pass

    # Create calendar_events if it doesn't exist (older DBs won't have it)
    conn.executescript("""
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
        CREATE TABLE IF NOT EXISTS song_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT,
            entity_key TEXT DEFAULT 'chorus_crafters',
            client_name TEXT,
            client_email TEXT,
            client_phone TEXT,
            event_type TEXT,
            event_date TEXT,
            honoree_names TEXT,
            event_notes TEXT,
            song_style TEXT,
            song_story TEXT,
            reference_songs TEXT,
            revisions_included INTEGER DEFAULT 2,
            revisions_used INTEGER DEFAULT 0,
            price REAL,
            deposit_amount REAL,
            deposit_paid INTEGER DEFAULT 0,
            deposit_date TEXT,
            balance_due REAL,
            balance_paid INTEGER DEFAULT 0,
            balance_date TEXT,
            demo_delivered INTEGER DEFAULT 0,
            demo_date TEXT,
            final_delivered INTEGER DEFAULT 0,
            final_date TEXT,
            status TEXT DEFAULT 'inquiry',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Migrate song_orders table — add columns that newer code expects
    try:
        order_cols = {row[1] for row in conn.execute("PRAGMA table_info(song_orders)")}
        order_migrations = [
            ("email_id", "TEXT"),
            ("event_type", "TEXT"),
            ("honoree_names", "TEXT"),
            ("event_notes", "TEXT"),
            ("song_style", "TEXT"),
            ("song_story", "TEXT"),
            ("reference_songs", "TEXT"),
            ("revisions_included", "INTEGER DEFAULT 2"),
            ("revisions_used", "INTEGER DEFAULT 0"),
            ("price", "REAL"),
            ("deposit_date", "TEXT"),
            ("balance_date", "TEXT"),
        ]
        for col, col_def in order_migrations:
            if col not in order_cols:
                conn.execute(f"ALTER TABLE song_orders ADD COLUMN {col} {col_def}")
        # Migrate quote_amount → price if old column exists and price was just added
        if "quote_amount" in order_cols and "price" not in order_cols:
            conn.execute("UPDATE song_orders SET price = quote_amount WHERE price IS NULL")
        conn.commit()
    except Exception:
        pass

    # Indexes on new tables/columns (safe now that tables and columns exist)
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_emails_account ON emails(account_email);
        CREATE INDEX IF NOT EXISTS idx_cal_start ON calendar_events(start_datetime, start_date);
        CREATE INDEX IF NOT EXISTS idx_cal_account ON calendar_events(account_email);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON song_orders(status);
        CREATE INDEX IF NOT EXISTS idx_orders_event_date ON song_orders(event_date);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
        CREATE INDEX IF NOT EXISTS idx_followup_status ON follow_ups(status);
    """)
    conn.commit()


def store_email(email: dict):
    conn = get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO emails
           (id, thread_id, account_email, sender, recipients, subject,
            body_snippet, date, labels, entity_key, is_sms_forward)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            email["id"],
            email.get("thread_id"),
            email.get("account_email", ""),
            email.get("sender"),
            json.dumps(email.get("recipients", [])),
            email.get("subject"),
            email.get("body_snippet", "")[:2000],
            email.get("date"),
            json.dumps(email.get("labels", [])),
            email.get("entity_key"),
            1 if email.get("is_sms_forward") else 0,
        ),
    )
    conn.commit()
    # Connection is reused (singleton) — do not close


def store_calendar_events(events: list[dict]):
    """Upsert a list of calendar events."""
    conn = get_connection()
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
                ev.get("start_datetime"),
                ev.get("end_date"),
                ev.get("end_datetime"),
                1 if ev.get("all_day") else 0,
                json.dumps(ev.get("attendees", [])),
                ev.get("status", "confirmed"),
                ev.get("entity_key"),
            ),
        )
    conn.commit()
    # Connection is reused (singleton) — do not close


def get_upcoming_events(days_ahead: int = 14) -> list[dict]:
    """Return calendar events starting within the next N days."""
    conn = get_connection()
    # Use strftime with 'T' separator to match the ISO format we store
    # (datetime('now') uses a space separator which breaks string comparison)
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
        (days_ahead, days_ahead),
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def store_extractions(email_id: str, extractions: dict):
    """Store all extracted items from Claude's analysis."""
    conn = get_connection()

    for agreement in extractions.get("agreements", []):
        conn.execute(
            """INSERT INTO agreements (email_id, entity_key, summary, parties, terms, source_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                email_id,
                agreement.get("entity_key"),
                agreement["summary"],
                json.dumps(agreement.get("parties", [])),
                agreement.get("terms"),
                agreement.get("source_date"),
            ),
        )

    for deadline in extractions.get("deadlines", []):
        conn.execute(
            """INSERT INTO deadlines (email_id, entity_key, description, due_date, priority, source_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                email_id,
                deadline.get("entity_key"),
                deadline["description"],
                deadline.get("due_date"),
                deadline.get("priority", "medium"),
                deadline.get("source_date"),
            ),
        )

    for item in extractions.get("financial_items", []):
        conn.execute(
            """INSERT INTO financial_items
               (email_id, entity_key, direction, counterparty, amount, currency, description, due_date, source_date, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                email_id,
                item.get("entity_key"),
                item["direction"],
                item.get("counterparty"),
                item.get("amount"),
                item.get("currency", "USD"),
                item.get("description"),
                item.get("due_date"),
                item.get("source_date"),
                item.get("status", "pending"),
            ),
        )

    for action in extractions.get("action_items", []):
        conn.execute(
            """INSERT INTO action_items
               (email_id, entity_key, description, assigned_to, due_date, priority, source_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                email_id,
                action.get("entity_key"),
                action["description"],
                action.get("assigned_to"),
                action.get("due_date"),
                action.get("priority", "medium"),
                action.get("source_date"),
            ),
        )

    # Mark email as processed
    conn.execute("UPDATE emails SET processed = 1 WHERE id = ?", (email_id,))
    conn.commit()
    # Connection is reused (singleton) — do not close


def mark_email_processed(email_id: str):
    conn = get_connection()
    conn.execute("UPDATE emails SET processed = 1 WHERE id = ?", (email_id,))
    conn.commit()
    # Connection is reused (singleton) — do not close


def get_unprocessed_emails(limit: int = 100) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM emails WHERE processed = 0 ORDER BY date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def get_pending_deadlines(days_ahead: int = 14) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT d.*, e.subject as email_subject, e.sender as email_sender
           FROM deadlines d
           LEFT JOIN emails e ON d.email_id = e.id
           WHERE d.status = 'pending'
             AND d.due_date IS NOT NULL
             AND d.due_date <= date('now', '+' || ? || ' days')
             AND (d.snoozed_until IS NULL OR d.snoozed_until <= date('now'))
           ORDER BY d.due_date ASC""",
        (days_ahead,),
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def get_pending_financial(direction: Optional[str] = None) -> list[dict]:
    conn = get_connection()
    snooze_filter = "AND (f.snoozed_until IS NULL OR f.snoozed_until <= date('now'))"
    if direction:
        rows = conn.execute(
            f"""SELECT f.*, e.subject as email_subject, e.sender as email_sender
               FROM financial_items f
               LEFT JOIN emails e ON f.email_id = e.id
               WHERE f.status = 'pending' AND f.direction = ?
               {snooze_filter}
               ORDER BY f.due_date ASC NULLS LAST""",
            (direction,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT f.*, e.subject as email_subject, e.sender as email_sender
               FROM financial_items f
               LEFT JOIN emails e ON f.email_id = e.id
               WHERE f.status = 'pending'
               {snooze_filter}
               ORDER BY f.direction, f.due_date ASC NULLS LAST"""
        ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def get_active_agreements() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.*, e.subject as email_subject, e.sender as email_sender
           FROM agreements a
           LEFT JOIN emails e ON a.email_id = e.id
           WHERE a.status = 'active'
             AND (a.snoozed_until IS NULL OR a.snoozed_until <= date('now'))
           ORDER BY a.created_at DESC"""
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def get_pending_actions(owner_name: str = "Daniel") -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.*, e.subject as email_subject, e.sender as email_sender
           FROM action_items a
           LEFT JOIN emails e ON a.email_id = e.id
           WHERE a.status = 'pending'
             AND (a.snoozed_until IS NULL OR a.snoozed_until <= date('now'))
             AND (a.assigned_to IS NULL
                  OR lower(a.assigned_to) = 'owner'
                  OR lower(a.assigned_to) LIKE '%' || lower(?) || '%')
           ORDER BY a.priority DESC, a.due_date ASC NULLS LAST""",
        (owner_name,),
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def get_sync_state(key: str) -> Optional[str]:
    conn = get_connection()
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    # Connection is reused (singleton) — do not close
    return row["value"] if row else None


def set_sync_state(key: str, value: str):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, datetime.utcnow().isoformat()),
    )
    conn.commit()
    # Connection is reused (singleton) — do not close


def update_item_status(table: str, item_id: int, status: str):
    allowed_tables = {"agreements", "deadlines", "financial_items", "action_items", "tasks", "follow_ups"}
    if table not in allowed_tables:
        raise ValueError(f"Invalid table: {table}")
    conn = get_connection()
    conn.execute(f"UPDATE {table} SET status = ? WHERE id = ?", (status, item_id))
    conn.commit()
    # Connection is reused (singleton) — do not close


# ── Song Orders (Chorus Crafters) ─────────────────────────────────────────────

ACTIVE_ORDER_STATUSES = (
    "inquiry", "quoted", "deposit_received",
    "in_production", "revision_requested", "in_revision", "delivered",
)


def upsert_song_order(order: dict) -> int:
    """Insert or update a song order. Returns the row id."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()

    # Try to match an existing order by client email + event date, or client name
    existing_id = None
    if order.get("client_email") and order.get("event_date"):
        row = conn.execute(
            "SELECT id FROM song_orders WHERE client_email = ? AND event_date = ?",
            (order["client_email"], order["event_date"]),
        ).fetchone()
        existing_id = row["id"] if row else None

    if not existing_id and order.get("client_name") and order.get("event_date"):
        row = conn.execute(
            "SELECT id FROM song_orders WHERE client_name = ? AND event_date = ?",
            (order["client_name"], order["event_date"]),
        ).fetchone()
        existing_id = row["id"] if row else None

    if existing_id:
        # Update non-null fields only
        fields = [
            "client_name", "client_email", "client_phone",
            "event_type", "event_date", "honoree_names", "event_notes",
            "song_style", "song_story", "reference_songs",
            "revisions_included", "price", "deposit_amount",
            "balance_due", "notes", "status",
        ]
        updates = {f: order[f] for f in fields if f in order and order[f] is not None}
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE song_orders SET {set_clause}, updated_at = ? WHERE id = ?",
                [*updates.values(), now, existing_id],
            )
        conn.commit()
        # Connection is reused (singleton) — do not close
        return existing_id
    else:
        cur = conn.execute(
            """INSERT INTO song_orders (
                email_id, client_name, client_email, client_phone,
                event_type, event_date, honoree_names, event_notes,
                song_style, song_story, reference_songs,
                revisions_included, price, deposit_amount, balance_due,
                status, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.get("email_id"),
                order.get("client_name"),
                order.get("client_email"),
                order.get("client_phone"),
                order.get("event_type"),
                order.get("event_date"),
                order.get("honoree_names"),
                order.get("event_notes"),
                order.get("song_style"),
                order.get("song_story"),
                json.dumps(order.get("reference_songs", [])),
                order.get("revisions_included", 2),
                order.get("price"),
                order.get("deposit_amount"),
                order.get("balance_due"),
                order.get("status", "inquiry"),
                order.get("notes"),
                now,
                now,
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        # Connection is reused (singleton) — do not close
        return new_id


def update_order(order_id: int, fields: dict):
    """Update specific fields on a song order."""
    allowed = {
        "client_name", "client_email", "client_phone",
        "event_type", "event_date", "honoree_names", "event_notes",
        "song_style", "song_story", "reference_songs",
        "revisions_included", "revisions_used",
        "price", "deposit_amount", "deposit_paid", "deposit_date",
        "balance_due", "balance_paid", "balance_date",
        "demo_delivered", "demo_date", "final_delivered", "final_date",
        "status", "notes",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    conn = get_connection()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE song_orders SET {set_clause}, updated_at = ? WHERE id = ?",
        [*updates.values(), datetime.utcnow().isoformat(), order_id],
    )
    conn.commit()
    # Connection is reused (singleton) — do not close


def get_order(order_id: int) -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM song_orders WHERE id = ?", (order_id,)).fetchone()
    # Connection is reused (singleton) — do not close
    return dict(row) if row else None


def get_active_orders() -> list[dict]:
    placeholders = ",".join("?" * len(ACTIVE_ORDER_STATUSES))
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM song_orders WHERE status IN ({placeholders}) ORDER BY event_date ASC NULLS LAST",
        ACTIVE_ORDER_STATUSES,
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def get_orders_by_status(status: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM song_orders WHERE status = ? ORDER BY event_date ASC NULLS LAST",
        (status,),
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def get_all_orders(include_closed: bool = False) -> list[dict]:
    conn = get_connection()
    if include_closed:
        rows = conn.execute(
            "SELECT * FROM song_orders ORDER BY event_date ASC NULLS LAST"
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(ACTIVE_ORDER_STATUSES))
        rows = conn.execute(
            f"SELECT * FROM song_orders WHERE status IN ({placeholders}) ORDER BY event_date ASC NULLS LAST",
            ACTIVE_ORDER_STATUSES,
        ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def get_orders_pipeline_summary() -> dict:
    """Revenue and count breakdown by status for the Chorus Crafters pipeline."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT status,
                  COUNT(*) as count,
                  COALESCE(SUM(price), 0) as total_value,
                  COALESCE(SUM(CASE WHEN deposit_paid = 1 THEN deposit_amount ELSE 0 END), 0) as deposits_collected,
                  COALESCE(SUM(CASE WHEN balance_paid = 1 THEN balance_due ELSE 0 END), 0) as balances_collected
           FROM song_orders
           WHERE status NOT IN ('cancelled')
           GROUP BY status"""
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return {r["status"]: dict(r) for r in rows}


def get_dashboard_summary() -> dict:
    """Get counts for the status dashboard."""
    conn = get_connection()

    placeholders = ",".join("?" * len(ACTIVE_ORDER_STATUSES))
    active_orders_row = conn.execute(
        f"SELECT COUNT(*) c, COALESCE(SUM(price),0) v FROM song_orders WHERE status IN ({placeholders})",
        ACTIVE_ORDER_STATUSES,
    ).fetchone()

    summary = {
        "total_emails": conn.execute("SELECT COUNT(*) c FROM emails").fetchone()["c"],
        "unprocessed_emails": conn.execute(
            "SELECT COUNT(*) c FROM emails WHERE processed = 0"
        ).fetchone()["c"],
        "active_agreements": conn.execute(
            "SELECT COUNT(*) c FROM agreements WHERE status = 'active'"
        ).fetchone()["c"],
        "pending_deadlines": conn.execute(
            "SELECT COUNT(*) c FROM deadlines WHERE status = 'pending'"
        ).fetchone()["c"],
        "money_owed_to_you": conn.execute(
            "SELECT COALESCE(SUM(amount), 0) c FROM financial_items WHERE direction = 'receivable' AND status = 'pending'"
        ).fetchone()["c"],
        "money_you_owe": conn.execute(
            "SELECT COALESCE(SUM(amount), 0) c FROM financial_items WHERE direction = 'payable' AND status = 'pending'"
        ).fetchone()["c"],
        "pending_actions": conn.execute(
            """SELECT COUNT(*) c FROM action_items WHERE status = 'pending'
               AND (assigned_to IS NULL OR lower(assigned_to) = 'owner'
                    OR lower(assigned_to) LIKE '%daniel%')"""
        ).fetchone()["c"],
        "active_song_orders": active_orders_row["c"],
        "song_orders_pipeline_value": active_orders_row["v"],
        "upcoming_events_7d": conn.execute(
            """SELECT COUNT(*) c FROM calendar_events
               WHERE status != 'cancelled'
                 AND (
                   (start_datetime IS NOT NULL
                    AND start_datetime >= strftime('%Y-%m-%dT%H:%M:%S', 'now')
                    AND start_datetime <= strftime('%Y-%m-%dT%H:%M:%S', 'now', '+7 days'))
                   OR
                   (start_date IS NOT NULL AND start_datetime IS NULL
                    AND start_date >= date('now')
                    AND start_date <= date('now', '+7 days'))
                 )"""
        ).fetchone()["c"],
        "pending_tasks": conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE status = 'pending'"
        ).fetchone()["c"],
        "follow_ups_waiting": conn.execute(
            "SELECT COUNT(*) c FROM follow_ups WHERE status = 'pending'"
        ).fetchone()["c"],
        "stale_actions": conn.execute(
            """SELECT COUNT(*) c FROM action_items
               WHERE status = 'pending'
                 AND created_at <= datetime('now', '-3 days')"""
        ).fetchone()["c"],
    }
    # Connection is reused (singleton) — do not close
    return summary


# ── Floating Tasks ────────────────────────────────────────────────────────────

def add_task(title: str, notes: str = None, entity_key: str = "personal",
             due_date: str = None, priority: str = "medium") -> int:
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """INSERT INTO tasks (title, notes, entity_key, due_date, priority, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, notes, entity_key, due_date, priority, now, now),
    )
    conn.commit()
    task_id = cur.lastrowid
    # Connection is reused (singleton) — do not close
    return task_id


def get_pending_tasks() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM tasks WHERE status = 'pending'
             AND (snoozed_until IS NULL OR snoozed_until <= date('now'))
           ORDER BY
             CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
             due_date ASC NULLS LAST,
             created_at ASC"""
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def complete_task(task_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), task_id),
    )
    conn.commit()
    # Connection is reused (singleton) — do not close


def delete_task(task_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    # Connection is reused (singleton) — do not close


# ── Follow-up Detection ───────────────────────────────────────────────────────

def upsert_follow_up(fu: dict):
    """Insert or update a follow-up record (keyed on thread_id)."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
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
            fu["thread_id"], fu.get("account_email"), fu.get("subject"),
            fu.get("recipient"), fu.get("last_sent_date"), fu.get("entity_key"),
            fu.get("days_waiting", 0), now, now,
        ),
    )
    conn.commit()
    # Connection is reused (singleton) — do not close


def mark_follow_up_replied(thread_id: str):
    conn = get_connection()
    conn.execute(
        "UPDATE follow_ups SET status = 'replied', updated_at = ? WHERE thread_id = ?",
        (datetime.utcnow().isoformat(), thread_id),
    )
    conn.commit()
    # Connection is reused (singleton) — do not close


def dismiss_follow_up(follow_up_id: int):
    conn = get_connection()
    conn.execute(
        "UPDATE follow_ups SET status = 'dismissed', updated_at = ? WHERE id = ?",
        (datetime.utcnow().isoformat(), follow_up_id),
    )
    conn.commit()
    # Connection is reused (singleton) — do not close


def get_pending_follow_ups() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM follow_ups WHERE status = 'pending'
             AND (snoozed_until IS NULL OR snoozed_until <= date('now'))
           ORDER BY days_waiting DESC"""
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


# ── Stale Action Items (Nag Mode) ─────────────────────────────────────────────

def get_stale_action_items(days: int = 3, owner_name: str = "Daniel") -> list[dict]:
    """Return action items that have been pending for more than N days."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.*, e.subject as email_subject,
                  CAST(julianday('now') - julianday(a.created_at) AS INTEGER) as days_old
           FROM action_items a
           LEFT JOIN emails e ON a.email_id = e.id
           WHERE a.status = 'pending'
             AND a.created_at <= datetime('now', '-' || ? || ' days')
             AND (a.snoozed_until IS NULL OR a.snoozed_until <= date('now'))
             AND (a.assigned_to IS NULL
                  OR lower(a.assigned_to) = 'owner'
                  OR lower(a.assigned_to) LIKE '%' || lower(?) || '%')
           ORDER BY a.created_at ASC""",
        (days, owner_name),
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


# ── Auto-labeling ─────────────────────────────────────────────────────────────

def get_unlabeled_emails(limit: int = 200) -> list[dict]:
    """Return processed emails that haven't been labeled in Gmail yet."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, account_email, entity_key FROM emails
           WHERE processed = 1 AND gmail_labeled = 0
             AND entity_key IS NOT NULL AND entity_key != 'personal'
           ORDER BY date DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    # Connection is reused (singleton) — do not close
    return [dict(r) for r in rows]


def mark_emails_labeled(email_ids: list[str]):
    if not email_ids:
        return
    conn = get_connection()
    placeholders = ",".join("?" * len(email_ids))
    conn.execute(
        f"UPDATE emails SET gmail_labeled = 1 WHERE id IN ({placeholders})",
        email_ids,
    )
    conn.commit()
    # Connection is reused (singleton) — do not close


# ── 14-Day Horizon ────────────────────────────────────────────────────────────

def get_horizon_data(days_ahead: int = 14, owner_name: str = "Daniel") -> dict:
    """Return upcoming events plus all pending items for cross-referencing."""
    events = get_upcoming_events(days_ahead=days_ahead)
    conn = get_connection()

    action_items = [dict(r) for r in conn.execute(
        """SELECT a.*, e.subject as email_subject
           FROM action_items a LEFT JOIN emails e ON a.email_id = e.id
           WHERE a.status = 'pending'
             AND (a.assigned_to IS NULL
                  OR lower(a.assigned_to) = 'owner'
                  OR lower(a.assigned_to) LIKE '%' || lower(?) || '%')
           ORDER BY a.due_date ASC NULLS LAST""",
        (owner_name,),
    ).fetchall()]

    financial_items = [dict(r) for r in conn.execute(
        """SELECT f.*, e.subject as email_subject
           FROM financial_items f LEFT JOIN emails e ON f.email_id = e.id
           WHERE f.status = 'pending'
           ORDER BY f.due_date ASC NULLS LAST"""
    ).fetchall()]

    deadlines = [dict(r) for r in conn.execute(
        """SELECT d.*, e.subject as email_subject
           FROM deadlines d LEFT JOIN emails e ON d.email_id = e.id
           WHERE d.status = 'pending'
           ORDER BY d.due_date ASC"""
    ).fetchall()]

    follow_ups = [dict(r) for r in conn.execute(
        "SELECT * FROM follow_ups WHERE status = 'pending' ORDER BY days_waiting DESC"
    ).fetchall()]

    tasks = [dict(r) for r in conn.execute(
        """SELECT * FROM tasks WHERE status = 'pending'
           ORDER BY due_date ASC NULLS LAST"""
    ).fetchall()]

    # Connection is reused (singleton) — do not close
    return {
        "events": events,
        "action_items": action_items,
        "financial_items": financial_items,
        "deadlines": deadlines,
        "follow_ups": follow_ups,
        "tasks": tasks,
    }


# ── Snooze ────────────────────────────────────────────────────────────────────

SNOOZEABLE_TABLES = {
    "action_items", "deadlines", "financial_items",
    "agreements", "tasks", "follow_ups",
}


def _parse_snooze_until(duration: str) -> str:
    """Parse a snooze duration like '3d', '1w', '2026-05-01' into a date string."""
    duration = duration.strip().lower()
    today = date.today()
    if duration.endswith("d"):
        days = int(duration[:-1])
        return (today + __import__("datetime").timedelta(days=days)).isoformat()
    if duration.endswith("w"):
        weeks = int(duration[:-1])
        return (today + __import__("datetime").timedelta(weeks=weeks)).isoformat()
    # Assume it's already a YYYY-MM-DD date
    date.fromisoformat(duration)  # validate
    return duration


def snooze_item(table: str, item_id: int, until: str):
    """Snooze an item until a given date. `until` can be '3d', '1w', or 'YYYY-MM-DD'."""
    if table not in SNOOZEABLE_TABLES:
        raise ValueError(f"Cannot snooze table: {table}")
    snooze_date = _parse_snooze_until(until)
    conn = get_connection()
    conn.execute(
        f"UPDATE {table} SET snoozed_until = ? WHERE id = ?",
        (snooze_date, item_id),
    )
    conn.commit()
    # Connection is reused (singleton) — do not close
    return snooze_date


def unsnooze_item(table: str, item_id: int):
    """Clear snooze on an item so it reappears immediately."""
    if table not in SNOOZEABLE_TABLES:
        raise ValueError(f"Cannot unsnooze table: {table}")
    conn = get_connection()
    conn.execute(f"UPDATE {table} SET snoozed_until = NULL WHERE id = ?", (item_id,))
    conn.commit()
    # Connection is reused (singleton) — do not close


def get_snoozed_items() -> list[dict]:
    """Return all currently snoozed items across all tables."""
    conn = get_connection()
    results = []
    for table in sorted(SNOOZEABLE_TABLES):
        try:
            rows = conn.execute(
                f"""SELECT id, '{table}' as source_table, snoozed_until,
                           CASE
                             WHEN snoozed_until <= date('now') THEN 'expired'
                             ELSE 'active'
                           END as snooze_state
                    FROM {table}
                    WHERE snoozed_until IS NOT NULL
                    ORDER BY snoozed_until ASC"""
            ).fetchall()
            # Pull a label field for display
            for row in rows:
                item = dict(row)
                # Try to get a description/title for display
                label_row = conn.execute(
                    f"SELECT * FROM {table} WHERE id = ?", (item["id"],)
                ).fetchone()
                if label_row:
                    d = dict(label_row)
                    item["label"] = (
                        d.get("description") or d.get("title") or
                        d.get("subject") or d.get("summary") or f"#{item['id']}"
                    )
                results.append(item)
        except Exception:
            pass
    # Connection is reused (singleton) — do not close
    return results
