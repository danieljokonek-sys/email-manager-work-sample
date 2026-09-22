"""Claude-powered extraction and the bulletin-board writer.

Two jobs, one class:

* :meth:`Analyzer.analyze_batch` turns a batch of emails into structured
  items (agreements, deadlines, money, action items) using a schema-constrained
  call, so the result is a validated :class:`EmailExtraction` per email.
* :meth:`Analyzer.write_bulletin` turns today's board into the two-section
  HTML the digest emails.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from email_manager import prompts
from email_manager.config import Config, EntityConfig
from email_manager.llm import ClaudeClient
from email_manager.schemas import EmailExtraction, ExtractionBatch

log = logging.getLogger(__name__)

# Body text sent per email for extraction. Enough for the ask, the amount, and
# the date; the rest is signature and quoted history.
EXTRACTION_SNIPPET_CHARS = 1500


def describe_entities(entities: Mapping[str, EntityConfig]) -> str:
    lines = []
    for key, entity in entities.items():
        line = f"- {entity.name} (key: {key}): {entity.description}"
        if entity.partner:
            line += f" (partner: {entity.partner})"
        lines.append(line)
    return "\n".join(lines) or "- (none configured)"


class Analyzer:
    def __init__(self, claude: ClaudeClient, config: Config) -> None:
        self.claude = claude
        self.config = config
        self.entities = config.entities
        self.owner = config.owner

    # ── extraction ──────────────────────────────────────────────────────────

    def analyze_batch(self, emails: Sequence[Mapping[str, Any]]) -> dict[str, EmailExtraction]:
        """Extract items from a batch. Returns extractions keyed by email id.

        Raises :class:`email_manager.llm.LLMOutputError` (or an SDK error) if the
        call fails; a failed batch is left unprocessed so it is retried next run.
        """
        if not emails:
            return {}
        entity_keys = [*self.entities.keys(), "personal"]
        payload = [
            {
                "id": e["id"],
                "sender": e.get("sender", ""),
                "recipients": e.get("recipients", []),
                "subject": e.get("subject", ""),
                "date": e.get("date", ""),
                "body_snippet": (e.get("body_snippet") or "")[:EXTRACTION_SNIPPET_CHARS],
            }
            for e in emails
        ]
        system = prompts.load("extraction_system").format(
            entities_description=describe_entities(self.entities),
            today=date.today().isoformat(),
        )
        user = prompts.load("extraction_user").format(
            entity_keys=", ".join(entity_keys),
            emails_json=json.dumps(payload, indent=1, default=str),
        )
        batch = self.claude.extract(
            "extract",
            system=system,
            user=user,
            schema=ExtractionBatch,
            effort=self.config.analysis.effort,
        )
        results = batch.by_email_id()
        missing = [e["id"] for e in emails if e["id"] not in results]
        if missing:
            log.warning(
                "Extraction omitted %d of %d emails; they will be retried",
                len(missing),
                len(emails),
            )
        return results

    # ── bulletin board ──────────────────────────────────────────────────────

    def write_bulletin(self, board: Mapping[str, Any]) -> str:
        """Write the board as HTML. ``board`` is the trimmed output of get_bulletin_items plus context keys."""
        owner = self.owner.first_name or "the owner"
        profile = f"\nAbout {owner}:\n{self.owner.profile}\n" if self.owner.profile else ""
        priorities = (
            f"\nWhat {owner} cares about most:\n{self.owner.briefing_priorities}\n"
            if self.owner.briefing_priorities
            else ""
        )
        system = prompts.load("bulletin_system").format(
            owner=owner,
            profile=profile,
            priorities=priorities,
            entities="\n".join(f"- {k}: {e.name}" for k, e in self.entities.items()) or "- (none)",
            horizon_days=board.get("horizon_days", self.config.digest.days_ahead),
        )
        user = (
            f"Today is {board.get('today')}. Board data (JSON):\n"
            f"{json.dumps(board, separators=(',', ':'), default=str)}\n\n"
            "Write the bulletin board HTML."
        )
        # The visible board is about 1K tokens; the headroom is for reasoning,
        # which counts against max_tokens on Sonnet 5.
        return self.claude.text(
            "bulletin",
            system=system,
            user=user,
            effort=self.config.digest.effort,
            max_tokens=8000,
        )
