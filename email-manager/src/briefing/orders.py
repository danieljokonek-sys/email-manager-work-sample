"""
Chorus Crafters order extraction and display logic.

Order status pipeline:
  inquiry → quoted → deposit_received → in_production →
  revision_requested → in_revision → delivered → complete
                                                 cancelled (any stage)
"""
import json
import anthropic
from datetime import date

from src.entities import Entity
from src.llm_usage import log_usage


# Status labels for display
STATUS_LABELS = {
    "inquiry":            "💬 Inquiry",
    "quoted":             "📋 Quoted",
    "deposit_received":   "💰 Deposit Received",
    "in_production":      "🎵 In Production",
    "revision_requested": "✏️  Revision Requested",
    "in_revision":        "🔄 In Revision",
    "delivered":          "📦 Delivered",
    "complete":           "✅ Complete",
    "cancelled":          "❌ Cancelled",
}

STATUS_ORDER = list(STATUS_LABELS.keys())

EVENT_TYPE_LABELS = {
    "wedding":     "💍 Wedding",
    "memorial":    "🕊️  Memorial",
    "birthday":    "🎂 Birthday",
    "anniversary": "💑 Anniversary",
    "other":       "🎉 Other",
}


ORDER_EXTRACTION_SYSTEM = """You are an assistant that extracts Chorus Crafters song order information from emails.

Chorus Crafters is a custom song company. Clients commission personalized songs for weddings, memorials, birthdays, anniversaries, and other significant life events. The owner delivers one finished custom song per order — not individual production/mixing/mastering services.

A typical order lifecycle looks like:
1. Client reaches out with their story and event details (inquiry)
2. Price and terms are communicated (quoted)
3. Client pays a deposit to confirm the order (deposit_received)
4. Song is written and produced (in_production)
5. Client may request revisions (revision_requested → in_revision)
6. Final song is delivered (delivered)
7. Client pays the balance and the order closes (complete)

YOUR JOB: Extract any song order details visible in the email. If an email clearly relates to a Chorus Crafters order, extract what you can. Leave fields null if not mentioned.

Today is {today}.

Return a JSON object (or null if the email has no order-relevant content):
{{
  "client_name": "<full name of person ordering>",
  "client_email": "<their email if visible>",
  "client_phone": "<their phone if mentioned>",
  "event_type": "<wedding|memorial|birthday|anniversary|other>",
  "event_date": "<YYYY-MM-DD or null>",
  "honoree_names": "<e.g. 'John & Jane', 'In memory of Bob'>",
  "event_notes": "<venue, context, any other event detail>",
  "song_style": "<genre, mood, instrumentation, vibe>",
  "song_story": "<the narrative/details they want in the song>",
  "reference_songs": ["<artist - title>"],
  "revisions_included": <number or null>,
  "price": <number or null>,
  "deposit_amount": <number or null>,
  "balance_due": <number or null>,
  "status": "<best-fit status from the pipeline or null>",
  "notes": "<anything else worth tracking>"
}}

Status guidance:
- "inquiry" if they're first reaching out or asking questions
- "quoted" if a price has been sent but no deposit yet
- "deposit_received" if they've confirmed payment of a deposit
- "in_production" if work is underway
- "revision_requested" if they've asked for changes on a delivered demo
- "in_revision" if revisions are being made
- "delivered" if the final song has been sent
- "complete" if final payment confirmed
- "cancelled" if they've withdrawn

Respond with JSON only. Return null if this email is not order-related."""


class OrderExtractor:
    def __init__(self, model: str = "claude-sonnet-5", effort: str = "low"):
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def extract_from_email(self, email: dict) -> dict | None:
        """Extract song order fields from a single email. Returns dict or None."""
        text = f"Subject: {email.get('subject', '')}\n" \
               f"From: {email.get('sender', '')}\n" \
               f"Date: {email.get('date', '')}\n\n" \
               f"{email.get('body_snippet', '')}"

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            output_config={"effort": self.effort},
            system=ORDER_EXTRACTION_SYSTEM.format(today=date.today().isoformat()),
            messages=[{"role": "user", "content": f"Extract order info from this email:\n\n{text}"}],
        )
        log_usage("order-extract", response)

        for block in response.content:
            if block.type == "text":
                return self._parse(block.text, email["id"])
        return None

    def _parse(self, text: str, email_id: str) -> dict | None:
        text = text.strip()
        if text.lower() in ("null", "none", ""):
            return None
        if text.startswith("```"):
            lines = text.split("\n")[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            data = json.loads(text)
            if data is None:
                return None
            data["email_id"] = email_id
            return data
        except json.JSONDecodeError:
            return None


def format_order_row(order: dict) -> tuple:
    """Return a tuple of display strings for Rich table row."""
    status = STATUS_LABELS.get(order.get("status", ""), order.get("status", ""))
    event_type = EVENT_TYPE_LABELS.get(order.get("event_type", ""), order.get("event_type", "") or "—")
    price = f"${order['price']:,.0f}" if order.get("price") else "—"
    deposit = "✓" if order.get("deposit_paid") else "✗"
    revs = f"{order.get('revisions_used', 0)}/{order.get('revisions_included', 2)}"
    event_date = order.get("event_date") or "—"
    return (
        str(order["id"]),
        order.get("client_name") or "—",
        event_type,
        event_date,
        status,
        price,
        deposit,
        revs,
    )


def format_order_detail(order: dict) -> str:
    """Render a full order detail as a text block for terminal display."""
    lines = []
    lines.append(f"Order #{order['id']}  {STATUS_LABELS.get(order.get('status',''), order.get('status',''))}")
    lines.append("─" * 60)

    def row(label, value):
        if value not in (None, "", "null", []):
            lines.append(f"  {label:<22} {value}")

    row("Client:", order.get("client_name"))
    row("Email:", order.get("client_email"))
    row("Phone:", order.get("client_phone"))
    lines.append("")
    row("Event Type:", EVENT_TYPE_LABELS.get(order.get("event_type",""), order.get("event_type","")))
    row("Event Date:", order.get("event_date"))
    row("Honorees:", order.get("honoree_names"))
    row("Event Notes:", order.get("event_notes"))
    lines.append("")
    row("Song Style:", order.get("song_style"))
    if order.get("song_story"):
        lines.append(f"  {'Story/Brief:':<22}")
        for line in (order["song_story"] or "").split("\n"):
            lines.append(f"    {line}")
    refs = order.get("reference_songs")
    if refs:
        try:
            refs = json.loads(refs) if isinstance(refs, str) else refs
        except Exception:
            refs = []
        if refs:
            lines.append(f"  {'Reference Songs:':<22} {refs[0]}")
            for r in refs[1:]:
                lines.append(f"  {'':<22} {r}")
    lines.append("")
    if order.get("price"):
        lines.append(f"  {'Price:':<22} ${order['price']:,.2f}")
    if order.get("deposit_amount"):
        paid = "✓ paid" if order.get("deposit_paid") else "✗ unpaid"
        lines.append(f"  {'Deposit:':<22} ${order['deposit_amount']:,.2f}  [{paid}]")
        if order.get("deposit_date"):
            lines.append(f"  {'Deposit Date:':<22} {order['deposit_date']}")
    if order.get("balance_due"):
        paid = "✓ paid" if order.get("balance_paid") else "✗ unpaid"
        lines.append(f"  {'Balance Due:':<22} ${order['balance_due']:,.2f}  [{paid}]")
        if order.get("balance_date"):
            lines.append(f"  {'Balance Paid:':<22} {order['balance_date']}")
    lines.append("")
    demo = "✓ sent" if order.get("demo_delivered") else "pending"
    final = "✓ delivered" if order.get("final_delivered") else "pending"
    revs = f"{order.get('revisions_used', 0)} of {order.get('revisions_included', 2)} used"
    lines.append(f"  {'Demo:':<22} {demo}")
    lines.append(f"  {'Final Track:':<22} {final}")
    lines.append(f"  {'Revisions:':<22} {revs}")
    if order.get("notes"):
        lines.append("")
        lines.append(f"  {'Notes:':<22} {order['notes']}")
    lines.append(f"\n  {'Created:':<22} {order.get('created_at','')[:10]}")
    lines.append(f"  {'Updated:':<22} {order.get('updated_at','')[:10]}")
    return "\n".join(lines)
