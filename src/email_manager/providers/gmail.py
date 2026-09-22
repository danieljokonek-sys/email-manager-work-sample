"""Gmail and Google Calendar via the Google API client with OAuth."""

from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, datetime, timedelta
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from email_manager.email_client import (
    AuthError,
    CalendarClientBase,
    CalendarEvent,
    EmailClient,
    EmailFilter,
    EmailMessage,
    Page,
    parse_address_list,
)

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
]

BODY_MAX_CHARS = 3000
REAUTH_HINT = "Re-authorize with: python main.py setup-accounts"


def _gmail_date(dt: datetime) -> str:
    return dt.strftime("%Y/%m/%d")


def _filter_query(email_filter: EmailFilter | None) -> str:
    if not email_filter or email_filter.is_empty:
        return ""
    parts = []
    if email_filter.after:
        parts.append(f"after:{_gmail_date(email_filter.after)}")
    if email_filter.before:
        parts.append(f"before:{_gmail_date(email_filter.before)}")
    return " ".join(parts)


def _parse_date(value: str) -> str:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class GmailClient(EmailClient):
    provider = "gmail"

    def __init__(self, credentials_file: str, token_file: str, account_email: str = "") -> None:
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.account_email = account_email
        self.service: Any = None
        self._creds: Credentials | None = None
        self._label_cache: dict[str, str] = {}

    @property
    def supports_labels(self) -> bool:
        return True

    @property
    def supports_calendar(self) -> bool:
        return True

    # ── auth ────────────────────────────────────────────────────────────────

    def authenticate(self, interactive: bool = False) -> None:
        creds: Credentials | None = None
        token_path = Path(self.token_file)
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            if creds and creds.scopes and not all(s in creds.scopes for s in SCOPES):
                creds = None  # token minted with narrower scopes: re-consent
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError as e:
                    # Dead refresh token: typically Google's 7-day expiry for OAuth
                    # apps still in "Testing", or access revoked. Only a fresh
                    # browser consent recovers it.
                    if not interactive:
                        raise AuthError(
                            f"Gmail refresh token for {self.account_email or self.token_file} is invalid "
                            f"({e.args[0] if e.args else e}). {REAUTH_HINT}"
                        ) from e
                    log.warning(
                        "Refresh failed for %s; re-authorizing in the browser", self.account_email
                    )
                    creds = self._run_browser_flow()
            else:
                if not interactive:
                    raise AuthError(
                        f"Gmail account {self.account_email or self.token_file} has no usable token. "
                        f"{REAUTH_HINT} (or remove it from config.yaml)."
                    )
                creds = self._run_browser_flow()
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
        self._creds = creds
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _run_browser_flow(self) -> Credentials:
        if not Path(self.credentials_file).exists():
            raise AuthError(
                f"Missing {self.credentials_file}. Download OAuth credentials from Google Cloud "
                "Console (APIs & Services > Credentials > OAuth 2.0 Client ID > Desktop app) "
                "and save the file as credentials/credentials.json"
            )
        flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
        log.info("Opening browser to authorize Gmail access for %s", self.account_email)
        return flow.run_local_server(port=0, login_hint=self.account_email or None)

    def get_credentials(self) -> Credentials:
        if self._creds is None:
            self.authenticate()
        assert self._creds is not None
        return self._creds

    def _svc(self) -> Any:
        if self.service is None:
            self.authenticate()
        return self.service

    # ── fetching ────────────────────────────────────────────────────────────

    def fetch_emails(
        self, since: datetime | None = None, max_results: int = 100
    ) -> list[EmailMessage]:
        query = f"after:{_gmail_date(since)}" if since else None
        kwargs: dict[str, Any] = {"userId": "me", "maxResults": max_results}
        if query:
            kwargs["q"] = query
        results = self._svc().users().messages().list(**kwargs).execute()
        return self._get_full(results.get("messages", []))

    def fetch_sent_emails(
        self, since: datetime | None = None, max_results: int = 40
    ) -> list[EmailMessage]:
        q_parts = ["in:sent"]
        if since:
            q_parts.append(f"after:{_gmail_date(since)}")
        results = (
            self._svc()
            .users()
            .messages()
            .list(userId="me", q=" ".join(q_parts), maxResults=max_results)
            .execute()
        )
        return self._get_metadata(results.get("messages", []))

    def get_thread_messages(self, thread_id: str) -> list[EmailMessage]:
        thread = (
            self._svc()
            .users()
            .threads()
            .get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            )
            .execute()
        )
        return [self._parse_metadata(m) for m in thread.get("messages", [])]

    def fetch_inbox_emails(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        return self._fetch_paged("in:inbox", max_results, page_token, email_filter)

    def fetch_all_emails_paged(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        return self._fetch_paged(
            "-in:sent -in:drafts -in:spam -in:trash", max_results, page_token, email_filter
        )

    def _fetch_paged(
        self,
        base_query: str,
        max_results: int,
        page_token: str | None,
        email_filter: EmailFilter | None,
    ) -> Page:
        query = " ".join(p for p in (base_query, _filter_query(email_filter)) if p)
        kwargs: dict[str, Any] = {"userId": "me", "maxResults": max_results, "q": query}
        if page_token:
            kwargs["pageToken"] = page_token
        results = self._svc().users().messages().list(**kwargs).execute()
        return self._get_full(results.get("messages", [])), results.get("nextPageToken")

    def _get_full(self, refs: list[dict[str, Any]]) -> list[EmailMessage]:
        svc = self._svc()
        out: list[EmailMessage] = []
        for ref in refs:
            msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            out.append(self._parse_full(msg))
        return out

    def _get_metadata(self, refs: list[dict[str, Any]]) -> list[EmailMessage]:
        svc = self._svc()
        out: list[EmailMessage] = []
        for ref in refs:
            msg = (
                svc.users()
                .messages()
                .get(
                    userId="me",
                    id=ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
            out.append(self._parse_metadata(msg))
        return out

    # ── sending and mutations ───────────────────────────────────────────────

    def send_email(self, to: str, subject: str, body_html: str) -> None:
        message = MIMEText(body_html, "html")
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        self._svc().users().messages().send(userId="me", body={"raw": raw}).execute()

    def trash_email(self, message_id: str) -> None:
        self._svc().users().messages().trash(userId="me", id=message_id).execute()

    # ── labels ──────────────────────────────────────────────────────────────

    def get_or_create_label(self, name: str, color: dict[str, str] | None = None) -> str | None:
        if "/" in name:
            parent = name.rsplit("/", 1)[0]
            if parent not in self._label_cache:
                self.get_or_create_label(parent)
        if name in self._label_cache:
            return self._label_cache[name]
        svc = self._svc()
        for label in svc.users().labels().list(userId="me").execute().get("labels", []):
            if label["name"].lower() == name.lower():
                self._label_cache[name] = label["id"]
                if color and not label.get("color"):
                    try:
                        svc.users().labels().update(
                            userId="me", id=label["id"], body={"color": color}
                        ).execute()
                    except Exception:
                        log.warning("Could not set color on label %s", name, exc_info=True)
                return str(label["id"])
        body: dict[str, Any] = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        if color:
            body["color"] = color
        try:
            new_label = svc.users().labels().create(userId="me", body=body).execute()
        except Exception:
            # Gmail rejects colors outside its palette; retry without one.
            body.pop("color", None)
            new_label = svc.users().labels().create(userId="me", body=body).execute()
        self._label_cache[name] = new_label["id"]
        return str(new_label["id"])

    def apply_label(self, message_id: str, label_id: str) -> None:
        self._svc().users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": [label_id]}
        ).execute()

    def apply_labels_and_actions(
        self,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> None:
        body: dict[str, Any] = {}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids
        if body:
            self._svc().users().messages().modify(userId="me", id=message_id, body=body).execute()

    def list_labels(self) -> list[dict[str, Any]]:
        return list(self._svc().users().labels().list(userId="me").execute().get("labels", []))

    def get_messages_by_label(self, label_id: str, max_results: int = 500) -> list[str]:
        svc = self._svc()
        ids: list[str] = []
        page_token = None
        while True:
            kwargs: dict[str, Any] = {
                "userId": "me",
                "labelIds": [label_id],
                "maxResults": min(max_results - len(ids), 500),
            }
            if page_token:
                kwargs["pageToken"] = page_token
            result = svc.users().messages().list(**kwargs).execute()
            ids.extend(m["id"] for m in result.get("messages", []))
            page_token = result.get("nextPageToken")
            if not page_token or len(ids) >= max_results:
                return ids

    def delete_label(self, label_id: str) -> None:
        self._svc().users().labels().delete(userId="me", id=label_id).execute()

    # ── parsing ─────────────────────────────────────────────────────────────

    @staticmethod
    def _headers(msg: dict[str, Any]) -> dict[str, str]:
        return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

    def _parse_metadata(self, msg: dict[str, Any]) -> EmailMessage:
        headers = self._headers(msg)
        return EmailMessage(
            id=msg["id"],
            thread_id=msg.get("threadId"),
            account_email=self.account_email,
            sender=headers.get("from", ""),
            recipients=parse_address_list(headers.get("to")),
            subject=headers.get("subject", ""),
            date=_parse_date(headers.get("date", "")),
            labels=list(msg.get("labelIds", [])),
        )

    def _parse_full(self, msg: dict[str, Any]) -> EmailMessage:
        headers = self._headers(msg)
        payload = msg.get("payload", {})
        body = self._extract_body(payload)
        to_cc = ", ".join(v for v in (headers.get("to"), headers.get("cc")) if v)
        return EmailMessage(
            id=msg["id"],
            thread_id=msg.get("threadId"),
            account_email=self.account_email,
            sender=headers.get("from", ""),
            recipients=parse_address_list(to_cc),
            subject=headers.get("subject", "(no subject)"),
            date=_parse_date(headers.get("date", "")),
            body_snippet=body[:BODY_MAX_CHARS] if body else msg.get("snippet", ""),
            labels=list(msg.get("labelIds", [])),
            has_attachment=self._has_attachment(payload),
        )

    def _has_attachment(self, payload: dict[str, Any]) -> bool:
        for part in payload.get("parts", []):
            if part.get("filename"):
                return True
            if part.get("parts") and self._has_attachment(part):
                return True
        return False

    def _extract_body(self, payload: dict[str, Any]) -> str:
        data = payload.get("body", {}).get("data")
        mime = payload.get("mimeType", "")
        if mime == "text/plain" and data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if mime.startswith("multipart/"):
            for part in payload.get("parts", []):
                body = self._extract_body(part)
                if body:
                    return body
        if data:
            text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return _strip_html(text) if mime == "text/html" else text
        return ""


class GoogleCalendarClient(CalendarClientBase):
    """Google Calendar, reusing the Gmail account's OAuth credentials."""

    def __init__(self, gmail: GmailClient, excluded_calendars: tuple[str, ...] = ()) -> None:
        self._gmail = gmail
        self.account_email = gmail.account_email
        self._excluded = {name.lower() for name in excluded_calendars}
        self._service: Any = None

    def _svc(self) -> Any:
        if self._service is None:
            self._service = build(
                "calendar", "v3", credentials=self._gmail.get_credentials(), cache_discovery=False
            )
        return self._service

    def fetch_upcoming_events(self, days_ahead: int = 30) -> list[CalendarEvent]:
        svc = self._svc()
        now = datetime.now(UTC)
        end = now + timedelta(days=days_ahead)
        calendars = svc.calendarList().list().execute().get("items", [])
        events: list[CalendarEvent] = []
        for cal in calendars:
            # Skip holiday feeds and other people's free/busy calendars, plus any
            # the owner excluded (third-party calendars that auto-add events).
            if cal.get("accessRole") not in ("owner", "writer", "reader"):
                continue
            if cal.get("summary", "").lower() in self._excluded:
                continue
            result = (
                svc.events()
                .list(
                    calendarId=cal["id"],
                    timeMin=now.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                )
                .execute()
            )
            events.extend(self._parse_event(item, cal) for item in result.get("items", []))
        events.sort(key=lambda e: e.get("start_datetime") or e.get("start_date") or "")
        return events

    def _parse_event(self, item: dict[str, Any], calendar: dict[str, Any]) -> CalendarEvent:
        start = item.get("start", {})
        end = item.get("end", {})
        start_date = start.get("date")
        start_datetime = start.get("dateTime")
        return CalendarEvent(
            event_id=item["id"],
            calendar_id=calendar["id"],
            calendar_name=calendar.get("summary", ""),
            account_email=self.account_email,
            title=item.get("summary", "(no title)"),
            description=(item.get("description") or "")[:500],
            location=item.get("location", ""),
            start_date=start_date,
            start_datetime=start_datetime,
            end_date=end.get("date"),
            end_datetime=end.get("dateTime"),
            all_day=bool(start_date and not start_datetime),
            attendees=[a.get("email", "") for a in item.get("attendees", []) if a.get("email")],
            status=item.get("status", "confirmed"),
        )
