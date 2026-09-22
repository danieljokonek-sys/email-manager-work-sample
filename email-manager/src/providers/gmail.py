"""
Gmail provider — wraps the Google Gmail API and Calendar API.
"""
import base64
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.email_client import EmailClient, CalendarClientBase

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
]


class GmailProvider(EmailClient):
    provider = "gmail"

    def __init__(self, credentials_file: str, token_file: str, account_email: str = ""):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.account_email = account_email
        self.service = None
        self._creds = None
        self._label_cache: dict[str, str] = {}

    @property
    def supports_labels(self) -> bool:
        return True

    @property
    def supports_calendar(self) -> bool:
        return True

    def authenticate(self, interactive: bool = False):
        creds = None
        token_path = Path(self.token_file)
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            if creds and creds.scopes and not all(s in creds.scopes for s in SCOPES):
                creds = None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError as e:
                    # The refresh token is dead — typically Google's 7-day
                    # expiry for OAuth apps still in "Testing" mode, or access
                    # revoked for this account. There is no recovery without a
                    # fresh browser consent.
                    if not interactive:
                        raise RefreshError(
                            f"Gmail refresh token for {self.account_email or self.token_file} is invalid "
                            f"({e.args[0] if e.args else e}). Re-authorize with: python main.py setup-accounts"
                        ) from e
                    print(f"\nRefresh failed for {self.account_email} ({e.args[0] if e.args else e}).")
                    print("Re-authorizing in the browser...")
                    creds = self._run_browser_flow()
            else:
                # No token, or a token with no refresh capability. Only mint a
                # new one via the browser when interactive — otherwise the
                # scheduled run would hang waiting on a browser nobody can
                # complete (e.g. a decommissioned account still in config).
                if not interactive:
                    raise RefreshError(
                        f"Gmail account {self.account_email or self.token_file} has no usable token. "
                        f"Re-authorize with: python main.py setup-accounts (or remove it from config.yaml)."
                    )
                creds = self._run_browser_flow()
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())
        self._creds = creds
        self.service = build("gmail", "v1", credentials=creds)
        return self

    def _run_browser_flow(self):
        """Open the local-server OAuth flow to mint fresh credentials."""
        if not Path(self.credentials_file).exists():
            raise FileNotFoundError(
                f"Missing {self.credentials_file}.\n"
                "Download OAuth credentials from Google Cloud Console:\n"
                "  APIs & Services > Credentials > Create > OAuth 2.0 Client ID > Desktop app\n"
                "Save the downloaded file as credentials/credentials.json"
            )
        flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
        hint = f" for {self.account_email}" if self.account_email else ""
        print(f"\nOpening browser to authorize Gmail access{hint}...")
        print("Sign in with the correct Google account when prompted.\n")
        return flow.run_local_server(port=0, login_hint=self.account_email or None)

    def get_credentials(self):
        if not self._creds:
            self.authenticate()
        return self._creds

    # ── Email fetching ──────────────────────────────────────────────────────

    def fetch_emails(self, since=None, max_results=100, extra_query=""):
        if not self.service:
            self.authenticate()
        q_parts = []
        if since:
            q_parts.append(f"after:{since.strftime('%Y/%m/%d')}")
        if extra_query:
            q_parts.append(extra_query)
        q = " ".join(q_parts) if q_parts else None
        kwargs = {"userId": "me", "maxResults": max_results}
        if q:
            kwargs["q"] = q
        results = self.service.users().messages().list(**kwargs).execute()
        emails = []
        for msg_ref in results.get("messages", []):
            msg = self.service.users().messages().get(userId="me", id=msg_ref["id"], format="full").execute()
            parsed = self._parse_message(msg)
            if parsed:
                emails.append(parsed)
        return emails

    def send_email(self, to, subject, body_html):
        if not self.service:
            self.authenticate()
        from email.mime.text import MIMEText
        message = MIMEText(body_html, "html")
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        self.service.users().messages().send(userId="me", body={"raw": raw}).execute()

    def fetch_sent_emails(self, since=None, max_results=40):
        if not self.service:
            self.authenticate()
        q_parts = ["in:sent"]
        if since:
            q_parts.append(f"after:{since.strftime('%Y/%m/%d')}")
        results = self.service.users().messages().list(
            userId="me", q=" ".join(q_parts), maxResults=max_results
        ).execute()
        emails = []
        for msg_ref in results.get("messages", []):
            msg = self.service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            ).execute()
            parsed = self._parse_message_metadata(msg)
            if parsed:
                emails.append(parsed)
        return emails

    def get_thread_messages(self, thread_id):
        if not self.service:
            self.authenticate()
        thread = self.service.users().threads().get(
            userId="me", id=thread_id, format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        return [m for m in (self._parse_message_metadata(msg) for msg in thread.get("messages", [])) if m]

    def fetch_inbox_emails(self, max_results=100, page_token=None, extra_query=""):
        return self._fetch_emails_paged("in:inbox", max_results, page_token, extra_query)

    def fetch_all_emails_paged(self, max_results=100, page_token=None, extra_query=""):
        return self._fetch_emails_paged("-in:sent -in:drafts -in:spam -in:trash", max_results, page_token, extra_query)

    def _fetch_emails_paged(self, base_query, max_results=100, page_token=None, extra_query=""):
        if not self.service:
            self.authenticate()
        q = base_query
        if extra_query:
            q += f" {extra_query}"
        kwargs: dict = {"userId": "me", "maxResults": max_results, "q": q}
        if page_token:
            kwargs["pageToken"] = page_token
        results = self.service.users().messages().list(**kwargs).execute()
        next_token = results.get("nextPageToken")
        emails = []
        for msg_ref in results.get("messages", []):
            msg = self.service.users().messages().get(userId="me", id=msg_ref["id"], format="full").execute()
            parsed = self._parse_message(msg)
            if parsed:
                emails.append(parsed)
        return emails, next_token

    # ── Label management ────────────────────────────────────────────────────

    def get_or_create_label(self, name, color=None):
        if "/" in name:
            parent = name.rsplit("/", 1)[0]
            if parent not in self._label_cache:
                self.get_or_create_label(parent)
        if name in self._label_cache:
            return self._label_cache[name]
        if not self.service:
            self.authenticate()
        result = self.service.users().labels().list(userId="me").execute()
        for label in result.get("labels", []):
            if label["name"].lower() == name.lower():
                self._label_cache[name] = label["id"]
                if color and not label.get("color"):
                    try:
                        self.service.users().labels().update(userId="me", id=label["id"], body={"color": color}).execute()
                    except Exception:
                        pass
                return label["id"]
        body: dict = {"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        if color:
            body["color"] = color
        try:
            new_label = self.service.users().labels().create(userId="me", body=body).execute()
        except Exception:
            body.pop("color", None)
            new_label = self.service.users().labels().create(userId="me", body=body).execute()
        self._label_cache[name] = new_label["id"]
        return new_label["id"]

    def apply_label(self, message_id, label_id):
        if not self.service:
            self.authenticate()
        self.service.users().messages().modify(userId="me", id=message_id, body={"addLabelIds": [label_id]}).execute()

    def apply_labels_and_actions(self, message_id, add_label_ids=None, remove_label_ids=None):
        if not self.service:
            self.authenticate()
        body: dict = {}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids
        if body:
            self.service.users().messages().modify(userId="me", id=message_id, body=body).execute()

    def trash_email(self, message_id):
        if not self.service:
            self.authenticate()
        self.service.users().messages().trash(userId="me", id=message_id).execute()

    def list_labels(self):
        if not self.service:
            self.authenticate()
        return self.service.users().labels().list(userId="me").execute().get("labels", [])

    def get_messages_by_label(self, label_id, max_results=500):
        if not self.service:
            self.authenticate()
        ids = []
        page_token = None
        while True:
            kwargs: dict = {"userId": "me", "labelIds": [label_id], "maxResults": min(max_results - len(ids), 500)}
            if page_token:
                kwargs["pageToken"] = page_token
            result = self.service.users().messages().list(**kwargs).execute()
            for msg in result.get("messages", []):
                ids.append(msg["id"])
            page_token = result.get("nextPageToken")
            if not page_token or len(ids) >= max_results:
                break
        return ids

    def delete_label(self, label_id):
        if not self.service:
            self.authenticate()
        self.service.users().labels().delete(userId="me", id=label_id).execute()

    # ── Message parsing ─────────────────────────────────────────────────────

    def _parse_message_metadata(self, msg):
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        date_str = headers.get("date", "")
        try:
            parsed_date = parsedate_to_datetime(date_str).isoformat()
        except Exception:
            parsed_date = date_str
        return {
            "id": msg["id"], "thread_id": msg.get("threadId"),
            "sender": headers.get("from", ""), "to": headers.get("to", ""),
            "subject": headers.get("subject", ""), "date": parsed_date,
            "labels": msg.get("labelIds", []),
        }

    def _parse_message(self, msg):
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body = self._extract_body(msg.get("payload", {}))
        date_str = headers.get("date", "")
        try:
            parsed_date = parsedate_to_datetime(date_str).isoformat()
        except Exception:
            parsed_date = date_str
        return {
            "id": msg["id"], "thread_id": msg.get("threadId"),
            "account_email": self.account_email,
            "sender": headers.get("from", ""),
            "recipients": self._parse_recipients(headers),
            "subject": headers.get("subject", "(no subject)"),
            "date": parsed_date,
            "body_snippet": body[:3000] if body else msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
            "has_attachment": self._has_attachment(msg.get("payload", {})),
        }

    def _has_attachment(self, payload):
        for part in payload.get("parts", []):
            if part.get("filename"):
                return True
            if part.get("parts") and self._has_attachment(part):
                return True
        return False

    def _extract_body(self, payload):
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        if payload.get("mimeType", "").startswith("multipart/"):
            for part in payload.get("parts", []):
                body = self._extract_body(part)
                if body:
                    return body
        if payload.get("body", {}).get("data"):
            text = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
            if payload.get("mimeType") == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text
        return ""

    def _parse_recipients(self, headers):
        to = headers.get("to", "")
        cc = headers.get("cc", "")
        combined = f"{to}, {cc}" if cc else to
        return [addr.strip() for addr in combined.split(",") if addr.strip()]


class GoogleCalendarClient(CalendarClientBase):
    """Google Calendar — reuses Gmail OAuth credentials."""

    def __init__(self, gmail_provider: GmailProvider):
        self.account_email = gmail_provider.account_email
        creds = gmail_provider.get_credentials()
        self.service = build("calendar", "v3", credentials=creds)

    def fetch_upcoming_events(self, days_ahead=30):
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()
        events = []
        try:
            calendars = self.service.calendarList().list().execute().get("items", [])
        except Exception:
            return []
        for cal in calendars:
            cal_id = cal["id"]
            cal_name = cal.get("summary", cal_id)
            try:
                result = self.service.events().list(
                    calendarId=cal_id, timeMin=time_min, timeMax=time_max,
                    singleEvents=True, orderBy="startTime", maxResults=50,
                ).execute()
                for ev in result.get("items", []):
                    start = ev.get("start", {})
                    events.append({
                        "event_id": ev["id"],
                        "calendar_id": cal_id,
                        "calendar_name": cal_name,
                        "title": ev.get("summary", "(no title)"),
                        "start_datetime": start.get("dateTime"),
                        "start_date": start.get("date"),
                        "end_datetime": ev.get("end", {}).get("dateTime"),
                        "end_date": ev.get("end", {}).get("date"),
                        "location": ev.get("location", ""),
                        "description": (ev.get("description") or "")[:500],
                        "attendees": ", ".join(a.get("email", "") for a in ev.get("attendees", [])),
                        "account_email": self.account_email,
                    })
            except Exception:
                continue
        return events
