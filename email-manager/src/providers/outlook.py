"""
Microsoft Outlook provider — uses Microsoft Graph API + MSAL for OAuth.

Supports Outlook.com, Hotmail, Live, and Office 365 accounts.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import msal
import requests

from src.email_client import EmailClient, CalendarClientBase

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite", "Mail.Send", "Calendars.Read"]


class OutlookClient(EmailClient):
    provider = "outlook"

    def __init__(self, client_id: str, tenant_id: str, token_file: str, account_email: str = ""):
        self.client_id = client_id
        self.tenant_id = tenant_id
        self.token_file = token_file
        self.account_email = account_email
        self._token = None
        self._app = None

    @property
    def supports_labels(self) -> bool:
        return False  # Outlook uses categories/folders, not Gmail labels

    @property
    def supports_calendar(self) -> bool:
        return True

    def authenticate(self):
        token_path = Path(self.token_file)
        cache = msal.SerializableTokenCache()
        if token_path.exists():
            cache.deserialize(token_path.read_text())

        authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self._app = msal.PublicClientApplication(
            self.client_id, authority=authority, token_cache=cache,
        )

        accounts = self._app.get_accounts()
        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
        else:
            result = None

        if not result:
            # Device code flow — works without a redirect URI
            flow = self._app.initiate_device_flow(scopes=SCOPES)
            if "user_code" in flow:
                print(f"\nTo authorize Outlook ({self.account_email}):")
                print(f"  1. Go to: {flow['verification_uri']}")
                print(f"  2. Enter code: {flow['user_code']}")
                print("  3. Sign in with your Microsoft account\n")
                result = self._app.acquire_token_by_device_flow(flow)
            else:
                raise RuntimeError(f"Could not initiate device flow: {flow.get('error_description', 'unknown error')}")

        if "access_token" not in result:
            raise RuntimeError(f"Auth failed: {result.get('error_description', result.get('error', 'unknown'))}")

        self._token = result["access_token"]

        # Save cache
        if cache.has_state_changed:
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(cache.serialize())

        # Resolve account email if not set
        if not self.account_email:
            me = self._get("/me")
            self.account_email = me.get("mail") or me.get("userPrincipalName", "")

        return self

    def _headers(self):
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _get(self, path, params=None):
        r = requests.get(f"{GRAPH_BASE}{path}", headers=self._headers(), params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        r = requests.post(f"{GRAPH_BASE}{path}", headers=self._headers(), json=data)
        r.raise_for_status()
        return r.json() if r.content else {}

    # ── Email fetching ──────────────────────────────────────────────────────

    def fetch_emails(self, since=None, max_results=100, extra_query=""):
        if not self._token:
            self.authenticate()
        params = {"$top": max_results, "$orderby": "receivedDateTime desc",
                  "$select": "id,conversationId,from,toRecipients,ccRecipients,subject,receivedDateTime,body,hasAttachments"}
        filters = []
        if since:
            filters.append(f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        if filters:
            params["$filter"] = " and ".join(filters)
        data = self._get("/me/messages", params)
        return [self._parse_message(m) for m in data.get("value", [])]

    def fetch_sent_emails(self, since=None, max_results=40):
        if not self._token:
            self.authenticate()
        params = {"$top": max_results, "$orderby": "sentDateTime desc",
                  "$select": "id,conversationId,from,toRecipients,subject,sentDateTime"}
        filters = []
        if since:
            filters.append(f"sentDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        if filters:
            params["$filter"] = " and ".join(filters)
        data = self._get("/me/mailFolders/SentItems/messages", params)
        return [self._parse_message_metadata(m) for m in data.get("value", [])]

    def get_thread_messages(self, thread_id):
        if not self._token:
            self.authenticate()
        params = {"$filter": f"conversationId eq '{thread_id}'",
                  "$select": "id,conversationId,from,toRecipients,subject,receivedDateTime",
                  "$top": 50, "$orderby": "receivedDateTime asc"}
        data = self._get("/me/messages", params)
        return [self._parse_message_metadata(m) for m in data.get("value", [])]

    def fetch_inbox_emails(self, max_results=100, page_token=None, extra_query=""):
        if not self._token:
            self.authenticate()
        params = {"$top": max_results, "$orderby": "receivedDateTime desc",
                  "$select": "id,conversationId,from,toRecipients,ccRecipients,subject,receivedDateTime,body,hasAttachments"}
        if page_token:
            # page_token is a full URL for Graph API
            r = requests.get(page_token, headers=self._headers())
            r.raise_for_status()
            data = r.json()
        else:
            data = self._get("/me/mailFolders/Inbox/messages", params)
        emails = [self._parse_message(m) for m in data.get("value", [])]
        next_link = data.get("@odata.nextLink")
        return emails, next_link

    def fetch_all_emails_paged(self, max_results=100, page_token=None, extra_query=""):
        if not self._token:
            self.authenticate()
        params = {"$top": max_results, "$orderby": "receivedDateTime desc",
                  "$select": "id,conversationId,from,toRecipients,ccRecipients,subject,receivedDateTime,body,hasAttachments",
                  "$filter": "isDraft eq false"}
        if page_token:
            r = requests.get(page_token, headers=self._headers())
            r.raise_for_status()
            data = r.json()
        else:
            data = self._get("/me/messages", params)
        emails = [self._parse_message(m) for m in data.get("value", [])]
        next_link = data.get("@odata.nextLink")
        return emails, next_link

    def send_email(self, to, subject, body_html):
        if not self._token:
            self.authenticate()
        self._post("/me/sendMail", {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": [{"emailAddress": {"address": to}}],
            }
        })

    def trash_email(self, message_id):
        if not self._token:
            self.authenticate()
        self._post(f"/me/messages/{message_id}/move", {"destinationId": "deleteditems"})

    # ── Message parsing ─────────────────────────────────────────────────────

    def _parse_message(self, msg):
        from_data = msg.get("from", {}).get("emailAddress", {})
        sender = f"{from_data.get('name', '')} <{from_data.get('address', '')}>"
        recipients = [r["emailAddress"]["address"] for r in msg.get("toRecipients", [])]
        recipients += [r["emailAddress"]["address"] for r in msg.get("ccRecipients", [])]
        body = msg.get("body", {}).get("content", "")
        if msg.get("body", {}).get("contentType") == "html":
            import re
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
        return {
            "id": msg["id"],
            "thread_id": msg.get("conversationId", msg["id"]),
            "account_email": self.account_email,
            "sender": sender,
            "recipients": recipients,
            "subject": msg.get("subject", "(no subject)"),
            "date": msg.get("receivedDateTime", ""),
            "body_snippet": body[:3000],
            "labels": [],
            "has_attachment": msg.get("hasAttachments", False),
        }

    def _parse_message_metadata(self, msg):
        from_data = msg.get("from", {}).get("emailAddress", {})
        sender = f"{from_data.get('name', '')} <{from_data.get('address', '')}>"
        to_list = [r["emailAddress"]["address"] for r in msg.get("toRecipients", [])]
        return {
            "id": msg["id"],
            "thread_id": msg.get("conversationId", msg["id"]),
            "sender": sender,
            "to": ", ".join(to_list),
            "subject": msg.get("subject", ""),
            "date": msg.get("receivedDateTime") or msg.get("sentDateTime", ""),
            "labels": [],
        }


class OutlookCalendarClient(CalendarClientBase):
    """Microsoft Calendar via Graph API."""

    def __init__(self, outlook_client: OutlookClient):
        self.account_email = outlook_client.account_email
        self._client = outlook_client

    def fetch_upcoming_events(self, days_ahead=30):
        now = datetime.now(timezone.utc)
        time_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        time_max = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "startDateTime": time_min, "endDateTime": time_max,
            "$top": 100, "$orderby": "start/dateTime",
            "$select": "id,subject,start,end,location,bodyPreview,attendees,organizer",
        }
        try:
            data = self._client._get("/me/calendarView", params)
        except Exception as e:
            log.error(f"Outlook calendar fetch failed: {e}")
            return []
        events = []
        for ev in data.get("value", []):
            start = ev.get("start", {})
            end = ev.get("end", {})
            attendee_emails = ", ".join(
                a.get("emailAddress", {}).get("address", "") for a in ev.get("attendees", [])
            )
            events.append({
                "event_id": ev["id"],
                "calendar_id": "outlook",
                "calendar_name": self.account_email,
                "title": ev.get("subject", "(no title)"),
                "start_datetime": start.get("dateTime"),
                "start_date": (start.get("dateTime") or "")[:10] or None,
                "end_datetime": end.get("dateTime"),
                "end_date": (end.get("dateTime") or "")[:10] or None,
                "location": ev.get("location", {}).get("displayName", ""),
                "description": (ev.get("bodyPreview") or "")[:500],
                "attendees": attendee_emails,
                "account_email": self.account_email,
            })
        return events
