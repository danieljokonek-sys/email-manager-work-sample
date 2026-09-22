"""Pydantic schemas for everything Claude returns.

These are the contracts between the prompts and the rest of the app. They are
passed to the Messages API as the structured-output format, so the model is
constrained to them server-side, and pydantic validates the result client-side.
Field descriptions are part of the prompt: keep them precise.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model

Priority = Literal["high", "medium", "low"]
Confidence = Literal["high", "medium", "low"]


# ── Extraction (briefing) ───────────────────────────────────────────────────


class Agreement(BaseModel):
    entity_key: str = Field(description="Entity key this relates to, or 'personal'.")
    summary: str = Field(description="What was agreed, in one sentence.")
    parties: list[str] = Field(default_factory=list, description="Names of the parties.")
    terms: str | None = Field(default=None, description="Key terms, if stated.")
    source_date: str | None = Field(default=None, description="YYYY-MM-DD the email was sent.")


class Deadline(BaseModel):
    entity_key: str
    description: str = Field(description="What is due.")
    due_date: str | None = Field(default=None, description="YYYY-MM-DD, or null if unknown.")
    priority: Priority = "medium"
    source_date: str | None = None


class FinancialItem(BaseModel):
    entity_key: str
    direction: Literal["receivable", "payable"] = Field(
        description="'receivable' = money owed TO the owner; 'payable' = money the owner owes."
    )
    counterparty: str | None = Field(default=None, description="Who owes or is owed.")
    amount: float | None = Field(default=None, description="Amount, or null if not stated.")
    currency: str = "USD"
    description: str | None = Field(default=None, description="What it is for.")
    due_date: str | None = Field(default=None, description="YYYY-MM-DD, or null.")
    source_date: str | None = None
    status: Literal["pending", "resolved"] = Field(
        default="pending",
        description="'pending' = still needs to be paid or collected; 'resolved' = already paid, charged, or received.",
    )


class ActionItem(BaseModel):
    entity_key: str
    description: str = Field(description="What needs to be done.")
    assigned_to: str | None = Field(
        default=None, description="Who should do it ('owner' for the user)."
    )
    due_date: str | None = Field(default=None, description="YYYY-MM-DD, or null.")
    priority: Priority = "medium"
    source_date: str | None = None


class EmailExtraction(BaseModel):
    email_id: str = Field(description="The id of the email these items came from, copied exactly.")
    entity_key: str = Field(
        description="Best matching entity key for the whole email, or 'personal'."
    )
    summary: str = Field(description="One or two sentences on what the email is about.")
    agreements: list[Agreement] = Field(default_factory=list)
    deadlines: list[Deadline] = Field(default_factory=list)
    financial_items: list[FinancialItem] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)


class ExtractionBatch(BaseModel):
    """One entry per email in the batch, in any order. Emails with nothing to extract still get an entry with empty lists."""

    emails: list[EmailExtraction]

    def by_email_id(self) -> dict[str, EmailExtraction]:
        return {e.email_id: e for e in self.emails}


# ── Classification (cleanup) ────────────────────────────────────────────────


class Classification(BaseModel):
    email_id: str = Field(description="The id of the email, copied exactly.")
    category: str = Field(description="Exactly one of the allowed category names.")
    action: Literal["label", "archive"] = Field(
        description="'label' keeps it in the inbox with a label; 'archive' labels it and removes it from the inbox."
    )
    confidence: Confidence
    reason: str = Field(description="One sentence.")


class ClassificationBatch(BaseModel):
    classifications: list[Classification]

    def by_email_id(self) -> dict[str, Classification]:
        return {c.email_id: c for c in self.classifications}


def classification_schema(categories: list[str]) -> type[ClassificationBatch]:
    """Build a ClassificationBatch whose ``category`` is an enum of the configured labels.

    The label taxonomy comes from config.yaml, so the enum is built at runtime.
    With the enum in the schema the model cannot invent a category; the prompt
    still explains what each category means.
    """
    if not categories:
        return ClassificationBatch
    category_type = Literal[tuple(categories)]  # type: ignore[valid-type]
    item = create_model(
        "ConfiguredClassification",
        __base__=Classification,
        category=(category_type, Field(description="Exactly one of the allowed category names.")),
    )
    batch = create_model(
        "ConfiguredClassificationBatch",
        __base__=ClassificationBatch,
        classifications=(list[item], ...),  # type: ignore[valid-type]
    )
    return batch


# ── Duplicate reconciliation ────────────────────────────────────────────────

ItemType = Literal["financial_items", "action_items", "deadlines"]


class MergeCanonical(BaseModel):
    """Synthesised fields for the surviving row. Only fields that apply to the item type are used."""

    description: str | None = None
    counterparty: str | None = None
    amount: float | None = None
    currency: str | None = None
    due_date: str | None = Field(default=None, description="YYYY-MM-DD or null.")
    assigned_to: str | None = None
    priority: Priority | None = None


class Merge(BaseModel):
    type: ItemType
    ids: list[int] = Field(
        description="Two or more ids of the SAME type that describe the same real-world item."
    )
    canonical: MergeCanonical


class MergePlan(BaseModel):
    merges: list[Merge] = Field(
        default_factory=list, description="Empty when there are no duplicates."
    )
