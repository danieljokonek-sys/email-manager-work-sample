"""
Generic IMAP/SMTP email provider — works with Yahoo, AOL, iCloud, and any IMAP server.

Uses Python stdlib only (imaplib, smtplib, email). No calendar support.
"""
import email
import email.utils
import imaplib
import logging
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

from src.email_client import EmailClient

log = logging.getLogger(__name__)

# Pre-configured provider defaults
PROVIDER_DEFAULTS = {
    "yahoo": {
        "imap_server": "imap.mail.yahoo.com",
        "imap_port": 993,
        "smtp_server": "smtp.mail.yahoo.com",
        "smtp_port": 465,
        "trash_folder": "Trash",
    },
    "aol": {
        "imap_server": "imap.aol.com",
        "imap_port": 993,
        "smtp_server": "smtp.aol.com",
        "smtp_port": 465,
        "trash_folder": "Trash",
    },
    "icloud": {
        "imap_server": "imap.mail.me.com",
        "imap_port": 993,
        "smtp_server": "smtp.mail.me.com",
        "smtp_port": 587,
        "trash_folder": "Deleted Messages",
    },
    "imap": {
        "imap_port": 993,
        "smtp_port": 465,
        "trash_folder": "Trash",
    },
}


class ImapClient(EmailClient):
    """Generic IMAP/SMTP client. Works with any IMAP-compatible mail server."""

    def __init__(self, account_email: str, password: str,
                 imap_server: str, smtp_server: str,
                 imap_port: int = 993, smtp_port: int = 465,
                 trash_folder: str = "Trash",
                 provider_name: str = "imap"):
        self.account_email = account_email
        self._password = password
        self._imap_server = imap_server
        self._smtp_server = smtp_server
        self._imap_port = imap_port
        self._smtp_port = smtp_port
        self._trash_folder = trash_folder
        self.provider = provider_name
        self._imap: imaplib.IMAP4_SSL | None = None

    @property
    def supports_labels(self) -> bool:
        return False

    @property
    def supports_calendar(self) -> bool:
        return False

    def authenticate(self, interactive: bool = False):
        ctx = ssl.create_default_context()
        self._imap = imaplib.IMAP4_SSL(self._imap_server, self._imap_port, ssl_context=ctx)
        self._imap.login(self.account_email, self._password)
        return self

    def _ensure_connected(self):
        if not self._imap:
            self.authenticate()
        try:
            self._imap.noop()
        except Exception:
            self.authenticate()

    # ── Email fetching ──────────────────────────────────────────────────────

    def fetch_emails(self, since=None, max_results=100, extra_query=""):
        self._ensure_connected()
        self._imap.select("INBOX")
        criteria = self._build_search_criteria(since)
        _, data = self._imap.search(None, *criteria)
        msg_ids = data[0].split()
        if not msg_ids:
            return []
        # Most recent first, limited
        msg_ids = msg_ids[-max_results:][::-1]
        return self._fetch_messages(msg_ids)

    def fetch_sent_emails(self, since=None, max_results=40):
        self._ensure_connected()
        # Try common sent folder names
        for folder in ['"[Gmail]/Sent Mail"', "Sent", '"Sent Items"', '"Sent Messages"', "INBOX.Sent"]:
            status, _ = self._imap.select(folder)
            if status == "OK":
                break
        else:
            return []
        criteria = self._build_search_criteria(since)
        _, data = self._imap.search(None, *criteria)
        msg_ids = data[0].split()
        if not msg_ids:
            return []
        msg_ids = msg_ids[-max_results:][::-1]
        return self._fetch_messages_metadata(msg_ids)

    def get_thread_messages(self, thread_id):
        # IMAP doesn't have native threading. thread_id here is a Message-ID.
        # Best-effort: search by References/In-Reply-To headers.
        self._ensure_connected()
        self._imap.select("INBOX")
        _, data = self._imap.search(None, f'(OR HEADER "References" "{thread_id}" HEADER "Message-ID" "{thread_id}")')
        msg_ids = data[0].split()
        if not msg_ids:
            return []
        return self._fetch_messages_metadata(msg_ids)

    def fetch_inbox_emails(self, max_results=100, page_token=None, extra_query=""):
        self._ensure_connected()
        self._imap.select("INBOX")
        _, data = self._imap.search(None, "ALL")
        msg_ids = data[0].split()
        if not msg_ids:
            return [], None
        # Simple pagination using offset
        start = int(page_token) if page_token else 0
        chunk = msg_ids[-(start + max_results):][::-1][:max_results] if not page_token else msg_ids[-(start + max_results):-(start)][::-1]
        if not chunk:
            return [], None
        emails = self._fetch_messages(chunk)
        next_token = str(start + max_results) if start + max_results < len(msg_ids) else None
        return emails, next_token

    def fetch_all_emails_paged(self, max_results=100, page_token=None, extra_query=""):
        # For IMAP, same as inbox fetch (we'd need to enumerate folders otherwise)
        return self.fetch_inbox_emails(max_results, page_token, extra_query)

    def send_email(self, to, subject, body_html):
        msg = MIMEText(body_html, "html")
        msg["From"] = self.account_email
        msg["To"] = to
        msg["Subject"] = subject
        ctx = ssl.create_default_context()
        if self._smtp_port == 587:
            with smtplib.SMTP(self._smtp_server, self._smtp_port) as server:
                server.starttls(context=ctx)
                server.login(self.account_email, self._password)
                server.sendmail(self.account_email, to, msg.as_string())
        else:
            with smtplib.SMTP_SSL(self._smtp_server, self._smtp_port, context=ctx) as server:
                server.login(self.account_email, self._password)
                server.sendmail(self.account_email, to, msg.as_string())

    def trash_email(self, message_id):
        self._ensure_connected()
        self._imap.select("INBOX")
        # message_id is the IMAP UID we stored
        self._imap.uid("COPY", message_id, self._trash_folder)
        self._imap.uid("STORE", message_id, "+FLAGS", "\\Deleted")
        self._imap.expunge()

    # ── Internal helpers ────────────────────────────────────────────────────

    def _build_search_criteria(self, since=None):
        if since:
            date_str = since.strftime("%d-%b-%Y")
            return [f'(SINCE {date_str})']
        return ["ALL"]

    def _fetch_messages(self, msg_ids):
        emails = []
        for mid in msg_ids:
            try:
                _, data = self._imap.fetch(mid, "(RFC822)")
                if not data or not data[0]:
                    continue
                raw = data[0][1]
                msg = email.message_from_bytes(raw)
                parsed = self._parse_email(msg, mid.decode() if isinstance(mid, bytes) else str(mid))
                if parsed:
                    emails.append(parsed)
            except Exception as e:
                log.warning(f"Failed to fetch message {mid}: {e}")
        return emails

    def _fetch_messages_metadata(self, msg_ids):
        emails = []
        for mid in msg_ids:
            try:
                _, data = self._imap.fetch(mid, "(BODY[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
                if not data or not data[0]:
                    continue
                raw = data[0][1]
                msg = email.message_from_bytes(raw)
                mid_str = mid.decode() if isinstance(mid, bytes) else str(mid)
                date_str = msg.get("Date", "")
                try:
                    parsed_date = email.utils.parsedate_to_datetime(date_str).isoformat()
                except Exception:
                    parsed_date = date_str
                emails.append({
                    "id": mid_str,
                    "thread_id": msg.get("Message-ID", mid_str),
                    "sender": msg.get("From", ""),
                    "to": msg.get("To", ""),
                    "subject": msg.get("Subject", ""),
                    "date": parsed_date,
                    "labels": [],
                })
            except Exception as e:
                log.warning(f"Failed to fetch metadata for {mid}: {e}")
        return emails

    def _parse_email(self, msg, uid):
        date_str = msg.get("Date", "")
        try:
            parsed_date = email.utils.parsedate_to_datetime(date_str).isoformat()
        except Exception:
            parsed_date = date_str

        body = self._extract_body(msg)
        to_str = msg.get("To", "")
        cc_str = msg.get("Cc", "")
        recipients = [a.strip() for a in f"{to_str}, {cc_str}".split(",") if a.strip()]
        has_attachment = any(
            part.get_content_disposition() == "attachment"
            for part in msg.walk()
        )

        return {
            "id": uid,
            "thread_id": msg.get("Message-ID", uid),
            "account_email": self.account_email,
            "sender": msg.get("From", ""),
            "recipients": recipients,
            "subject": msg.get("Subject", "(no subject)"),
            "date": parsed_date,
            "body_snippet": body[:3000] if body else "",
            "labels": [],
            "has_attachment": has_attachment,
        }

    def _extract_body(self, msg):
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="replace")
            # Fallback to HTML
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        text = payload.decode("utf-8", errors="replace")
                        text = re.sub(r"<[^>]+>", " ", text)
                        return re.sub(r"\s+", " ", text).strip()
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                text = payload.decode("utf-8", errors="replace")
                if msg.get_content_type() == "text/html":
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                return text
        return ""
