<!--
Duplicate reconciliation: user prompt. Placeholders: {data_json}.
-->
Here are the pending items, grouped by type. Each item has a stable numeric "id".

DATA:
{data_json}

Find clusters of duplicates. For each cluster give the type, the ids, and the synthesized canonical fields:
- financial_items: counterparty, amount, currency, description, due_date
- action_items: description, assigned_to, due_date, priority
- deadlines: description, due_date, priority
