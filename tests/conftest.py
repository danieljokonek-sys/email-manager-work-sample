"""Shared fixtures: a temporary app home, a fake Anthropic SDK, and a fake mailbox."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from email_manager import db
from email_manager.config import Config
from email_manager.db import cleanup_ledger
from email_manager.email_client import EmailClient, EmailFilter, EmailMessage, Page
from email_manager.llm import ClaudeClient, UsageTracker

# ── app home ────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point every path the app uses at a temporary directory with a fresh database."""
    monkeypatch.setenv("EMAIL_MANAGER_HOME", str(tmp_path))
    db.configure(tmp_path / "data" / "tracker.db")
    cleanup_ledger.configure(tmp_path / "data" / "organized.db")
    db.init_db()
    yield tmp_path
    db.configure(None)
    cleanup_ledger.configure(None)


@pytest.fixture
def config() -> Config:
    return Config.model_validate(
        {
            "owner": {
                "name": "Sam Rivera",
                "profile": "Designer.",
                "briefing_priorities": "Money first.",
            },
            "accounts": [
                {"name": "Studio", "email": "sam@example.com", "provider": "gmail"},
                {"name": "Personal", "email": "sam.home@example.com", "provider": "gmail"},
            ],
            "digest": {"send_to": "sam@example.com", "send_from_account": "Personal"},
            "entities": {
                "studio": {
                    "name": "Rivera Studio",
                    "description": "Design studio",
                    "keywords": ["studio", "logo"],
                },
                "rental": {
                    "name": "Elm Street Rental",
                    "description": "Rental",
                    "keywords": ["rent", "tenant"],
                },
            },
            "cleanup": {"accounts": ["sam@example.com"], "batch_size": 3},
            "analysis": {"batch_size": 2},
        }
    )


# ── fake Anthropic SDK ──────────────────────────────────────────────────────


def _usage(**overrides: int) -> SimpleNamespace:
    base = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeMessages:
    """Stands in for ``client.messages``. Records every call; answers via handlers."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.parse_handler: Callable[[type[BaseModel], dict[str, Any]], Any] | None = None
        self.text_handler: Callable[[dict[str, Any]], str] | None = None
        self.stop_reason = "end_turn"
        self.usage_overrides: dict[str, int] = {}

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"kind": "parse", **kwargs})
        schema = kwargs["output_format"]
        parsed = self.parse_handler(schema, kwargs) if self.parse_handler else None
        return SimpleNamespace(
            parsed_output=parsed,
            stop_reason=self.stop_reason,
            model=kwargs["model"],
            usage=_usage(**self.usage_overrides),
            content=[],
        )

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"kind": "create", **kwargs})
        text = (
            self.text_handler(kwargs) if self.text_handler else "<h2>Today</h2><ul><li>ok</li></ul>"
        )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=self.stop_reason,
            model=kwargs["model"],
            usage=_usage(**self.usage_overrides),
        )


class FakeAnthropic:
    def __init__(self) -> None:
        self.messages = FakeMessages()


@pytest.fixture
def fake_sdk() -> FakeAnthropic:
    return FakeAnthropic()


@pytest.fixture
def claude(fake_sdk: FakeAnthropic) -> ClaudeClient:
    return ClaudeClient(model="claude-sonnet-5", client=fake_sdk, tracker=UsageTracker())  # type: ignore[arg-type]


# ── fake mailbox ────────────────────────────────────────────────────────────


class FakeEmailClient(EmailClient):
    """In-memory mailbox that records every mutation."""

    provider = "fake"

    def __init__(
        self,
        account_email: str = "sam@example.com",
        *,
        labels: bool = True,
        fail_auth: bool = False,
    ) -> None:
        self.account_email = account_email
        self.inbox: list[EmailMessage] = []
        self.sent: list[EmailMessage] = []
        self.threads: dict[str, list[EmailMessage]] = {}
        self.trashed: list[str] = []
        self.labeled: list[tuple[str, str]] = []
        self.modified: list[tuple[str, list[str] | None, list[str] | None]] = []
        self.outgoing: list[tuple[str, str, str]] = []
        self.labels: dict[str, str] = {}
        self.authenticated = False
        self._labels_supported = labels
        self._fail_auth = fail_auth

    @property
    def supports_labels(self) -> bool:
        return self._labels_supported

    def authenticate(self, interactive: bool = False) -> None:
        if self._fail_auth:
            raise RuntimeError("token expired")
        self.authenticated = True

    def fetch_emails(
        self, since: datetime | None = None, max_results: int = 100
    ) -> list[EmailMessage]:
        return list(self.inbox[:max_results])

    def fetch_sent_emails(
        self, since: datetime | None = None, max_results: int = 40
    ) -> list[EmailMessage]:
        return list(self.sent[:max_results])

    def get_thread_messages(self, thread_id: str) -> list[EmailMessage]:
        return list(self.threads.get(thread_id, []))

    def fetch_inbox_emails(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        return list(self.inbox[:max_results]), None

    def fetch_all_emails_paged(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        return self.fetch_inbox_emails(max_results, page_token, email_filter)

    def send_email(self, to: str, subject: str, body_html: str) -> None:
        self.outgoing.append((to, subject, body_html))

    def trash_email(self, message_id: str) -> None:
        self.trashed.append(message_id)

    def get_or_create_label(self, name: str, color: dict[str, str] | None = None) -> str | None:
        return self.labels.setdefault(name, f"id-{len(self.labels) + 1}")

    def apply_label(self, message_id: str, label_id: str) -> None:
        self.labeled.append((message_id, label_id))

    def apply_labels_and_actions(
        self,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> None:
        self.modified.append((message_id, add_label_ids, remove_label_ids))

    def list_labels(self) -> list[dict[str, Any]]:
        return [{"name": n, "id": i} for n, i in self.labels.items()]


@pytest.fixture
def mailbox() -> FakeEmailClient:
    return FakeEmailClient()


def make_email(email_id: str, **overrides: Any) -> EmailMessage:
    base: dict[str, Any] = {
        "id": email_id,
        "thread_id": f"t-{email_id}",
        "account_email": "sam@example.com",
        "sender": "Someone <someone@example.com>",
        "recipients": ["sam@example.com"],
        "subject": f"Subject {email_id}",
        "date": "2026-09-20T10:00:00+00:00",
        "body_snippet": "Hello",
        "labels": [],
        "has_attachment": False,
    }
    base.update(overrides)
    return EmailMessage(**base)  # type: ignore[typeddict-item]
