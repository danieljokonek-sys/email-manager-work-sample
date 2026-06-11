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
  - Set the counterparty to the card/lender name (e.g. "Chase Visa ending 8454", "Wells Fargo Mortgage")
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
    def __init__(self, entities: dict[str, Entity], model: str = "claude-opus-4-6",
                 owner_profile: str = "", briefing_priorities: str = ""):
        self.client = anthropic.Anthropic()
        self.entities = entities
        self.model = model
        self.owner_profile = owner_profile
        self.briefing_priorities = briefing_priorities
        self._order_extractor = OrderExtractor(model=model)

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
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

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

    def generate_digest_analysis(self, digest_data: dict) -> str:
        """Use Claude to generate a natural-language digest summary."""
        owner_name = self.owner_profile.split("\n")[0].strip() if self.owner_profile else "the user"

        # Build profile context block
        profile_block = ""
        if self.owner_profile:
            profile_block = f"\n\nABOUT THE OWNER:\n{self.owner_profile}"

        # Build priorities block
        priorities_block = ""
        if self.briefing_priorities:
            priorities_block = f"\n\nBRIEFING PRIORITIES (what the owner cares about most):\n{self.briefing_priorities}"

        system_prompt = """You are a sharp, concise personal assistant writing a daily briefing email. Write in a warm but direct tone, like a trusted chief of staff.
{profile}{priorities}

Business entities:
{entities}

Format the briefing as clean HTML for email. Use headers, bullet points, and bold for emphasis. Keep it scannable.

IMPORTANT: Always display times in 12-hour format with AM/PM (e.g., "2:30 PM", "9:00 AM"). Never use 24-hour / military time (e.g., "14:30", "09:00"). Convert any 24-hour times in the data to 12-hour format.

Include these sections (skip any with nothing to report):
1. <h2>Headlines</h2> — ALWAYS THE FIRST SECTION. Your editorial pick of the top 3 to 5 things the owner should know today, drawn from EVERYTHING you have (calendar, deadlines, financial items, action items, tasks, agreements, follow-up threads, and what you know about the owner's profile and priorities). NEVER more than 5 items. Each headline should be a single tight line — what it is, who it's with or about, and why it matters today specifically. Lead with the single most important thing. If there are fewer than 3 truly notable items, show fewer — do not pad. These are the cream of the crop, not a duplicate of the lists below.
2. <h2>Urgent</h2> — anything due today or overdue, calendar events today/tomorrow, AND any action items or tasks that have been sitting unresolved for 3+ days (call these out by name with how many days they've been waiting)
3. <h2>14-Day Horizon</h2> — THE most important detailed section. For EACH calendar event in the next 14 days, create a sub-entry. Under each event, scan through ALL pending items (action items, financial items, deadlines, follow-ups, tasks) and surface anything related to that event. Match items by: (1) same entity/business, (2) due dates falling near the event date, (3) overlapping keywords — client names, venue names, project names, honoree names. Format each event as an <h3> with date and event name, then a <ul> of related pending items. Events with nothing pending: list in a single brief line. This gives a complete "what's coming and what's still unresolved" view.
4. <h2 style="color:#2ecc71">Financial Updates</h2> — INFORMATIONAL ONLY. This is a status section, not a call to action. Render every line in green (style="color:#2ecc71") to signal that it is an update, not a task. Combine money coming IN and money going OUT into a single list, organized by entity. Prefix incoming lines with "+ " and outgoing with "− " so direction is obvious without changing the color. Include amount and counterparty. Do NOT use words like "you owe", "must pay", "collect", "follow up on" — keep the language declarative ("$500 from Bob — Chorus Crafters song delivery", "− $200 to Wells Fargo — mortgage statement").
5. <h2>Task List</h2> — manually-added floating tasks (not email-derived), sorted by priority and due date
6. <h2>Action Items</h2> — email-derived action items not already surfaced in Headlines, Urgent, or Horizon. Sort by priority and age (oldest/most overdue first). Do NOT include follow-up / awaiting-reply threads here — those are intentionally not surfaced as their own section anymore.
7. <h2>Quick Stats</h2> — emails scanned, accounts covered, items tracked

Do NOT include "Active Agreements" or "Follow-ups Needed" sections. Agreements and awaiting-reply threads are inputs you can use to inform Headlines, Urgent, and the Horizon, but they no longer get their own sections.

Always end with a one-sentence focus recommendation for today based on the most pressing item.""".format(
            profile=profile_block,
            priorities=priorities_block,
            entities=self._build_entities_description(),
        )

        user_prompt = f"""Generate today's briefing from this data:

{json.dumps(digest_data, indent=2, default=str)}

Today is {date.today().isoformat()}. Write the full HTML email body."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # response.content is a list of blocks; find the text block
        if response.content:
            return response.content[0].text

        return "<p>No digest generated — API returned empty response.</p>"
