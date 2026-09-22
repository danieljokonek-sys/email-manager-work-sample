"""Provider parsing, tested on synthetic API payloads. No network."""

import base64
import email
from typing import Any

from email_manager.providers.gmail import GmailClient
from email_manager.providers.imap_client import ImapClient
from email_manager.providers.outlook import OutlookClient


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_gmail_parses_multipart_message_with_attachment() -> None:
    client = GmailClient("creds.json", "token.json", "me@example.com")
    msg: dict[str, Any] = {
        "id": "abc",
        "threadId": "thr",
        "labelIds": ["INBOX"],
        "snippet": "snip",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Priya <priya@x.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Cc", "value": "cc@x.com"},
                {"name": "Subject", "value": "Logo"},
                {"name": "Date", "value": "Mon, 21 Sep 2026 10:00:00 +0000"},
            ],
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64("<p>Hi <b>there</b></p>")}},
                {"mimeType": "application/pdf", "filename": "brief.pdf", "body": {}},
            ],
        },
    }
    parsed = client._parse_full(msg)
    assert parsed["id"] == "abc" and parsed["thread_id"] == "thr"
    assert parsed["recipients"] == ["me@example.com", "cc@x.com"]
    assert parsed["body_snippet"] == "Hi there"
    assert parsed["has_attachment"] is True
    assert parsed["date"].startswith("2026-09-21T10:00:00")
    assert parsed["account_email"] == "me@example.com"


def test_gmail_prefers_plain_text_and_falls_back_to_snippet() -> None:
    client = GmailClient("c", "t", "me@example.com")
    plain = {
        "id": "1",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": _b64("plain body")}},
    }
    assert client._parse_full(plain)["body_snippet"] == "plain body"
    empty = {
        "id": "2",
        "snippet": "from snippet",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {}},
    }
    assert client._parse_full(empty)["body_snippet"] == "from snippet"
    assert client._parse_full(empty)["has_attachment"] is False


def test_gmail_metadata_uses_one_recipient_shape() -> None:
    client = GmailClient("c", "t", "me@example.com")
    msg = {
        "id": "1",
        "threadId": "t",
        "payload": {"headers": [{"name": "To", "value": "a@x.com, b@x.com"}]},
    }
    assert client._parse_metadata(msg)["recipients"] == ["a@x.com", "b@x.com"]


def test_outlook_parses_html_body_and_attachments_flag() -> None:
    client = OutlookClient("cid", "common", "token.json", "me@outlook.com")
    msg = {
        "id": "o1",
        "conversationId": "conv",
        "from": {"emailAddress": {"name": "Jordan", "address": "j@x.com"}},
        "toRecipients": [{"emailAddress": {"address": "me@outlook.com"}}],
        "ccRecipients": [{"emailAddress": {"address": "cc@x.com"}}],
        "subject": "Faucet",
        "receivedDateTime": "2026-09-21T10:00:00Z",
        "body": {"contentType": "html", "content": "<div>Leak   in kitchen</div>"},
        "hasAttachments": True,
    }
    parsed = client._parse_full(msg)
    assert parsed["sender"] == "Jordan <j@x.com>"
    assert parsed["recipients"] == ["me@outlook.com", "cc@x.com"]
    assert parsed["body_snippet"] == "Leak in kitchen"
    assert parsed["has_attachment"] is True
    meta = client._parse_metadata(
        {"id": "o2", "sentDateTime": "2026-09-20T09:00:00Z", "toRecipients": []}
    )
    assert meta["date"] == "2026-09-20T09:00:00Z" and meta["thread_id"] == "o2"


def test_imap_parses_rfc822_message() -> None:
    client = ImapClient("me@yahoo.com", "pw", "imap.example", "smtp.example")
    raw = (
        "From: Alex <alex@x.com>\r\nTo: me@yahoo.com\r\nCc: other@x.com\r\nSubject: Quote\r\n"
        "Date: Mon, 21 Sep 2026 10:00:00 +0000\r\nMessage-ID: <m1@x.com>\r\n"
        'Content-Type: multipart/mixed; boundary="B"\r\n\r\n'
        "--B\r\nContent-Type: text/plain\r\n\r\nHere is the quote.\r\n"
        "--B\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=q.pdf\r\n\r\nxx\r\n--B--\r\n"
    )
    parsed = client._parse_full(email.message_from_string(raw), "42")
    assert parsed["id"] == "42" and parsed["thread_id"] == "<m1@x.com>"
    assert parsed["recipients"] == ["me@yahoo.com", "other@x.com"]
    assert parsed["body_snippet"].strip() == "Here is the quote."
    assert parsed["has_attachment"] is True


def test_imap_html_only_message_is_stripped() -> None:
    client = ImapClient("me@yahoo.com", "pw", "imap.example", "smtp.example")
    raw = "From: a@x.com\r\nContent-Type: text/html\r\n\r\n<p>Hello <i>world</i></p>"
    assert client._parse_full(email.message_from_string(raw), "1")["body_snippet"] == "Hello world"
