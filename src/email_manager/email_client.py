"""The provider contract.

Every mailbox the app can read (Gmail, Outlook, Yahoo, any IMAP server)
implements :class:`EmailClient`. The pipeline only ever talks to this
interface, so adding a provider means implementing one class. Calendar access
is a separate, optional interface because only some providers have one.

Messages cross the boundary as :class:`EmailMessage` dicts with one fixed
shape, whichever provider produced them. Date filtering crosses it as an
:class:`EmailFilter` that each provider translates into its own query syntax,
so no Gmail search operators leak into the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict


class ProviderError(RuntimeError):
    """A provider call failed for a reason other than authentication."""


class AuthError(ProviderError):
    """The account could not authenticate and needs the owner's attention."""


class EmailMessage(TypedDict, total=False):
    id: str
    thread_id: str | None
    account_email: str
    sender: str
    recipients: list[str]
    subject: str
    date: str
    body_snippet: str
    labels: list[str]
    has_attachment: bool


class CalendarEvent(TypedDict, total=False):
    event_id: str
    calendar_id: str
    calendar_name: str
    account_email: str
    title: str
    description: str
    location: str
    start_date: str | None
    start_datetime: str | None
    end_date: str | None
    end_datetime: str | None
    all_day: bool
    attendees: list[str]
    status: str


@dataclass(frozen=True)
class EmailFilter:
    """Provider-neutral date window for bulk fetches. Either bound may be open."""

    after: datetime | None = None
    before: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return self.after is None and self.before is None


Page = tuple[list[EmailMessage], str | None]
"""A page of messages plus the token for the next page (None when exhausted)."""


class EmailClient(ABC):
    """Base interface for all email providers."""

    account_email: str
    provider: str

    @abstractmethod
    def authenticate(self, interactive: bool = False) -> None:
        """Authenticate with the provider.

        ``interactive=True`` permits opening a browser to (re-)mint credentials
        and is only used by interactive commands like ``setup-accounts``. The
        scheduled pipeline calls with ``interactive=False`` so a dead token
        raises :class:`AuthError` instead of hanging on a browser prompt that
        nobody is there to complete.
        """

    @abstractmethod
    def fetch_emails(
        self, since: datetime | None = None, max_results: int = 100
    ) -> list[EmailMessage]:
        """Recent inbox messages with bodies, newest first."""

    @abstractmethod
    def fetch_sent_emails(
        self, since: datetime | None = None, max_results: int = 40
    ) -> list[EmailMessage]:
        """Recent sent messages (headers only) for follow-up detection."""

    @abstractmethod
    def get_thread_messages(self, thread_id: str) -> list[EmailMessage]:
        """All messages in a thread (headers only)."""

    @abstractmethod
    def fetch_inbox_emails(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        """One page of inbox messages with bodies."""

    @abstractmethod
    def fetch_all_emails_paged(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        """One page of all mail except sent, drafts, spam, and trash."""

    @abstractmethod
    def send_email(self, to: str, subject: str, body_html: str) -> None: ...

    @abstractmethod
    def trash_email(self, message_id: str) -> None:
        """Move a message to the provider's trash. Never a permanent delete."""

    # Label operations: default no-ops for providers without label support.

    def get_or_create_label(self, name: str, color: dict[str, str] | None = None) -> str | None:
        return None

    def apply_label(self, message_id: str, label_id: str) -> None:
        return None

    def apply_labels_and_actions(
        self,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> None:
        return None

    def list_labels(self) -> list[dict[str, Any]]:
        return []

    def get_messages_by_label(self, label_id: str, max_results: int = 500) -> list[str]:
        return []

    def delete_label(self, label_id: str) -> None:
        return None

    @property
    def supports_labels(self) -> bool:
        return False

    @property
    def supports_calendar(self) -> bool:
        return False


class CalendarClientBase(ABC):
    """Base interface for calendar providers."""

    account_email: str

    @abstractmethod
    def fetch_upcoming_events(self, days_ahead: int = 30) -> list[CalendarEvent]: ...


def parse_address_list(value: str | None) -> list[str]:
    """Split a To/Cc header into individual addresses."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]
