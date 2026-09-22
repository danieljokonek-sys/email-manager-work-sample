<!--
Extraction: user prompt. Placeholders: {entity_keys}, {emails_json}.
-->
Analyze these emails and extract actionable items. For each email, determine which business entity (if any) it relates to, and extract agreements, deadlines, financial items, and action items.

Entity keys to use: {entity_keys}
If an item does not clearly relate to any entity, use "personal" as the entity_key.

EMAILS TO ANALYZE:
{emails_json}
