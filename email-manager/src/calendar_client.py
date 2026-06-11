"""
Google Calendar client — reuses the same OAuth credentials as Gmail.

Fetches upcoming events from all calendars on a given Google account
so they can be surfaced in the digest and cross-referenced with emails.
"""
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build


class CalendarClient:
    def __init__(self, gmail_client):
        """
        Pass an authenticated GmailClient — we reuse its credentials
        so the user only needs to authorize once per account.
        """
        self._gmail = gmail_client
        self.account_email = getattr(gmail_client, 'account_email', getattr(gmail_client, 'email', ''))
        self._service = None

    def _get_service(self):
        if not self._service:
            creds = self._gmail.get_credentials()
            self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def fetch_upcoming_events(self, days_ahead: int = 30) -> list[dict]:
        """Fetch all events from all calendars for the next N days."""
        svc = self._get_service()

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)

        # List all calendars this account has access to
        calendars = svc.calendarList().list().execute().get("items", [])

        events = []
        # Third-party calendars (e.g. Partiful) auto-add events regardless of
        # RSVP status — exclude them so only real Google Calendar events appear.
        EXCLUDED_CALENDARS = {"Partiful"}

        for cal in calendars:
            # Skip calendars that are just holidays or other people's busy status
            if cal.get("accessRole") not in ("owner", "writer", "reader"):
                continue
            if cal.get("summary", "") in EXCLUDED_CALENDARS:
                continue

            result = svc.events().list(
                calendarId=cal["id"],
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
            ).execute()

            for item in result.get("items", []):
                parsed = self._parse_event(item, cal)
                if parsed:
                    events.append(parsed)

        # Sort all events across calendars by start time
        events.sort(key=lambda e: e.get("start_datetime") or e.get("start_date") or "")
        return events

    @staticmethod
    def _to_naive(dt_str: str | None) -> str | None:
        """Convert to local time, then strip timezone offset."""
        if not dt_str:
            return dt_str
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt.isoformat()
        except (ValueError, TypeError):
            return dt_str

    def _parse_event(self, item: dict, calendar: dict) -> dict | None:
        start = item.get("start", {})
        end = item.get("end", {})

        # all-day events use "date", timed events use "dateTime"
        start_date = start.get("date")
        start_datetime = self._to_naive(start.get("dateTime"))
        end_date = end.get("date")
        end_datetime = self._to_naive(end.get("dateTime"))

        return {
            "event_id": item["id"],
            "calendar_id": calendar["id"],
            "calendar_name": calendar.get("summary", ""),
            "account_email": self._gmail.account_email,
            "title": item.get("summary", "(no title)"),
            "description": (item.get("description") or "")[:500],
            "location": item.get("location", ""),
            "start_date": start_date,
            "start_datetime": start_datetime,
            "end_date": end_date,
            "end_datetime": end_datetime,
            "all_day": bool(start_date and not start_datetime),
            "attendees": [
                a.get("email", "") for a in item.get("attendees", [])
            ],
            "status": item.get("status", "confirmed"),
            "html_link": item.get("htmlLink", ""),
        }
