"""Bulletin-board digest: assemble today's board, have Claude write it, wrap it, send it.

The board is read-only. There is nothing to mark done: items age off by the
rules in :mod:`email_manager.db.bulletin`, so the only thing sent to Claude is
what is actually on the board today.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from email_manager import db
from email_manager.briefing.analyzer import Analyzer
from email_manager.config import Config
from email_manager.email_client import EmailClient

BOARD_SECTIONS = ("events", "deadlines", "action_items", "financial_items", "tasks", "follow_ups")
SUBJECT_PREFIXES = ("daily briefing", "weekly briefing")
"""Cleanup and follow-up detection key off these to leave the bot's own email alone."""


def is_own_briefing(subject: str | None) -> bool:
    """True for the digest the bot emails to the owner, and replies/forwards of it."""
    bare = (subject or "").strip()
    changed = True
    while changed:
        changed = False
        for prefix in ("re:", "fwd:", "fw:"):
            if bare.lower().startswith(prefix):
                bare = bare[len(prefix) :].strip()
                changed = True
    return bare.lower().startswith(SUBJECT_PREFIXES)


def _format_iso_to_12hr(iso_str: str) -> str:
    """'2026-09-17T14:30:00' -> 'Sep 17, 2026 at 2:30 PM'."""
    if not iso_str or "T" not in iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    return dt.strftime("%b %d, %Y at %I:%M %p").replace(" 0", " ")


def localize_times(obj: Any) -> Any:
    """Recursively rewrite ISO datetimes in start/end fields as 12-hour strings for the prompt."""
    keys = {"start_datetime", "end_datetime"}
    if isinstance(obj, dict):
        return {
            k: _format_iso_to_12hr(v) if k in keys and isinstance(v, str) else localize_times(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [localize_times(item) for item in obj]
    return obj


def strip_fences(html: str) -> str:
    """Remove a ```html fence if the model added one."""
    html = (html or "").strip()
    if html.startswith("```"):
        lines = html.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        html = "\n".join(lines)
    return html


class DigestGenerator:
    def __init__(
        self, analyzer: Analyzer, config: Config, send_client: EmailClient | None = None
    ) -> None:
        self.analyzer = analyzer
        self.config = config
        self.send_client = send_client

    def build_html(self, board: Mapping[str, list[dict[str, Any]]]) -> str:
        """The board as an HTML fragment (no wrapper). Does not send."""
        data: dict[str, Any] = localize_times(dict(board))
        if not any(data.get(k) for k in BOARD_SECTIONS):
            return "<h2>Today</h2><p>Nothing on the board. Enjoy the quiet.</p>"
        today = date.today()
        data["today"] = f"{today.strftime('%A')}, {today.isoformat()}"
        data["horizon_days"] = self.config.digest.days_ahead
        data["entities"] = {k: e.name for k, e in self.config.entities.items()}
        return strip_fences(self.analyzer.write_bulletin(data))

    def send(self, html: str, digest_type: str = "daily") -> str:
        """Email the wrapped board. Returns the subject line."""
        if self.send_client is None:
            raise RuntimeError("No send client configured for the digest")
        label = "Daily" if digest_type == "daily" else "Weekly"
        subject = f"{label} Briefing: {date.today().strftime('%b %d, %Y')}"
        self.send_client.send_email(
            to=self.config.digest.send_to, subject=subject, body_html=self.wrap_html(html)
        )
        return subject

    @staticmethod
    def wrap_html(body: str) -> str:
        today = date.today().strftime("%A, %B %d")
        generated = datetime.now().strftime("%B %d, %Y at %I:%M %p")
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
  <p>Generated {generated}</p>
</div>
</body>
</html>"""


def collect_board(config: Config) -> dict[str, list[dict[str, Any]]]:
    """Today's board from the database, with the owner's own addresses excluded from follow-ups."""
    return db.get_bulletin_items(
        days_ahead=config.digest.days_ahead,
        owner_name=config.owner.first_name,
        exclude_recipients=config.own_addresses,
    )
