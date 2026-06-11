"""
SQLite storage for tracking which emails have been organized by the cleanup bot.
"""
import sqlite3
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = _APP_DIR / "data" / "organized.db"


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS organized (
            email_id     TEXT PRIMARY KEY,
            account_email TEXT,
            category     TEXT,
            action       TEXT,
            confidence   TEXT,
            reason       TEXT,
            organized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def is_email_organized(conn: sqlite3.Connection, email_ids: list[str]) -> set[str]:
    if not email_ids:
        return set()
    placeholders = ",".join("?" for _ in email_ids)
    rows = conn.execute(
        f"SELECT email_id FROM organized WHERE email_id IN ({placeholders})", email_ids
    ).fetchall()
    return {r[0] for r in rows}


def store_organized_email(conn: sqlite3.Connection, account_email: str, classifications: dict):
    for eid, info in classifications.items():
        conn.execute(
            "INSERT OR REPLACE INTO organized (email_id, account_email, category, action, confidence, reason) VALUES (?,?,?,?,?,?)",
            (eid, account_email, info.get("category"), info.get("action"), info.get("confidence"), info.get("reason")),
        )
    conn.commit()


def get_organized_summary(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT category, action, COUNT(*) FROM organized GROUP BY category, action ORDER BY COUNT(*) DESC"
    ).fetchall()
