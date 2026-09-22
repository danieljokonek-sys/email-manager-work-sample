"""Duplicate reconciliation for the daily brief.

Emails about the same real-world obligation arrive as several messages
(invoice, reminder, past-due notice), and each analysis pass inserts a fresh
row. Left alone, one obligation shows up as several board lines. This module
asks Claude to cluster the items on today's board that refer to the same
underlying thing, then merges each cluster into a single canonical row.

The survivor keeps the most recent sighting date, so a re-mentioned obligation
stays fresh against the undated fall-off in the board rules while one that
stops being mentioned still ages out. Absorbed rows are marked
``status='merged'`` with ``merged_into`` pointing at the survivor, never
deleted, so a merge is reversible.

One call per run, skipped when nothing could be a duplicate. Conservative by
design: when unsure, the prompt says not to merge, and the schema restricts a
merge to ids of one type.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from email_manager import db, prompts
from email_manager.config import Config
from email_manager.llm import ClaudeClient
from email_manager.schemas import MergePlan

log = logging.getLogger(__name__)


def _recency_key(item: Mapping[str, Any]) -> tuple[str, str, int]:
    return (item.get("source_date") or "", item.get("created_at") or "", int(item.get("id") or 0))


def reconcile_duplicates(
    claude: ClaudeClient, config: Config, board: Mapping[str, list[dict[str, Any]]]
) -> int:
    """Cluster and merge duplicate board items. Returns the number of rows merged away."""
    items = db.get_reconcilable_items(board)
    if not any(len(items.get(t, [])) >= 2 for t in db.bulletin.RECONCILE_TABLES):
        return 0

    payload = {t: items[t] for t in db.bulletin.RECONCILE_TABLES if items.get(t)}
    plan = claude.extract(
        "reconcile",
        system=prompts.load("reconcile_system"),
        user=prompts.load("reconcile_user").format(
            data_json=json.dumps(payload, separators=(",", ":"), default=str)
        ),
        schema=MergePlan,
        effort=config.analysis.effort,
        max_tokens=4000,
    )

    by_type = {t: {int(i["id"]): i for i in items.get(t, [])} for t in db.bulletin.RECONCILE_TABLES}
    merged = 0
    for merge in plan.merges:
        # Keep only ids that are real, still-pending items of the stated type.
        members = [by_type[merge.type][i] for i in merge.ids if i in by_type[merge.type]]
        if len(members) < 2:
            continue
        survivor = max(members, key=_recency_key)
        canonical_id = int(survivor["id"])
        dupe_ids = [int(m["id"]) for m in members if int(m["id"]) != canonical_id]
        if not dupe_ids:
            continue
        fields = merge.canonical.model_dump(exclude_none=True)
        # Force source_date to the most recent sighting so re-mentioned items stay fresh.
        fields["source_date"] = (
            survivor.get("source_date")
            or max((m.get("source_date") or "") for m in members)
            or None
        )
        merged += db.apply_item_merge(merge.type, canonical_id, dupe_ids, fields)
        log.info("Reconciled %d duplicate %s into id=%d", len(dupe_ids), merge.type, canonical_id)
    return merged
