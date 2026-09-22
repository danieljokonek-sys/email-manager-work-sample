"""Inbox classification and the actions taken on it, with the safety guards.

The label taxonomy comes from ``config.yaml``; a default set is used when none
is configured. The taxonomy drives three things: the category list in the
prompt, the enum in the output schema (so the model cannot invent a category),
and the protection rules that decide what may be trashed.

Safety model, in order:

1. Nothing is ever deleted permanently; ``trash_email`` moves to the
   provider's trash.
2. Age-based trashing only happens when the caller passes an explicit cutoff.
3. Categories marked ``vital`` are never trashed by age.
4. A low-confidence classification is never trashed; junk becomes archive.
5. An email with an attachment is never trashed; it is archived instead.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from email_manager import prompts
from email_manager.config import Config, LabelConfig
from email_manager.email_client import EmailClient
from email_manager.llm import ClaudeClient
from email_manager.schemas import Classification, classification_schema

log = logging.getLogger(__name__)

CLASSIFICATION_SNIPPET_CHARS = 1200

_COLOR_PALETTE = [
    "#fb4c2f", "#16a765", "#4a86e8", "#ffad47", "#a479e2",
    "#b65775", "#149e60", "#6d9eeb", "#cc3a21", "#b694e8",
    "#e07798", "#3c78d8", "#eba093", "#a46a21", "#fbc8d9",
    "#8e63ce", "#285bac", "#c9daf8", "#ac2b16", "#b9e4d0",
    "#ffd6a2", "#3dc789", "#a4c2f4", "#f691b3", "#98d7e4",
]  # fmt: skip

DEFAULT_LABELS: list[LabelConfig] = [
    LabelConfig(name="Personal", group="Personal", description="Friends, family, personal correspondence"),
    LabelConfig(name="Tax", group="Finance", description="Tax documents, W-2s, 1099s, CPA correspondence", protection="vital"),
    LabelConfig(name="Fees and Bills", group="Finance", description="Subscription charges, utility bills, payment confirmations"),
    LabelConfig(name="Banking and Investments", group="Finance", description="Bank statements, investment accounts, credit cards, loans", protection="vital"),
    LabelConfig(name="Legal", group="Legal & Government", description="Contracts, legal notices, attorney correspondence", protection="vital"),
    LabelConfig(name="Government", group="Legal & Government", description="DMV, agencies, voter registration, government correspondence"),
    LabelConfig(name="Product Purchases", group="Shopping", description="Order confirmations, receipts, warranty info"),
    LabelConfig(name="Licenses and Keys", group="Shopping", description="Software license keys, activation codes, serial numbers", protection="vital"),
    LabelConfig(name="Account Security", group="Security", description="Password resets, 2FA codes, security alerts", protection="vital"),
    LabelConfig(name="Logins and Verification", group="Security", description="Email verification, new device sign-ins, login confirmations"),
    LabelConfig(name="Health and Insurance", group="Health & Travel", description="Medical, dental, prescriptions, insurance claims", protection="vital"),
    LabelConfig(name="Travel", group="Health & Travel", description="Flight confirmations, hotel reservations, trip itineraries"),
    LabelConfig(name="Employment", group="Personal", description="Job-related, HR, payroll, benefits", protection="vital"),
    LabelConfig(name="Junk", group="", description="Marketing, spam, newsletters, unsolicited bulk email", protection="none", auto_delete=True),
]  # fmt: skip


@dataclass(frozen=True)
class LabelTaxonomy:
    keep: tuple[str, ...]
    junk: tuple[str, ...]
    vital: frozenset[str]
    label_paths: Mapping[str, str]
    colors: Mapping[str, dict[str, str]]
    descriptions: Mapping[str, str]

    @property
    def all_categories(self) -> tuple[str, ...]:
        return self.keep + self.junk

    @property
    def fallback(self) -> str:
        if "Personal" in self.keep:
            return "Personal"
        return self.keep[0] if self.keep else "Personal"

    def label_path(self, category: str) -> str:
        return self.label_paths.get(category, f"Personal/{category}")


def build_taxonomy(labels: Sequence[LabelConfig] | None) -> LabelTaxonomy:
    """Turn the configured labels into the structures the classifier and actions use."""
    labels = list(labels) if labels else list(DEFAULT_LABELS)
    keep: list[str] = []
    junk: list[str] = []
    vital: set[str] = set()
    paths: dict[str, str] = {}
    colors: dict[str, dict[str, str]] = {}
    descriptions: dict[str, str] = {}
    for i, lbl in enumerate(labels):
        descriptions[lbl.name] = lbl.description
        (junk if lbl.auto_delete else keep).append(lbl.name)
        if lbl.protection == "vital":
            vital.add(lbl.name)
        path = f"{lbl.group}/{lbl.name}" if lbl.group else lbl.name
        paths[lbl.name] = path
        colors[path] = {
            "textColor": "#ffffff",
            "backgroundColor": _COLOR_PALETTE[i % len(_COLOR_PALETTE)],
        }
    return LabelTaxonomy(
        keep=tuple(keep),
        junk=tuple(junk),
        vital=frozenset(vital),
        label_paths=paths,
        colors=colors,
        descriptions=descriptions,
    )


def build_classification_system_prompt(
    owner_name: str, account_emails: Sequence[str], taxonomy: LabelTaxonomy
) -> str:
    groups: dict[str, list[str]] = {}
    for cat in taxonomy.keep:
        path = taxonomy.label_paths.get(cat, cat)
        group = path.split("/")[0] if "/" in path else "Other"
        groups.setdefault(group, []).append(cat)

    lines: list[str] = []
    for group, cats in groups.items():
        lines.append(f"\n## {group} categories:")
        for cat in cats:
            desc = taxonomy.descriptions.get(cat, "")
            lines.append(f"- {cat}: {desc}" if desc else f"- {cat}")
    if taxonomy.junk:
        lines.append("\n## JUNK categories (trashed, no label):")
        for cat in taxonomy.junk:
            desc = taxonomy.descriptions.get(
                cat, "Marketing, spam, newsletters, unsolicited bulk email"
            )
            lines.append(f"- {cat}: ALL of the following get trashed: {desc}")

    return prompts.load("classification_system").format(
        owner_name=owner_name,
        account_emails=", ".join(account_emails),
        categories_text="\n".join(lines),
        fallback=taxonomy.fallback,
        today=date.today().isoformat(),
    )


@dataclass
class CleanupStats:
    labeled: int = 0
    archived: int = 0
    trashed: int = 0
    skipped: int = 0
    errors: int = 0
    decisions: list[str] = field(default_factory=list)
    """Human-readable per-email decisions (used by dry runs)."""

    def add(self, other: CleanupStats) -> None:
        self.labeled += other.labeled
        self.archived += other.archived
        self.trashed += other.trashed
        self.skipped += other.skipped
        self.errors += other.errors
        self.decisions.extend(other.decisions)

    def as_dict(self) -> dict[str, int]:
        return {
            "labeled": self.labeled,
            "archived": self.archived,
            "trashed": self.trashed,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def decide(
    email: Mapping[str, Any],
    result: Classification,
    taxonomy: LabelTaxonomy,
    cutoff: datetime | None,
) -> str:
    """Apply the safety guards to one classification. Returns 'trash', 'archive', or 'label'.

    Pure, so the guard logic is unit-tested in isolation from any mailbox.
    """
    is_junk = result.category in taxonomy.junk
    old_and_disposable = False
    if cutoff and not is_junk and result.category not in taxonomy.vital:
        try:
            email_date = datetime.fromisoformat(str(email.get("date", "")))
        except ValueError:
            email_date = None
        if email_date is not None:
            if email_date.tzinfo is None:
                email_date = email_date.replace(tzinfo=UTC)
            old_and_disposable = email_date < cutoff

    would_trash = is_junk or old_and_disposable
    if would_trash and result.confidence == "low":
        return "archive"
    if would_trash and email.get("has_attachment"):
        return "archive"
    if would_trash:
        return "trash"
    return result.action


class EmailOrganizer:
    def __init__(
        self, claude: ClaudeClient, config: Config, taxonomy: LabelTaxonomy | None = None
    ) -> None:
        self.claude = claude
        self.config = config
        self.taxonomy = taxonomy or build_taxonomy(config.labels)
        self.batch_size = config.cleanup.batch_size
        self._schema = classification_schema(list(self.taxonomy.all_categories))

    def classify_batch(self, emails: Sequence[Mapping[str, Any]]) -> dict[str, Classification]:
        if not emails:
            return {}
        payload = [
            {
                "id": e["id"],
                "sender": e.get("sender", ""),
                "recipients": e.get("recipients", []),
                "subject": e.get("subject", ""),
                "date": e.get("date", ""),
                "body_snippet": (e.get("body_snippet") or "")[:CLASSIFICATION_SNIPPET_CHARS],
                "labels": e.get("labels", []),
            }
            for e in emails
        ]
        system = build_classification_system_prompt(
            self.config.owner.name, self.config.cleanup.accounts, self.taxonomy
        )
        user = prompts.load("classification_user").format(
            categories=", ".join(self.taxonomy.all_categories),
            emails_json=json.dumps(payload, indent=1, default=str),
        )
        batch = self.claude.extract(
            "cleanup-classify",
            system=system,
            user=user,
            schema=self._schema,
            effort=self.config.cleanup.effort,
            max_tokens=8000,
        )
        results = batch.by_email_id()
        missing = [e["id"] for e in emails if e["id"] not in results]
        if missing:
            log.warning("Classification omitted %d of %d emails", len(missing), len(emails))
        return results

    def classify_and_act(
        self,
        emails: Sequence[Mapping[str, Any]],
        client: EmailClient,
        *,
        dry_run: bool = False,
        delete_older_than_days: int | None = None,
    ) -> tuple[CleanupStats, dict[str, Classification]]:
        """Classify a batch and label/archive/trash each email under the guards.

        Returns the stats and the classifications actually processed, so the
        caller can record them in the ledger. Emails the model omitted are
        counted as skipped and left for the next run.
        """
        stats = CleanupStats()
        classifications = self.classify_batch(emails)
        by_id = {e["id"]: e for e in emails}
        cutoff = (
            datetime.now(UTC) - timedelta(days=delete_older_than_days)
            if delete_older_than_days
            else None
        )

        for email_id, result in classifications.items():
            email = by_id.get(email_id, {})
            fate = decide(email, result, self.taxonomy, cutoff)
            if dry_run:
                stats.decisions.append(
                    f"{fate.upper():7} {result.category:<24} ({result.confidence}) {email.get('subject', '?')}"
                )
                self._count(stats, fate)
                continue
            try:
                if fate == "trash":
                    client.trash_email(email_id)
                else:
                    label_path = self.taxonomy.label_path(result.category)
                    label_id = client.get_or_create_label(
                        label_path, color=self.taxonomy.colors.get(label_path)
                    )
                    if fate == "archive":
                        client.apply_labels_and_actions(
                            email_id,
                            add_label_ids=[label_id] if label_id else None,
                            remove_label_ids=["INBOX"],
                        )
                    elif label_id:
                        client.apply_label(email_id, label_id)
                self._count(stats, fate)
            except Exception:
                log.warning("Failed to process %s", email_id, exc_info=True)
                stats.errors += 1
        stats.skipped = len(emails) - (
            stats.labeled + stats.archived + stats.trashed + stats.errors
        )
        return stats, classifications

    @staticmethod
    def _count(stats: CleanupStats, fate: str) -> None:
        if fate == "trash":
            stats.trashed += 1
        elif fate == "archive":
            stats.archived += 1
        else:
            stats.labeled += 1
