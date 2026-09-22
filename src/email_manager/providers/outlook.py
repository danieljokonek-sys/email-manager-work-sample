"""Outlook.com / Hotmail / Microsoft 365 via Microsoft Graph, with MSAL device-code OAuth."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import msal
import requests

from email_manager.email_client import (
    AuthError,
    CalendarClientBase,
    CalendarEvent,
    EmailClient,
    EmailFilter,
    EmailMessage,
    Page,
    ProviderError,
)

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite", "Mail.Send", "Calendars.Read"]
BODY_MAX_CHARS = 3000
REQUEST_TIMEOUT = 30
_SELECT_FULL = (
    "id,conversationId,from,toRecipients,ccRecipients,subject,receivedDateTime,body,hasAttachments"
)
_SELECT_META = "id,conversationId,from,toRecipients,subject,receivedDateTime,sentDateTime"


def _graph_time(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _odata_literal(value: str) -> str:
    """Escape a string for use inside an OData ``$filter`` single-quoted literal."""
    return value.replace("'", "''")


def _filter_clauses(email_filter: EmailFilter | None) -> list[str]:
    if not email_filter or email_filter.is_empty:
        return []
    clauses = []
    if email_filter.after:
        clauses.append(f"receivedDateTime ge {_graph_time(email_filter.after)}")
    if email_filter.before:
        clauses.append(f"receivedDateTime lt {_graph_time(email_filter.before)}")
    return clauses


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class OutlookClient(EmailClient):
    provider = "outlook"

    def __init__(
        self, client_id: str, tenant_id: str, token_file: str, account_email: str = ""
    ) -> None:
        self.client_id = client_id
        self.tenant_id = tenant_id
        self.token_file = token_file
        self.account_email = account_email
        self._token: str | None = None
        self._app: msal.PublicClientApplication | None = None

    @property
    def supports_labels(self) -> bool:
        return False  # Outlook uses categories/folders, not Gmail-style labels

    @property
    def supports_calendar(self) -> bool:
        return True

    # ── auth ────────────────────────────────────────────────────────────────

    def authenticate(self, interactive: bool = False) -> None:
        token_path = Path(self.token_file)
        cache = msal.SerializableTokenCache()
        if token_path.exists():
            cache.deserialize(token_path.read_text(encoding="utf-8"))

        self._app = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=cache,
        )
        accounts = self._app.get_accounts()
        result = self._app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None

        if not result:
            if not interactive:
                raise AuthError(
                    f"Outlook account {self.account_email} has no usable token. "
                    "Re-authorize with: python main.py setup-accounts"
                )
            flow = self._app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise AuthError(
                    f"Could not start device flow: {flow.get('error_description', 'unknown error')}"
                )
            # The device-code instructions are for the person at the keyboard.
            print(f"\nTo authorize Outlook ({self.account_email}):")  # noqa: T201
            print(f"  1. Go to: {flow['verification_uri']}")  # noqa: T201
            print(f"  2. Enter code: {flow['user_code']}")  # noqa: T201
            print("  3. Sign in with your Microsoft account\n")  # noqa: T201
            result = self._app.acquire_token_by_device_flow(flow)

        if "access_token" not in result:
            raise AuthError(
                f"Outlook auth failed: {result.get('error_description', result.get('error', 'unknown'))}"
            )
        self._token = result["access_token"]

        if cache.has_state_changed:
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(cache.serialize(), encoding="utf-8")

        if not self.account_email:
            me = self.graph_get("/me")
            self.account_email = me.get("mail") or me.get("userPrincipalName", "")

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self.authenticate()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def graph_get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("http") else f"{GRAPH_BASE}{path_or_url}"
        try:
            r = requests.get(url, headers=self._headers(), params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            raise ProviderError(f"Graph GET {path_or_url} failed: {e}") from e
        return dict(r.json())

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            r = requests.post(
                f"{GRAPH_BASE}{path}", headers=self._headers(), json=data, timeout=REQUEST_TIMEOUT
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise ProviderError(f"Graph POST {path} failed: {e}") from e
        return dict(r.json()) if r.content else {}

    # ── fetching ────────────────────────────────────────────────────────────

    def fetch_emails(
        self, since: datetime | None = None, max_results: int = 100
    ) -> list[EmailMessage]:
        params: dict[str, Any] = {
            "$top": max_results,
            "$orderby": "receivedDateTime desc",
            "$select": _SELECT_FULL,
        }
        if since:
            params["$filter"] = f"receivedDateTime ge {_graph_time(since)}"
        data = self.graph_get("/me/messages", params)
        return [self._parse_full(m) for m in data.get("value", [])]

    def fetch_sent_emails(
        self, since: datetime | None = None, max_results: int = 40
    ) -> list[EmailMessage]:
        params: dict[str, Any] = {
            "$top": max_results,
            "$orderby": "sentDateTime desc",
            "$select": _SELECT_META,
        }
        if since:
            params["$filter"] = f"sentDateTime ge {_graph_time(since)}"
        data = self.graph_get("/me/mailFolders/SentItems/messages", params)
        return [self._parse_metadata(m) for m in data.get("value", [])]

    def get_thread_messages(self, thread_id: str) -> list[EmailMessage]:
        params = {
            "$filter": f"conversationId eq '{_odata_literal(thread_id)}'",
            "$select": _SELECT_META,
            "$top": 50,
            "$orderby": "receivedDateTime asc",
        }
        data = self.graph_get("/me/messages", params)
        return [self._parse_metadata(m) for m in data.get("value", [])]

    def fetch_inbox_emails(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        return self._fetch_paged(
            "/me/mailFolders/Inbox/messages", [], max_results, page_token, email_filter
        )

    def fetch_all_emails_paged(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        return self._fetch_paged(
            "/me/messages", ["isDraft eq false"], max_results, page_token, email_filter
        )

    def _fetch_paged(
        self,
        path: str,
        base_filters: list[str],
        max_results: int,
        page_token: str | None,
        email_filter: EmailFilter | None,
    ) -> Page:
        if page_token:
            data = self.graph_get(page_token)  # Graph's next-page token is a full URL
        else:
            params: dict[str, Any] = {
                "$top": max_results,
                "$orderby": "receivedDateTime desc",
                "$select": _SELECT_FULL,
            }
            filters = base_filters + _filter_clauses(email_filter)
            if filters:
                params["$filter"] = " and ".join(filters)
            data = self.graph_get(path, params)
        return [self._parse_full(m) for m in data.get("value", [])], data.get("@odata.nextLink")

    # ── sending and mutations ───────────────────────────────────────────────

    def send_email(self, to: str, subject: str, body_html: str) -> None:
        self._post(
            "/me/sendMail",
            {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": body_html},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                }
            },
        )

    def trash_email(self, message_id: str) -> None:
        self._post(f"/me/messages/{message_id}/move", {"destinationId": "deleteditems"})

    # ── parsing ─────────────────────────────────────────────────────────────

    @staticmethod
    def _sender(msg: dict[str, Any]) -> str:
        from_data = msg.get("from", {}).get("emailAddress", {})
        return f"{from_data.get('name', '')} <{from_data.get('address', '')}>".strip()

    @staticmethod
    def _addresses(entries: list[dict[str, Any]]) -> list[str]:
        return [
            e.get("emailAddress", {}).get("address", "") for e in entries if e.get("emailAddress")
        ]

    def _parse_full(self, msg: dict[str, Any]) -> EmailMessage:
        body_obj = msg.get("body", {})
        body = body_obj.get("content", "")
        if body_obj.get("contentType") == "html":
            body = _strip_html(body)
        return EmailMessage(
            id=msg["id"],
            thread_id=msg.get("conversationId", msg["id"]),
            account_email=self.account_email,
            sender=self._sender(msg),
            recipients=self._addresses(msg.get("toRecipients", []))
            + self._addresses(msg.get("ccRecipients", [])),
            subject=msg.get("subject", "(no subject)"),
            date=msg.get("receivedDateTime", ""),
            body_snippet=body[:BODY_MAX_CHARS],
            labels=[],
            has_attachment=bool(msg.get("hasAttachments", False)),
        )

    def _parse_metadata(self, msg: dict[str, Any]) -> EmailMessage:
        return EmailMessage(
            id=msg["id"],
            thread_id=msg.get("conversationId", msg["id"]),
            account_email=self.account_email,
            sender=self._sender(msg),
            recipients=self._addresses(msg.get("toRecipients", [])),
            subject=msg.get("subject", ""),
            date=msg.get("receivedDateTime") or msg.get("sentDateTime", ""),
            labels=[],
        )


class OutlookCalendarClient(CalendarClientBase):
    """Microsoft calendar via Graph's calendarView."""

    def __init__(self, outlook: OutlookClient) -> None:
        self._client = outlook
        self.account_email = outlook.account_email

    def fetch_upcoming_events(self, days_ahead: int = 30) -> list[CalendarEvent]:
        now = datetime.now(UTC)
        params = {
            "startDateTime": _graph_time(now),
            "endDateTime": _graph_time(now + timedelta(days=days_ahead)),
            "$top": 100,
            "$orderby": "start/dateTime",
            "$select": "id,subject,start,end,location,bodyPreview,attendees,isAllDay",
        }
        data = self._client.graph_get("/me/calendarView", params)
        events: list[CalendarEvent] = []
        for ev in data.get("value", []):
            start = ev.get("start", {}).get("dateTime")
            end = ev.get("end", {}).get("dateTime")
            all_day = bool(ev.get("isAllDay"))
            events.append(
                CalendarEvent(
                    event_id=ev["id"],
                    calendar_id="outlook",
                    calendar_name=self.account_email,
                    account_email=self.account_email,
                    title=ev.get("subject", "(no title)"),
                    description=(ev.get("bodyPreview") or "")[:500],
                    location=ev.get("location", {}).get("displayName", ""),
                    start_date=(start or "")[:10] or None,
                    start_datetime=None if all_day else start,
                    end_date=(end or "")[:10] or None,
                    end_datetime=None if all_day else end,
                    all_day=all_day,
                    attendees=[
                        a.get("emailAddress", {}).get("address", "")
                        for a in ev.get("attendees", [])
                        if a.get("emailAddress", {}).get("address")
                    ],
                    status="confirmed",
                )
            )
        return events
