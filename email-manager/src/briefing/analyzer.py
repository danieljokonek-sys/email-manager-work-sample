"""
Claude-powered email analysis engine.

Processes batches of emails through Claude to extract:
- Agreements and commitments
- Deadlines and due dates
- Financial obligations (who owes what, to which entity)
- Action items
- Chorus Crafters song orders (dedicated extraction pass)
"""
import json
import anthropic
from datetime import date

from src.entities import Entity
from src.briefing.orders import OrderExtractor
from src.briefing import database as db
from src.llm_usage import log_usage


EXTRACTION_SYSTEM_PROMPT = """You are a personal assistant AI that analyzes email communications for a musician/entrepreneur who runs multiple businesses:

BUSINESS ENTITIES:
{entities_description}

Each entity above includes a description of what it is and what to look for. Use those
descriptions to decide which entity an email relates to and what kind of items it implies
(client work, revenue, expenses, deadlines, and so on).

YOUR JOB: Analyze each email and extract any actionable information. Be thorough but precise — only extract items that are clearly stated or strongly implied in the communication. Do not fabricate or speculate.

For financial items:
- "receivable" = money owed TO the owner (someone should pay them)
- "payable" = money the owner owes to someone else
- Always try to identify which business entity the financial item relates to
- Include the counterparty name (who owes or is owed)
- For a service or creative business: a client commission/order = receivable; paying a contractor or collaborator = payable
- CRITICAL: Set status to distinguish unpaid vs already-paid:
  - "pending" = money that STILL NEEDS to be paid or collected (unpaid invoices, outstanding bills, upcoming due dates)
  - "resolved" = money already paid/received/charged (purchase receipts, subscription charges already processed, payments confirmed, auto-debits already taken, credit card charges, Venmo/PayPal payments sent)
  - Credit card purchases, debit charges, and any transaction already processed = "resolved". The owner already paid — it's not an outstanding debt.
  - If the email is a receipt, confirmation, order confirmation, or shows the charge was already processed, use status "resolved" — do NOT mark it "pending"
- For credit card statements and loan statements:
  - Extract the outstanding balance as a financial item with direction "payable"
  - Use status "pending" since the balance is owed
  - Set the counterparty to the card/lender name (e.g. "Visa ending 1234", "Mortgage lender")
  - Set description to include "Statement balance" or "Outstanding balance"
  - Set due_date to the payment due date from the statement

For deadlines:
- ONLY extract a deadline if it meets ONE of these criteria:
  1. CALENDAR-LINKED CLIENT DEADLINE: a client-facing date that ties to a calendar event — client delivery dates, booking/event/show dates, lease or rent milestones, or other entity-specific commitments
  2. FINANCIAL: payment due dates, invoice deadlines, tax filing dates, statement-balance due dates
  3. EXPLICIT FOLLOW-UP REQUEST: the sender definitively asked for a response or decision BY a specific date
- Do NOT extract: marketing dates, generic "by end of week" asks, internal soft targets, mentions of dates in passing ("we'll get back to you next week"), speculative or aspirational dates, newsletter deadlines, social plans
- When in doubt, DO NOT extract. A tighter list is better than a noisy one.
- Priority: "high" for < 3 days or explicit urgency, "medium" for < 2 weeks, "low" otherwise

For agreements:
- Capture any commitments, verbal contracts, promises made in either direction
- For creative/service work: note relevant details (scope, event type, parties involved, revisions included)
- Note the parties involved

For action items:
- ONLY extract an action item if it meets ONE of these criteria:
  1. CALENDAR-LINKED CLIENT WORK: something specific the owner must do that ties to a calendar event/deadline (deliver a client deliverable, send a revision, prepare for an upcoming event)
  2. FINANCIAL: send/sign a contract, pay a bill, send an invoice, follow up on a payment, confirm a transfer
  3. DEFINITIVE FOLLOW-UP: the sender explicitly asked the owner a question or decision and is waiting for a reply
- Do NOT extract: vague suggestions, FYIs, newsletters, automated notifications, social pleasantries, "would be nice if" items, things the owner is already aware of from calendar, marketing/promotional emails
- When in doubt, DO NOT extract. Quiet over noisy.

If an email is purely social, a newsletter, or marketing — return empty arrays for all fields. Don't force-fit items that aren't there.

Today's date is {today}.

Respond with valid JSON only."""


EXTRACTION_USER_PROMPT = """Analyze these emails and extract actionable items. For each email, determine which business entity (if any) it relates to, and extract agreements, deadlines, financial items, and action items.

Entity keys to use: {entity_keys}
If an item doesn't clearly relate to any entity, use "personal" as the entity_key.

EMAILS TO ANALYZE:
{emails_json}

Respond with a JSON object mapping email IDs to their extractions:
{{
  "<email_id>": {{
    "entity_key": "<best matching entity key or 'personal'>",
    "summary": "<1-2 sentence summary of what this email is about>",
    "agreements": [
      {{
        "entity_key": "<entity>",
        "summary": "<what was agreed>",
        "parties": ["<name1>", "<name2>"],
        "terms": "<key terms if any>",
        "source_date": "<YYYY-MM-DD>"
      }}
    ],
    "deadlines": [
      {{
        "entity_key": "<entity>",
        "description": "<what is due>",
        "due_date": "<YYYY-MM-DD or null>",
        "priority": "<high|medium|low>",
        "source_date": "<YYYY-MM-DD>"
      }}
    ],
    "financial_items": [
      {{
        "entity_key": "<entity>",
        "direction": "<receivable|payable>",
        "counterparty": "<name of person/org>",
        "amount": <number or null>,
        "currency": "USD",
        "description": "<what it's for>",
        "due_date": "<YYYY-MM-DD or null>",
        "source_date": "<YYYY-MM-DD>",
        "status": "<pending|resolved>"
      }}
    ],
    "action_items": [
      {{
        "entity_key": "<entity>",
        "description": "<what needs to be done>",
        "assigned_to": "<who should do it>",
        "due_date": "<YYYY-MM-DD or null>",
        "priority": "<high|medium|low>",
        "source_date": "<YYYY-MM-DD>"
      }}
    ]
  }}
}}

Only include items that are clearly present in the emails. Empty arrays are fine."""


class Analyzer:
    def __init__(self, entities: dict[str, Entity], model: str = "claude-sonnet-5",
                 owner_profile: str = "", briefing_priorities: str = "",
                 effort: str = "low", owner_name: str = ""):
        self.client = anthropic.Anthropic()
        self.entities = entities
        self.model = model
        self.effort = effort
        self.owner_name = owner_name
        self.owner_profile = owner_profile
        self.briefing_priorities = briefing_priorities
        self._order_extractor = OrderExtractor(model=model, effort=effort)

    def _build_entities_description(self) -> str:
        parts = []
        for key, entity in self.entities.items():
            desc = f"- {entity.name} (key: {key}): {entity.description}"
            if entity.partner:
                desc += f" — partner: {entity.partner}"
            parts.append(desc)
        return "\n".join(parts)

    def analyze_batch(self, emails: list[dict]) -> dict:
        """Analyze a batch of emails and return extractions keyed by email ID.

        Also runs a dedicated Chorus Crafters order extraction pass on any
        email classified as chorus_crafters.
        """
        if not emails:
            return {}

        entities_desc = self._build_entities_description()
        entity_keys = list(self.entities.keys()) + ["personal"]

        emails_for_prompt = []
        for e in emails:
            emails_for_prompt.append({
                "id": e["id"],
                "sender": e.get("sender", ""),
                "recipients": e.get("recipients", []),
                "subject": e.get("subject", ""),
                "date": e.get("date", ""),
                "body_snippet": e.get("body_snippet", "")[:1500],
            })

        system_prompt = EXTRACTION_SYSTEM_PROMPT.format(
            entities_description=entities_desc,
            today=date.today().isoformat(),
        )

        user_prompt = EXTRACTION_USER_PROMPT.format(
            entity_keys=", ".join(entity_keys),
            emails_json=json.dumps(emails_for_prompt, indent=2),
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            output_config={"effort": self.effort},
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        log_usage("extract", response)

        results = {}
        for block in response.content:
            if block.type == "text":
                results = self._parse_json_response(block.text)
                break

        # Second pass: dedicated order extraction for Chorus Crafters emails
        for email in emails:
            entity_key = (
                results.get(email["id"], {}).get("entity_key")
                or email.get("entity_key")
            )
            if entity_key == "chorus_crafters":
                order_data = self._order_extractor.extract_from_email(email)
                if order_data:
                    order_id = db.upsert_song_order(order_data)
                    # Tag extraction result so caller knows an order was created
                    if email["id"] in results:
                        results[email["id"]]["song_order_id"] = order_id

        return results

    def _parse_json_response(self, text: str) -> dict:
        """Parse JSON from Claude's response, handling markdown code blocks."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Drop first and last lines (```json and ```)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            return {}

    def generate_bulletin(self, board: dict, effort: str = "medium") -> str:
        """Write the daily bulletin board (Today / Next Two Weeks) as HTML.

        ``board`` is the trimmed output of database.get_bulletin_items plus
        ``today``, ``horizon_days`` and ``entities`` (see digest.py). Only what
        is on the board is sent; the output is capped short on purpose.
        """
        owner = self.owner_name or "the owner"

        profile_block = ""
        if self.owner_profile:
            profile_block = f"\nAbout {owner}:\n{self.owner_profile}\n"

        priorities_block = ""
        if self.briefing_priorities:
            priorities_block = f"\nWhat {owner} cares about most:\n{self.briefing_priorities}\n"

        system_prompt = BULLETIN_SYSTEM_PROMPT.format(
            owner=owner,
            profile=profile_block,
            priorities=priorities_block,
            entities=self._build_entities_description(),
            horizon_days=board.get("horizon_days", 14),
        )

        user_prompt = (
            f"Today is {board.get('today')}. Board data (JSON):\n"
            f"{json.dumps(board, separators=(',', ':'), default=str)}\n\n"
            "Write the bulletin board HTML."
        )

        # Thinking tokens count against max_tokens on Sonnet 5. The visible
        # board is ~1K tokens; the headroom is for the model's reasoning.
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            output_config={"effort": effort},
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        log_usage("bulletin", response)

        for block in response.content:
            if block.type == "text":
                return block.text
        return "<p>No board generated: the API returned an empty response.</p>"


BULLETIN_SYSTEM_PROMPT = """You write a short daily bulletin board email for {owner}. It is read-only: nothing gets checked off, so say only what matters and say it once.
{profile}{priorities}
Business entities (entity_key: name):
{entities}

Output clean HTML for email using only <h2>, <h3>, <ul>, <li>, and <b>. Exactly two sections, in this order.

<h2>Today</h2>
The top things to be aware of today, most important first. At most 7 bullets. Draw from: calendar events today, anything due today or overdue (say how many days overdue), high-priority items landing tomorrow, and money moving today. One line per bullet: what it is, who it is with, and the time or date. If fewer than 7 things genuinely matter today, list fewer. Never pad.

<h2>Next Two Weeks</h2>
Chronological, starting tomorrow, covering the next {horizon_days} days. One <h3> per day that has something on it, formatted like "Thu Sep 17". Under each day, bullets for calendar events (with 12-hour time), deadlines, action items, and money due that day. Skip days with nothing. Then a final <h3>No date yet</h3> with at most 6 bullets for undated items still worth knowing (recent money items, threads waiting on a reply, tasks). Omit that block if nothing is worth listing.

Rules:
- Several entries that describe the same real-world thing: write it once, as one bullet.
- Times in 12-hour format with AM/PM. Never 24-hour time.
- Money lines are declarative, prefixed "+ " for money coming in and "− " for money going out. Example: "+ $400 from a client, final balance on a commission". Example: "− $229 gym dues, Oct 1". No "you owe", "collect", or "pay" phrasing.
- Do not invent or speculate. Use only the data given.
- No intro, no summary, no stats, no advice, no closing line. Just the two sections."""
