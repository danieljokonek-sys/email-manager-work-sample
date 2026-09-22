"""
Bulletin-board digest.

Builds the day's board from tracker.db (database.get_bulletin_items), has
Claude write it up as two sections (Today / Next Two Weeks), and emails it.

The board is read-only. There is nothing to mark done: items age off by the
time rules in get_bulletin_items(), so the only thing sent to Claude is what
is actually on the board today.
"""
from datetime import date, datetime

from src.briefing import database as db
from src.briefing.analyzer import Analyzer
from src.email_client import EmailClient
from src.entities import Entity


BOARD_SECTIONS = ("events", "deadlines", "action_items", "financial_items",
                  "tasks", "follow_ups")


def _format_iso_to_12hr(iso_str: str) -> str:
    """Convert an ISO datetime string to 'Sep 17, 2026 at 2:30 PM'."""
    if not iso_str or "T" not in iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%b %d, %Y at %I:%M %p").replace(" 0", " ")
    except (ValueError, TypeError):
        return iso_str


def _localize_times(obj):
    """Recursively convert ISO datetime strings in dicts/lists to 12-hour format."""
    datetime_keys = {"start_datetime", "end_datetime"}
    if isinstance(obj, dict):
        return {
            k: _format_iso_to_12hr(v) if k in datetime_keys and isinstance(v, str) else _localize_times(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_localize_times(item) for item in obj]
    return obj


def _strip_fences(html: str) -> str:
    html = (html or "").strip()
    if html.startswith("```"):
        lines = html.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        html = "\n".join(lines)
    return html


class DigestGenerator:
    def __init__(
        self,
        analyzer: Analyzer,
        send_client: EmailClient | None,
        entities: dict[str, Entity],
        send_to: str,
        days_ahead: int = db.BULLETIN_DAYS_AHEAD,
        effort: str = "medium",
    ):
        self.analyzer = analyzer
        self.send_client = send_client
        self.entities = entities
        self.send_to = send_to
        self.days_ahead = days_ahead
        self.effort = effort

    def collect_board(self) -> dict:
        return db.get_bulletin_items(days_ahead=self.days_ahead)

    def build_html(self, board: dict | None = None) -> str:
        """Return the board as an HTML fragment (no wrapper). Does not send."""
        board = board if board is not None else self.collect_board()
        data = _localize_times(board)
        if not any(data.get(k) for k in BOARD_SECTIONS):
            return self._empty_html()

        today = date.today()
        data["today"] = f"{today.strftime('%A')}, {today.isoformat()}"
        data["horizon_days"] = self.days_ahead
        data["entities"] = {k: e.name for k, e in self.entities.items()}
        html = self.analyzer.generate_bulletin(data, effort=self.effort)
        return _strip_fences(html)

    def generate_and_send(self, digest_type: str = "daily", board: dict | None = None) -> str:
        """Build the board and email it. Returns the HTML fragment."""
        if self.send_client is None:
            raise RuntimeError("No send client configured for the digest")
        html = self.build_html(board)
        # Keep the "Daily/Weekly Briefing" prefix: cleanup and follow-up
        # detection both key off it to leave the bot's own email alone.
        label = "Daily" if digest_type == "daily" else "Weekly"
        subject = f"{label} Briefing — {date.today().strftime('%b %d, %Y')}"
        self.send_client.send_email(
            to=self.send_to,
            subject=subject,
            body_html=self._wrap_html(html),
        )
        return html

    def _empty_html(self) -> str:
        return "<h2>Today</h2><p>Nothing on the board. Enjoy the quiet.</p>"

    def _wrap_html(self, body: str) -> str:
        """Wrap the board in a minimal styled email template."""
        today = date.today().strftime("%A, %B %d")
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #222; max-width: 680px; margin: 0 auto; padding: 20px; line-height: 1.45; }}
  .board-date {{ color: #6c757d; font-size: 0.9em; margin: 0 0 4px; }}
  h2 {{ color: #1a1a2e; border-bottom: 2px solid #e94560; padding-bottom: 6px; margin-top: 28px; }}
  h3 {{ color: #16213e; margin: 18px 0 6px; font-size: 1em; }}
  ul {{ padding-left: 20px; margin: 4px 0; }}
  li {{ margin: 5px 0; }}
  .footer {{ margin-top: 32px; padding-top: 12px; border-top: 1px solid #dee2e6; font-size: 0.8em; color: #6c757d; }}
</style>
</head>
<body>
<p class="board-date">{today}</p>
{body}
<div class="footer">
  <p>Bulletin board. Items drop off on their own as their dates pass.</p>
  <p>Generated {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
</div>
</body>
</html>"""
