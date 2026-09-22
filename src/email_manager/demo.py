"""Seed a throwaway database with sample data and render a board from it.

This is the fastest way to see what the tool produces without connecting a
mailbox: one Claude call, no email accounts, nothing written to the real
database. The same data set is used by the test suite.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from email_manager import db, pipeline
from email_manager.config import Config
from email_manager.llm import ClaudeClient
from email_manager.schemas import ActionItem, Deadline, EmailExtraction, FinancialItem

DEMO_CONFIG = Config.model_validate(
    {
        "owner": {
            "name": "Sam Rivera",
            "profile": "Freelance designer running a small studio and managing one rental property.",
            "briefing_priorities": "Client deadlines and anything with money attached come first.",
        },
        "accounts": [{"name": "Studio", "email": "sam@example.com", "provider": "gmail"}],
        "digest": {"send_to": "sam@example.com", "days_ahead": 14},
        "entities": {
            "studio": {
                "name": "Rivera Studio",
                "description": "Design studio. Clients commission brand and web work.",
                "keywords": ["studio", "brand", "logo", "website"],
            },
            "rental": {
                "name": "Elm Street Rental",
                "description": "A rental property. Revenue is rent; expenses are repairs and taxes.",
                "keywords": ["rent", "tenant", "lease", "elm street"],
            },
        },
    }
)


def _d(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _dt(days: int, hour: int) -> str:
    return f"{_d(days)}T{hour:02d}:00:00"


def seed(config: Config = DEMO_CONFIG) -> None:
    """Insert a realistic week of items into the current database."""
    db.init_db()
    db.store_calendar_events(
        [
            {"event_id": "ev1", "calendar_name": "Work", "account_email": "sam@example.com", "title": "Brand review day with Northwind Coffee", "start_date": _d(0), "all_day": True, "entity_key": "studio"},
            {"event_id": "ev2", "calendar_name": "Work", "account_email": "sam@example.com", "title": "Site walkthrough, Elm Street", "start_datetime": _dt(3, 10), "end_datetime": _dt(3, 11), "entity_key": "rental"},
            {"event_id": "ev3", "calendar_name": "Personal", "account_email": "sam@example.com", "title": "Dentist", "start_datetime": _dt(6, 9), "end_datetime": _dt(6, 10)},
            {"event_id": "ev4", "calendar_name": "Work", "account_email": "sam@example.com", "title": "Launch day: Northwind website", "start_date": _d(12), "all_day": True, "entity_key": "studio"},
        ]
    )  # fmt: skip

    emails = [
        ("m1", "Northwind Coffee <ops@northwind.example>", "Invoice #1042 reminder", _d(-2)),
        ("m2", "Northwind Coffee <ops@northwind.example>", "Re: Invoice #1042 (past due)", _d(-1)),
        ("m3", "Jordan Lee <jordan@tenant.example>", "Kitchen faucet leaking", _d(-1)),
        ("m4", "City Utilities <billing@utilities.example>", "Your statement is ready", _d(-3)),
        ("m5", "Priya Shah <priya@halcyon.example>", "Logo concepts: which direction?", _d(-4)),
    ]
    for email_id, sender, subject, sent in emails:
        db.store_email(
            {
                "id": email_id,
                "account_email": "sam@example.com",
                "sender": sender,
                "subject": subject,
                "date": sent,
                "body_snippet": "",
            }
        )

    extractions = {
        "m1": EmailExtraction(email_id="m1", entity_key="studio", summary="Invoice reminder", financial_items=[
            FinancialItem(entity_key="studio", direction="receivable", counterparty="Northwind Coffee", amount=2400, description="Invoice #1042, brand identity phase 1", due_date=_d(-2), source_date=_d(-2)),
        ]),
        "m2": EmailExtraction(email_id="m2", entity_key="studio", summary="Past-due notice", financial_items=[
            FinancialItem(entity_key="studio", direction="receivable", counterparty="Northwind Coffee", amount=2400, description="Invoice 1042 past due, brand identity", due_date=_d(-2), source_date=_d(-1)),
        ]),
        "m3": EmailExtraction(email_id="m3", entity_key="rental", summary="Tenant repair request", action_items=[
            ActionItem(entity_key="rental", description="Schedule plumber for the kitchen faucet at Elm Street", assigned_to="owner", due_date=_d(2), priority="high", source_date=_d(-1)),
        ]),
        "m4": EmailExtraction(email_id="m4", entity_key="rental", summary="Utility statement", financial_items=[
            FinancialItem(entity_key="rental", direction="payable", counterparty="City Utilities", amount=186.40, description="Statement balance, Elm Street water and power", due_date=_d(9), source_date=_d(-3)),
        ]),
        "m5": EmailExtraction(email_id="m5", entity_key="studio", summary="Client waiting on a decision", deadlines=[
            Deadline(entity_key="studio", description="Send Priya the chosen logo direction", due_date=_d(1), priority="high", source_date=_d(-4)),
        ]),
    }  # fmt: skip
    for email_id, extraction in extractions.items():
        db.store_extractions(email_id, extraction)

    db.add_task("Renew studio insurance", entity_key="studio", due_date=_d(7), priority="medium")
    db.add_task("Read the new lease template", entity_key="rental")
    db.upsert_follow_up(
        {
            "thread_id": "t9",
            "account_email": "sam@example.com",
            "subject": "Quote for spring photo shoot",
            "recipient": "alex@photos.example",
            "last_sent_date": _d(-5),
            "entity_key": "studio",
            "days_waiting": 5,
        }
    )


def run_demo(claude_factory: Callable[[str], ClaudeClient], out_dir: Path | None = None) -> Path:
    """Seed a temp database, build the board with one Claude call, write the HTML. Returns the path."""
    config = DEMO_CONFIG
    previous = db.configured_path()
    with tempfile.TemporaryDirectory() as tmp:
        db.configure(Path(tmp) / "demo.db")
        try:
            seed(config)
            result = pipeline.build_digest(
                config, claude_factory(config.analysis.model), dry_run=True
            )
        finally:
            db.configure(previous)
    assert result.written_to is not None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "sample-digest.html"
        target.write_text(result.written_to.read_text(encoding="utf-8"), encoding="utf-8")
        return target
    return result.written_to
