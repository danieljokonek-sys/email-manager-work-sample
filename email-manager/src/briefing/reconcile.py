"""
Duplicate reconciliation for the daily brief.

Emails about the same real-world obligation arrive across multiple messages
(invoice -> reminder -> past-due notice), and each analysis pass inserts a
fresh row. Left alone, one obligation shows up as several board lines. This
module asks Claude to cluster the items on today's board that refer to the
same underlying thing, then merges each cluster into a single canonical row in
tracker.db. Only board items are sent, which keeps the call small.

The survivor keeps the MOST RECENT sighting date, so a re-mentioned obligation
stays fresh against the undated fall-off in database.get_bulletin_items while
one that stops being mentioned still ages out. Absorbed rows are never deleted
— they are marked status='merged' with merged_into pointing at the survivor,
so the merge is reversible.

One Claude call per run, skipped when nothing could be a duplicate.
Conservative by design: when unsure, it does not merge.
"""
import json
import logging

import anthropic

from src.briefing import database as db
from src.llm_usage import log_usage

log = logging.getLogger(__name__)

_TYPES = ("financial_items", "action_items", "deadlines")

RECONCILE_SYSTEM_PROMPT = """You deduplicate a personal daily brief.

You are given lists of currently-pending tracked items (financial items, action items, deadlines) that were extracted from many separate emails. Different emails frequently describe the SAME real-world obligation — an invoice, then a reminder, then a past-due notice; or a back-and-forth email thread about one task. Your job is to find clusters of items that refer to the SAME underlying real-world thing so they can be merged into one.

STRICT RULES:
- Only cluster items of the SAME type (financial with financial, action with action, deadline with deadline). Never mix types.
- Only merge when you are confident the items are the SAME obligation or task — not merely similar, related, or from the same counterparty. When in doubt, DO NOT merge.
- Different instances of a recurring bill are DIFFERENT items. June rent and July rent are different. Two different monthly statements are different. Do NOT merge them.
- Two different invoices from the same vendor for different work are DIFFERENT.
- A payment reminder, past-due notice, or updated statement for the SAME invoice/balance IS the same item — merge it, even if the amount was updated or the wording differs.
- A reply/continuation of the same task thread IS the same action item — merge it.
- For each cluster, synthesize ONE canonical version using the most accurate and up-to-date details across the members: the latest known amount, the latest/most specific due date, and the clearest single-line description.

Respond with valid JSON only."""

RECONCILE_USER_PROMPT = """Here are the pending items, grouped by type. Each item has a stable numeric "id".

DATA:
{data_json}

Find clusters of duplicates. Respond with a JSON object:
{{
  "merges": [
    {{
      "type": "<financial_items|action_items|deadlines>",
      "ids": [<two or more ids from that SAME type that are the same real-world item>],
      "canonical": {{ <synthesized fields for that type, see below> }}
    }}
  ]
}}

Canonical fields by type:
- financial_items: counterparty, amount (number or null), currency, description, due_date ("YYYY-MM-DD" or null)
- action_items: description, assigned_to, due_date ("YYYY-MM-DD" or null), priority ("high"|"medium"|"low")
- deadlines: description, due_date ("YYYY-MM-DD" or null), priority ("high"|"medium"|"low")

Only include clusters with 2 or more ids. If there are no duplicates, respond with {{"merges": []}}."""


def _parse_json_response(text: str) -> dict:
    """Parse JSON from Claude's response, tolerating markdown fences."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return {}


def _recency_key(item: dict):
    """Sort key: most recently sighted item wins (survivor selection)."""
    return (item.get("source_date") or "", item.get("created_at") or "", item.get("id") or 0)


def reconcile_duplicates(model: str = "claude-sonnet-5", effort: str = "low",
                         board: dict | None = None) -> int:
    """Cluster and merge duplicate board items. Returns count of rows merged away.

    ``board`` is the output of database.get_bulletin_items(); when omitted it
    is fetched here.
    """
    items = db.get_reconcilable_items(board)

    # Nothing can be a duplicate unless some type has at least two items.
    if not any(len(items.get(t, [])) >= 2 for t in _TYPES):
        return 0

    payload = {t: items.get(t, []) for t in _TYPES if items.get(t)}
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=3000,
        output_config={"effort": effort},
        system=RECONCILE_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": RECONCILE_USER_PROMPT.format(
                data_json=json.dumps(payload, separators=(",", ":"), default=str)
            ),
        }],
    )
    log_usage("reconcile", response)

    text = ""
    for block in response.content:
        if block.type == "text":
            text = block.text
            break
    parsed = _parse_json_response(text)
    merges = parsed.get("merges", []) if isinstance(parsed, dict) else []

    # Index items by (type, id) so we can validate ids and pick survivors.
    by_type = {t: {int(i["id"]): i for i in items.get(t, [])} for t in _TYPES}

    merged_count = 0
    for merge in merges:
        if not isinstance(merge, dict):
            continue
        item_type = merge.get("type")
        if item_type not in _TYPES:
            continue
        # Keep only ids that are real, still-pending items of this type.
        try:
            ids = [int(i) for i in merge.get("ids", [])]
        except (TypeError, ValueError):
            continue
        members = [by_type[item_type][i] for i in ids if i in by_type[item_type]]
        if len(members) < 2:
            continue

        # Survivor = most recently sighted; fall-off measures age from here.
        survivor = max(members, key=_recency_key)
        canonical_id = int(survivor["id"])
        dupe_ids = [int(m["id"]) for m in members if int(m["id"]) != canonical_id]
        if not dupe_ids:
            continue

        # Trust the LLM's synthesized fields, but force source_date to the most
        # recent sighting across the cluster so re-mentioned items stay fresh.
        canonical_fields = dict(merge.get("canonical") or {})
        canonical_fields["source_date"] = survivor.get("source_date") \
            or max((m.get("source_date") or "") for m in members) or None

        db.apply_item_merge(item_type, canonical_id, dupe_ids, canonical_fields)
        merged_count += len(dupe_ids)
        log.info(
            "Reconciled %d duplicate %s into id=%d",
            len(dupe_ids), item_type, canonical_id,
        )

    return merged_count
