"""
Abstract base for all email providers (Gmail, Outlook, Yahoo, IMAP).

Every provider must implement the core methods. Label operations have
default no-ops for providers that don't support Gmail-style labels.
"""
from abc import ABC, abstractmethod
from datetime import datetime


class EmailClient(ABC):
    """Base interface for all email providers."""

    account_email: str
    provider: str  # "gmail", "outlook", "yahoo", "imap"

    @abstractmethod
    def authenticate(self):
        """Authenticate with the provider. May open browser for OAuth."""
        ...

    @abstractmethod
    def fetch_emails(self, since: datetime | None = None, max_results: int = 100, extra_query: str = "") -> list[dict]:
        """Fetch emails since a given date. Returns list of parsed email dicts."""
        ...

    @abstractmethod
    def fetch_sent_emails(self, since: datetime | None = None, max_results: int = 40) -> list[dict]:
        """Fetch sent emails for follow-up detection."""
        ...

    @abstractmethod
    def get_thread_messages(self, thread_id: str) -> list[dict]:
        """Get all messages in a thread/conversation (metadata only)."""
        ...

    @abstractmethod
    def fetch_inbox_emails(self, max_results: int = 100, page_token: str | None = None, extra_query: str = "") -> tuple[list[dict], str | None]:
        """Fetch inbox emails with pagination. Returns (emails, next_page_token)."""
        ...

    @abstractmethod
    def fetch_all_emails_paged(self, max_results: int = 100, page_token: str | None = None, extra_query: str = "") -> tuple[list[dict], str | None]:
        """Fetch all mail (except sent/drafts/spam/trash) with pagination."""
        ...

    @abstractmethod
    def send_email(self, to: str, subject: str, body_html: str):
        """Send an email."""
        ...

    @abstractmethod
    def trash_email(self, message_id: str):
        """Move a message to trash."""
        ...

    # Label operations — default no-ops for providers without label support.
    # Gmail overrides all of these. Outlook uses categories. IMAP uses folders.

    def get_or_create_label(self, name: str, color: dict | None = None) -> str | None:
        return None

    def apply_label(self, message_id: str, label_id: str):
        pass

    def apply_labels_and_actions(self, message_id: str, add_label_ids: list[str] | None = None, remove_label_ids: list[str] | None = None):
        pass

    def list_labels(self) -> list[dict]:
        return []

    def get_messages_by_label(self, label_id: str, max_results: int = 500) -> list[str]:
        return []

    def delete_label(self, label_id: str):
        pass

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
    def fetch_upcoming_events(self, days_ahead: int = 30) -> list[dict]:
        """Fetch upcoming calendar events. Returns list of event dicts."""
        ...
