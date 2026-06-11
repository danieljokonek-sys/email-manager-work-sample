"""
Digest generation and email delivery.

Queries the database for pending items, uses Claude to generate a
natural-language briefing, and sends it via Gmail.
"""
from datetime import date, datetime

from src.briefing import database as db
from src.briefing.analyzer import Analyzer
from src.gmail_client import GmailClient
from src.entities import Entity
from src.briefing.database import get_horizon_data


def _format_iso_to_12hr(iso_str: str) -> str:
    """Convert an ISO datetime string to 'Apr 10, 2026 at 2:30 PM' format."""
    if not iso_str or "T" not in iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%b %d, %Y at %I:%M %p").replace(" 0", " ")
    except (ValueError, TypeError):
        return iso_str


def _localize_times(obj):
    """Recursively convert ISO datetime strings in dicts/lists to 12-hour format."""
    datetime_keys = {"start_datetime", "end_datetime", "created_at", "updated_at",
                     "sent_at", "last_checked"}
    if isinstance(obj, dict):
        return {
            k: _format_iso_to_12hr(v) if k in datetime_keys and isinstance(v, str) else _localize_times(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_localize_times(item) for item in obj]
    return obj


class DigestGenerator:
    def __init__(
        self,
        analyzer: Analyzer,
        gmail: GmailClient,
        entities: dict[str, Entity],
        send_to: str,
    ):
        self.analyzer = analyzer
        self.gmail = gmail
        self.entities = entities
        self.send_to = send_to

    def collect_digest_data(self) -> dict:
        """Gather all pending items from the database for the digest."""
        summary = db.get_dashboard_summary()

        return {
            "date": date.today().isoformat(),
            "summary": summary,
            "upcoming_calendar_events": db.get_upcoming_events(days_ahead=14),
            "upcoming_deadlines": db.get_pending_deadlines(days_ahead=14),
            "money_owed_to_you": db.get_pending_financial(direction="receivable"),
            "money_you_owe": db.get_pending_financial(direction="payable"),
            "active_agreements": db.get_active_agreements(),
            "action_items": db.get_pending_actions(),
            "tasks": db.get_pending_tasks(),
            "follow_ups_needed": db.get_pending_follow_ups(),
            "stale_action_items": db.get_stale_action_items(days=3),
            "horizon": get_horizon_data(days_ahead=14),
            "entities": {
                k: {"name": e.name, "description": e.description}
                for k, e in self.entities.items()
            },
        }

    def generate_and_send(self, digest_type: str = "daily") -> str:
        """Generate a digest and send it via email. Returns the HTML content."""
        data = _localize_times(self.collect_digest_data())

        # Check if there's anything to report
        has_content = (
            data["summary"]["pending_deadlines"] > 0
            or data["summary"]["pending_actions"] > 0
            or data["summary"]["money_owed_to_you"] > 0
            or data["summary"]["money_you_owe"] > 0
            or data["summary"]["active_agreements"] > 0
            or data["summary"].get("pending_tasks", 0) > 0
            or data["summary"].get("follow_ups_waiting", 0) > 0
            or data["upcoming_calendar_events"]
        )

        if not has_content:
            html = self._empty_digest_html()
        else:
            html = self.analyzer.generate_digest_analysis(data)

        # Clean up HTML if it's wrapped in code blocks
        html = html.strip()
        if html.startswith("```"):
            lines = html.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            html = "\n".join(lines)

        subject = f"{'Daily' if digest_type == 'daily' else 'Weekly'} Briefing — {date.today().strftime('%b %d, %Y')}"

        self.gmail.send_email(
            to=self.send_to,
            subject=subject,
            body_html=self._wrap_html(html),
        )

        return html

    def _empty_digest_html(self) -> str:
        return """
        <h2>All Clear</h2>
        <p>No pending deadlines, financial items, or action items to report.
        Enjoy your day!</p>
        """

    def _wrap_html(self, body: str) -> str:
        """Wrap content in a styled HTML email template."""
        return f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #333; max-width: 680px; margin: 0 auto; padding: 20px; }}
  h1 {{ color: #1a1a2e; border-bottom: 2px solid #e94560; padding-bottom: 8px; }}
  h2 {{ color: #1a1a2e; margin-top: 24px; }}
  h3 {{ color: #16213e; }}
  .urgent {{ background: #fff3cd; border-left: 4px solid #e94560; padding: 12px; margin: 8px 0; }}
  .money {{ background: #d4edda; border-left: 4px solid #28a745; padding: 12px; margin: 8px 0; }}
  .deadline {{ background: #e8f4f8; border-left: 4px solid #17a2b8; padding: 12px; margin: 8px 0; }}
  .entity-tag {{ display: inline-block; background: #e9ecef; border-radius: 4px; padding: 2px 8px; font-size: 0.85em; color: #495057; }}
  ul {{ padding-left: 20px; }}
  li {{ margin: 6px 0; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 16px 0; }}
  .stat {{ background: #f8f9fa; padding: 12px; border-radius: 8px; text-align: center; }}
  .stat-number {{ font-size: 1.8em; font-weight: bold; color: #1a1a2e; }}
  .stat-label {{ font-size: 0.85em; color: #6c757d; }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #dee2e6; font-size: 0.85em; color: #6c757d; }}
</style>
</head>
<body>
<div style="background: #1a1a2e; border-radius: 8px; padding: 12px 20px; margin-bottom: 20px; text-align: center;">
  <a href="http://localhost:5050" style="color: #fff; font-weight: 600; font-size: 1.05em; text-decoration: none;">Open Dashboard to Mark Items Done &rarr;</a>
</div>
{body}
<div class="footer" style="margin-top:32px; padding-top:16px; border-top:1px solid #dee2e6; font-size:0.85em; color:#6c757d;">
  <p>Use the <a href="http://localhost:5050" style="color: #e94560; text-decoration: none; font-weight: 600;">web dashboard</a> to mark items done.</p>
  <p>Generated by your Briefing Bot on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
</div>
</body>
</html>"""
