"""
Email Organizer — Claude-powered inbox categorization, labeling, and cleanup.

Label categories are loaded from config.yaml at runtime. If no labels are
defined in the config, a minimal set of sensible defaults is used.
"""
import json
import logging
import anthropic

from src.llm_usage import log_usage
from datetime import datetime, timedelta, timezone
from datetime import date

log = logging.getLogger("organizer")

# ── Color palette for auto-assigning Gmail label colors ──────────────────
_COLOR_PALETTE = [
    "#fb4c2f", "#16a765", "#4a86e8", "#ffad47", "#a479e2",
    "#b65775", "#149e60", "#6d9eeb", "#cc3a21", "#b694e8",
    "#e07798", "#3c78d8", "#eba093", "#a46a21", "#fbc8d9",
    "#8e63ce", "#285bac", "#c9daf8", "#ac2b16", "#b9e4d0",
    "#ffd6a2", "#3dc789", "#a4c2f4", "#f691b3", "#98d7e4",
]

# ── Fallback defaults (used when config has no labels section) ───────────
_DEFAULT_LABELS = [
    {"name": "Personal", "group": "Personal", "description": "Friends, family, personal correspondence", "protection": "standard"},
    {"name": "Tax", "group": "Finance", "description": "Tax documents, W-2s, 1099s, CPA correspondence", "protection": "vital"},
    {"name": "Fees and Bills", "group": "Finance", "description": "Subscription charges, utility bills, payment confirmations", "protection": "standard"},
    {"name": "Banking and Investments", "group": "Finance", "description": "Bank statements, investment accounts, credit cards, loans", "protection": "vital"},
    {"name": "Legal", "group": "Legal & Government", "description": "Contracts, legal notices, attorney correspondence", "protection": "vital"},
    {"name": "Government", "group": "Legal & Government", "description": "DMV, agencies, voter registration, government correspondence", "protection": "standard"},
    {"name": "Product Purchases", "group": "Shopping", "description": "Order confirmations, receipts, warranty info", "protection": "standard"},
    {"name": "Licenses and Keys", "group": "Shopping", "description": "Software license keys, activation codes, serial numbers", "protection": "vital"},
    {"name": "Account Security", "group": "Security", "description": "Password resets, 2FA codes, security alerts", "protection": "vital"},
    {"name": "Logins and Verification", "group": "Security", "description": "Email verification, new device sign-ins, login confirmations", "protection": "standard"},
    {"name": "Health and Insurance", "group": "Health & Travel", "description": "Medical, dental, prescriptions, insurance claims", "protection": "vital"},
    {"name": "Travel", "group": "Health & Travel", "description": "Flight confirmations, hotel reservations, trip itineraries", "protection": "standard"},
    {"name": "Employment", "group": "Personal", "description": "Job-related, HR, payroll, benefits", "protection": "vital"},
    {"name": "Junk", "group": "", "description": "Marketing, spam, newsletters, unsolicited bulk email", "protection": "none", "auto_delete": True},
]


def build_label_config(config: dict) -> dict:
    """Build all label data structures from config.yaml.

    Returns a dict with keys:
        keep_categories, junk_categories, vital_categories, strict_vital_categories,
        all_categories, label_path_map, label_colors, label_descriptions
    """
    labels = config.get("labels") or []
    if not labels:
        labels = list(_DEFAULT_LABELS)

    keep = []
    junk = []
    vital = set()
    strict_vital = set()
    path_map = {}
    colors = {}
    descriptions = {}
    color_idx = 0

    for lbl in labels:
        name = lbl["name"]
        group = lbl.get("group", "")
        desc = lbl.get("description", "")
        protection = lbl.get("protection", "standard")
        auto_delete = lbl.get("auto_delete", False)

        descriptions[name] = desc

        if auto_delete:
            junk.append(name)
        else:
            keep.append(name)

        if protection == "vital":
            vital.add(name)
            strict_vital.add(name)
        elif protection == "standard":
            # standard categories are not in vital — they can be aged out
            pass

        # Build Gmail label path: "Group/Name" or just "Name" if no group
        label_path = f"{group}/{name}" if group else name
        path_map[name] = label_path

        # Auto-assign color from palette
        bg = _COLOR_PALETTE[color_idx % len(_COLOR_PALETTE)]
        color_idx += 1
        colors[label_path] = {"textColor": "#ffffff", "backgroundColor": bg}

    return {
        "keep_categories": keep,
        "junk_categories": junk,
        "vital_categories": vital,
        "strict_vital_categories": strict_vital,
        "all_categories": keep + junk,
        "label_path_map": path_map,
        "label_colors": colors,
        "label_descriptions": descriptions,
    }

def _build_classification_system_prompt(owner_name: str, account_emails: list[str],
                                       label_cfg: dict) -> str:
    """Build the classification system prompt dynamically from label config."""
    keep_cats = label_cfg["keep_categories"]
    junk_cats = label_cfg["junk_categories"]
    descriptions = label_cfg["label_descriptions"]

    # Group keep categories by their label group
    path_map = label_cfg["label_path_map"]
    groups: dict[str, list[str]] = {}
    for cat in keep_cats:
        path = path_map.get(cat, cat)
        group = path.split("/")[0] if "/" in path else "Other"
        groups.setdefault(group, []).append(cat)

    # Build category listing
    cat_lines = []
    for group, cats in groups.items():
        cat_lines.append(f"\n## {group} categories:")
        for cat in cats:
            desc = descriptions.get(cat, "")
            cat_lines.append(f"- {cat}: {desc}" if desc else f"- {cat}")

    if junk_cats:
        cat_lines.append("\n## JUNK category (auto-deleted, no label):")
        for cat in junk_cats:
            desc = descriptions.get(cat, "Marketing, spam, newsletters, unsolicited bulk email")
            cat_lines.append(f"- {cat}: ALL of the following get auto-deleted \u2014 {desc}")

    categories_text = "\n".join(cat_lines)

    # Determine the default/fallback category
    fallback = "Personal" if "Personal" in keep_cats else keep_cats[0] if keep_cats else "Personal"

    return f"""You are an email triage assistant. Your job is to classify emails into categories so the user's inbox stays clean and organized.

OWNER: {owner_name}
OWNER'S EMAIL ACCOUNTS: {', '.join(account_emails)}
{categories_text}

## IMPORTANT: You MUST ONLY use the exact category names listed above. NEVER invent new categories. If an email doesn't fit perfectly, force it into the closest matching category. When truly unsure, use "{fallback}" as the fallback. There is NO "Important" or "Other" catch-all.

## CRITICAL RULES:
1. ENTITY/BUSINESS FIRST: If an email clearly relates to one of the entity categories, use that category
2. Order confirmations/receipts belong in a shopping/purchase category (NOT Junk)
3. Password resets, 2FA codes belong in a security category (NEVER Junk)
4. Software license keys, activation codes belong in a license/key category (NEVER Junk)
5. Flight/hotel/car rental confirmations belong in a travel category (NEVER Junk)
6. Bank/investment statements belong in a banking/finance category
7. Tax-related from any source -> use the tax category (overrides other finance categories)
8. Subscription CHARGE/receipt -> fees/bills category; subscription PROMO -> Junk
9. Shipping tracking for a real order -> shipping category (NOT Junk)
10. Emails FROM the owner TO the owner -> classify by CONTENT
11. When in doubt between keep and junk, ALWAYS lean toward KEEP
12. NEVER output a category name that is not in the provided list. Force every email into the best existing category. Use "{fallback}" if nothing else fits.

Today's date: {date.today().isoformat()}

Respond with valid JSON only."""


CLASSIFICATION_USER_PROMPT = """Classify each email below. For each, provide:
- category: one of {categories}
- action: "label" (keep in inbox with category label) or "archive" (label + remove from inbox). Emails classified as "Junk" are auto-deleted regardless of action.
- confidence: "high", "medium", or "low"

Guidelines for action:
- "label": Emails the user should see or may need to act on
- "archive": Emails worth keeping for records but not needing inbox attention
- Any email categorized as "Junk" will be auto-deleted — no label, straight to trash

EMAILS:
{emails_json}

Respond with a JSON object mapping email IDs to classifications:
{{
  "<email_id>": {{
    "category": "<category>",
    "action": "<label|archive|trash>",
    "confidence": "<high|medium|low>",
    "reason": "<1 sentence why>"
  }}
}}"""


class EmailOrganizer:
    def __init__(self, owner_name: str, account_emails: list[str],
                 model: str = "claude-sonnet-5", batch_size: int = 25,
                 label_cfg: dict | None = None, effort: str = "low"):
        self.client = anthropic.Anthropic()
        self.owner_name = owner_name
        self.account_emails = account_emails
        self.model = model
        self.effort = effort
        self.batch_size = batch_size
        # If no label_cfg provided, build from defaults
        self.label_cfg = label_cfg or build_label_config({})

    def classify_batch(self, emails: list[dict]) -> dict:
        if not emails:
            return {}
        emails_for_prompt = [{"id": e["id"], "sender": e.get("sender", ""), "recipients": e.get("recipients", []), "subject": e.get("subject", ""), "date": e.get("date", ""), "body_snippet": e.get("body_snippet", "")[:1200], "labels": e.get("labels", [])} for e in emails]
        system_prompt = _build_classification_system_prompt(self.owner_name, self.account_emails, self.label_cfg)
        user_prompt = CLASSIFICATION_USER_PROMPT.format(categories=", ".join(self.label_cfg["all_categories"]), emails_json=json.dumps(emails_for_prompt, indent=2))
        response = self.client.messages.create(
            model=self.model, max_tokens=8000,
            output_config={"effort": self.effort},
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        log_usage("cleanup-classify", response)
        for block in response.content:
            if block.type == "text":
                return self._parse_json(block.text)
        return {}

    def classify_and_act(self, emails: list[dict], gmail_client, dry_run: bool = False, delete_older_than_days: int | None = None, strict: bool = False) -> dict:
        stats = {"labeled": 0, "archived": 0, "trashed": 0, "skipped": 0, "errors": 0}
        classifications = self.classify_batch(emails)
        email_map = {e["id"]: e for e in emails}
        cutoff = None
        if delete_older_than_days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=delete_older_than_days)
        junk_cats = self.label_cfg["junk_categories"]
        vital_cats = self.label_cfg["vital_categories"]
        strict_vital_cats = self.label_cfg["strict_vital_categories"]
        for email_id, result in classifications.items():
            category = result.get("category", "Personal")
            action = result.get("action", "label")
            confidence = result.get("confidence", "medium")
            is_junk = category in junk_cats
            is_old_and_disposable = False
            if cutoff and not is_junk:
                email_date_str = email_map.get(email_id, {}).get("date", "")
                try:
                    email_date = datetime.fromisoformat(email_date_str)
                    if email_date.tzinfo is None:
                        email_date = email_date.replace(tzinfo=timezone.utc)
                    protected = strict_vital_cats if strict else vital_cats
                    if email_date < cutoff and category not in protected:
                        is_old_and_disposable = True
                except (ValueError, TypeError):
                    pass
            if (is_junk or is_old_and_disposable) and confidence == "low":
                is_old_and_disposable = False
                if is_junk:
                    action = "archive"
            if dry_run:
                subj = email_map.get(email_id, {}).get("subject", "?")
                if is_junk:
                    fate = "DELETE (junk)"
                elif is_old_and_disposable:
                    fate = "DELETE (old, non-vital)"
                else:
                    fate = action
                log.info(f"[DRY RUN] {email_id}: {category} -> {fate} ({confidence}) -- {subj}")
                stats["trashed" if (is_junk or is_old_and_disposable) else "labeled"] += 1
                continue
            try:
                if is_junk or is_old_and_disposable:
                    gmail_client.trash_email(email_id)
                    stats["trashed"] += 1
                else:
                    label_name = self._label_name_for_category(category)
                    color = self.label_cfg["label_colors"].get(label_name)
                    label_id = gmail_client.get_or_create_label(label_name, color=color)
                    if action == "archive":
                        gmail_client.apply_labels_and_actions(email_id, add_label_ids=[label_id], remove_label_ids=["INBOX"])
                        stats["archived"] += 1
                    else:
                        gmail_client.apply_label(email_id, label_id)
                        stats["labeled"] += 1
            except Exception as e:
                log.warning(f"Failed to process {email_id}: {e}")
                stats["errors"] += 1
        stats["skipped"] = len(emails) - sum(stats[k] for k in ("labeled", "archived", "trashed", "errors"))
        return stats, classifications

    def _label_name_for_category(self, category: str) -> str:
        return self.label_cfg["label_path_map"].get(category, f"Personal/{category}")

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
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
