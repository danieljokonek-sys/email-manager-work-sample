"""Generic IMAP/SMTP provider for Yahoo, AOL, iCloud, and any other IMAP server.

Standard library only (imaplib, smtplib, email). No calendar, no labels.
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.message import Message
from email.mime.text import MIMEText
from typing import Any

from email_manager.email_client import (
    AuthError,
    EmailClient,
    EmailFilter,
    EmailMessage,
    Page,
    parse_address_list,
)

log = logging.getLogger(__name__)

BODY_MAX_CHARS = 3000

PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
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
    "imap": {"imap_port": 993, "smtp_port": 465, "trash_folder": "Trash"},
}

_SENT_FOLDERS = ('"[Gmail]/Sent Mail"', "Sent", '"Sent Items"', '"Sent Messages"', "INBOX.Sent")


def _imap_date(dt: datetime) -> str:
    return dt.strftime("%d-%b-%Y")


def _imap_quoted(value: str) -> str:
    """Quote a string for an IMAP SEARCH argument, escaping quotes and backslashes."""
    cleaned = value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")
    return f'"{cleaned}"'


def _search_criteria(
    since: datetime | None = None, email_filter: EmailFilter | None = None
) -> list[str]:
    parts: list[str] = []
    if since:
        parts.append(f"SINCE {_imap_date(since)}")
    if email_filter:
        if email_filter.after:
            parts.append(f"SINCE {_imap_date(email_filter.after)}")
        if email_filter.before:
            parts.append(f"BEFORE {_imap_date(email_filter.before)}")
    return [f"({' '.join(parts)})"] if parts else ["ALL"]


def _parse_date(value: str) -> str:
    try:
        return email.utils.parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return value


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class ImapClient(EmailClient):
    def __init__(
        self,
        account_email: str,
        password: str,
        imap_server: str,
        smtp_server: str,
        imap_port: int = 993,
        smtp_port: int = 465,
        trash_folder: str = "Trash",
        provider_name: str = "imap",
    ) -> None:
        self.account_email = account_email
        self.provider = provider_name
        self._password = password
        self._imap_server = imap_server
        self._smtp_server = smtp_server
        self._imap_port = imap_port
        self._smtp_port = smtp_port
        self._trash_folder = trash_folder
        self._imap: imaplib.IMAP4_SSL | None = None

    # ── auth ────────────────────────────────────────────────────────────────

    def authenticate(self, interactive: bool = False) -> None:
        try:
            self._imap = imaplib.IMAP4_SSL(
                self._imap_server, self._imap_port, ssl_context=ssl.create_default_context()
            )
            self._imap.login(self.account_email, self._password)
        except (imaplib.IMAP4.error, OSError) as e:
            raise AuthError(f"IMAP login failed for {self.account_email}: {e}") from e

    def _conn(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            self.authenticate()
        else:
            try:
                self._imap.noop()
            except (imaplib.IMAP4.error, OSError):
                self.authenticate()
        assert self._imap is not None
        return self._imap

    # ── fetching ────────────────────────────────────────────────────────────

    def fetch_emails(
        self, since: datetime | None = None, max_results: int = 100
    ) -> list[EmailMessage]:
        conn = self._conn()
        conn.select("INBOX")
        ids = self._search(conn, _search_criteria(since=since))
        return self._fetch_full(conn, ids[-max_results:][::-1])

    def fetch_sent_emails(
        self, since: datetime | None = None, max_results: int = 40
    ) -> list[EmailMessage]:
        conn = self._conn()
        for folder in _SENT_FOLDERS:
            status, _ = conn.select(folder)
            if status == "OK":
                break
        else:
            return []
        ids = self._search(conn, _search_criteria(since=since))
        return self._fetch_headers(conn, ids[-max_results:][::-1])

    def get_thread_messages(self, thread_id: str) -> list[EmailMessage]:
        # IMAP has no native threading; thread_id is a Message-ID. Best effort:
        # find messages that reference it or are it.
        conn = self._conn()
        conn.select("INBOX")
        quoted = _imap_quoted(thread_id)
        ids = self._search(conn, [f"(OR HEADER References {quoted} HEADER Message-ID {quoted})"])
        return self._fetch_headers(conn, ids)

    def fetch_inbox_emails(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        conn = self._conn()
        conn.select("INBOX")
        ids = self._search(conn, _search_criteria(email_filter=email_filter))
        if not ids:
            return [], None
        # Offset pagination from the newest message backwards.
        start = int(page_token) if page_token else 0
        newest_first = ids[::-1]
        chunk = newest_first[start : start + max_results]
        if not chunk:
            return [], None
        next_token = str(start + max_results) if start + max_results < len(newest_first) else None
        return self._fetch_full(conn, chunk), next_token

    def fetch_all_emails_paged(
        self,
        max_results: int = 100,
        page_token: str | None = None,
        email_filter: EmailFilter | None = None,
    ) -> Page:
        # Without a folder walk this is the inbox. Documented limitation; the
        # method exists so callers can treat every provider the same way.
        return self.fetch_inbox_emails(max_results, page_token, email_filter)

    # ── sending and mutations ───────────────────────────────────────────────

    def send_email(self, to: str, subject: str, body_html: str) -> None:
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

    def trash_email(self, message_id: str) -> None:
        conn = self._conn()
        conn.select("INBOX")
        conn.uid("COPY", message_id, self._trash_folder)
        conn.uid("STORE", message_id, "+FLAGS", "\\Deleted")
        conn.expunge()

    # ── internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _search(conn: imaplib.IMAP4_SSL, criteria: list[str]) -> list[bytes]:
        _, data = conn.search(None, *criteria)
        return list(data[0].split()) if data and data[0] else []

    def _fetch_full(self, conn: imaplib.IMAP4_SSL, ids: list[bytes]) -> list[EmailMessage]:
        out: list[EmailMessage] = []
        for mid in ids:
            try:
                _, data = conn.fetch(mid.decode(), "(RFC822)")
                if not data or not data[0] or not isinstance(data[0], tuple):
                    continue
                out.append(self._parse_full(email.message_from_bytes(data[0][1]), mid.decode()))
            except (imaplib.IMAP4.error, OSError):
                log.warning("Failed to fetch message %r", mid, exc_info=True)
        return out

    def _fetch_headers(self, conn: imaplib.IMAP4_SSL, ids: list[bytes]) -> list[EmailMessage]:
        out: list[EmailMessage] = []
        for mid in ids:
            try:
                _, data = conn.fetch(
                    mid.decode(), "(BODY[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])"
                )
                if not data or not data[0] or not isinstance(data[0], tuple):
                    continue
                msg = email.message_from_bytes(data[0][1])
                uid = mid.decode()
                out.append(
                    EmailMessage(
                        id=uid,
                        thread_id=msg.get("Message-ID", uid),
                        account_email=self.account_email,
                        sender=msg.get("From", ""),
                        recipients=parse_address_list(msg.get("To", "")),
                        subject=msg.get("Subject", ""),
                        date=_parse_date(msg.get("Date", "")),
                        labels=[],
                    )
                )
            except (imaplib.IMAP4.error, OSError):
                log.warning("Failed to fetch headers for %r", mid, exc_info=True)
        return out

    def _parse_full(self, msg: Message, uid: str) -> EmailMessage:
        body = self._extract_body(msg)
        to_cc = ", ".join(v for v in (msg.get("To", ""), msg.get("Cc", "")) if v)
        return EmailMessage(
            id=uid,
            thread_id=msg.get("Message-ID", uid),
            account_email=self.account_email,
            sender=msg.get("From", ""),
            recipients=parse_address_list(to_cc),
            subject=msg.get("Subject", "(no subject)"),
            date=_parse_date(msg.get("Date", "")),
            body_snippet=body[:BODY_MAX_CHARS],
            labels=[],
            has_attachment=any(p.get_content_disposition() == "attachment" for p in msg.walk()),
        )

    @staticmethod
    def _decode(part: Message) -> str:
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""

    def _extract_body(self, msg: Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    text = self._decode(part)
                    if text:
                        return text
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    text = self._decode(part)
                    if text:
                        return _strip_html(text)
            return ""
        text = self._decode(msg)
        return _strip_html(text) if msg.get_content_type() == "text/html" else text
