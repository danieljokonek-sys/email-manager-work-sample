"""
Local web dashboard for managing briefing items.

Provides a browser-based UI with checkboxes to mark action items,
deadlines, financials, tasks, follow-ups, and agreements as done —
replacing the cumbersome email-reply workflow.

Usage:  python main.py dashboard [--port 5050]
"""
import json
from flask import Flask, render_template_string, request, redirect, url_for, flash

from src.briefing import database as db

# Status each category uses when marked "done"
DONE_STATUS = {
    "action_items": "done",
    "deadlines": "done",
    "financial_items": "resolved",
    "tasks": "done",
    "follow_ups": "dismissed",
    "agreements": "fulfilled",
}

TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Briefing Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    color: #333; max-width: 740px; margin: 0 auto; padding: 20px 16px 100px;
    background: #fafafa;
  }
  h1 { color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 10px; margin-bottom: 6px; font-size: 1.6em; }
  .subtitle { color: #6c757d; font-size: 0.9em; margin-bottom: 24px; }
  h2 { color: #1a1a2e; margin: 28px 0 12px; font-size: 1.15em; display: flex; align-items: center; gap: 8px; }
  .count { background: #e9ecef; color: #495057; border-radius: 12px; padding: 2px 10px; font-size: 0.8em; font-weight: normal; }

  .flash { background: #d4edda; border: 1px solid #c3e6cb; color: #155724; padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; }

  .item {
    background: #fff; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;
    border-left: 4px solid #dee2e6; display: flex; align-items: flex-start; gap: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }
  .item:hover { box-shadow: 0 2px 6px rgba(0,0,0,0.08); }
  .item input[type="checkbox"] { margin-top: 3px; width: 18px; height: 18px; cursor: pointer; flex-shrink: 0; }
  .item-body { flex: 1; min-width: 0; }
  .item-desc { font-size: 0.95em; line-height: 1.4; }
  .item-meta { font-size: 0.8em; color: #6c757d; margin-top: 4px; display: flex; flex-wrap: wrap; gap: 8px; }
  .item-meta span { white-space: nowrap; }

  .cat-action .item { border-left-color: #e94560; }
  .cat-deadline .item { border-left-color: #17a2b8; }
  .cat-financial .item { border-left-color: #28a745; }
  .cat-task .item { border-left-color: #6f42c1; }
  .cat-followup .item { border-left-color: #fd7e14; }
  .cat-agreement .item { border-left-color: #20c997; }
  .cat-calendar .item { border-left-color: #6c757d; background: #f8f9fa; }

  .priority-high { color: #dc3545; font-weight: 600; }
  .priority-medium { color: #fd7e14; }
  .entity-tag { background: #e9ecef; border-radius: 4px; padding: 1px 6px; font-size: 0.8em; color: #495057; }
  .amount { font-weight: 600; color: #28a745; }
  .amount.payable { color: #dc3545; }

  .submit-bar {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #fff; border-top: 2px solid #e94560; padding: 12px 20px;
    display: flex; justify-content: center; gap: 16px; align-items: center;
    box-shadow: 0 -2px 8px rgba(0,0,0,0.08); z-index: 100;
  }
  .submit-bar button {
    background: #e94560; color: #fff; border: none; border-radius: 8px;
    padding: 10px 28px; font-size: 1em; font-weight: 600; cursor: pointer;
  }
  .submit-bar button:hover { background: #d63851; }
  .selected-count { color: #6c757d; font-size: 0.9em; }

  .empty { color: #6c757d; font-style: italic; padding: 12px 0; }

  .cal-time { font-weight: 600; color: #1a1a2e; min-width: 58px; }
</style>
</head>
<body>
<h1>Briefing Dashboard</h1>
<p class="subtitle">Check items off as you complete them, then hit the button at the bottom.</p>

{% with messages = get_flashed_messages() %}
{% if messages %}
  {% for msg in messages %}
  <div class="flash">{{ msg }}</div>
  {% endfor %}
{% endif %}
{% endwith %}

<form method="POST" action="{{ url_for('mark_done') }}" id="dashboard-form">

<!-- Action Items -->
{% if actions %}
<div class="cat-action">
  <h2>Action Items <span class="count">{{ actions|length }}</span></h2>
  {% for item in actions %}
  <div class="item">
    <input type="checkbox" name="items" value="action_items:{{ item.id }}:done">
    <div class="item-body">
      <div class="item-desc">{{ item.description }}</div>
      <div class="item-meta">
        {% if item.priority and item.priority != 'normal' %}
          <span class="priority-{{ item.priority }}">{{ item.priority }}</span>
        {% endif %}
        {% if item.due_date %}<span>Due: {{ item.due_date }}</span>{% endif %}
        {% if item.entity_key %}<span class="entity-tag">{{ item.entity_key }}</span>{% endif %}
        {% if item.email_subject %}<span>Re: {{ item.email_subject[:60] }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<!-- Deadlines -->
{% if deadlines %}
<div class="cat-deadline">
  <h2>Deadlines <span class="count">{{ deadlines|length }}</span></h2>
  {% for item in deadlines %}
  <div class="item">
    <input type="checkbox" name="items" value="deadlines:{{ item.id }}:done">
    <div class="item-body">
      <div class="item-desc">{{ item.description }}</div>
      <div class="item-meta">
        {% if item.priority and item.priority != 'normal' %}
          <span class="priority-{{ item.priority }}">{{ item.priority }}</span>
        {% endif %}
        {% if item.due_date %}<span>Due: {{ item.due_date }}</span>{% endif %}
        {% if item.entity_key %}<span class="entity-tag">{{ item.entity_key }}</span>{% endif %}
        {% if item.email_subject %}<span>Re: {{ item.email_subject[:60] }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<!-- Money Owed To You -->
{% if receivables %}
<div class="cat-financial">
  <h2>Money Owed To You <span class="count">{{ receivables|length }}</span></h2>
  {% for item in receivables %}
  <div class="item">
    <div class="item-body">
      <div class="item-desc">
        <span class="amount">{{ item.currency or '$' }}{{ "%.2f"|format(item.amount or 0) }}</span>
        {% if item.counterparty %} from {{ item.counterparty }}{% endif %}
        {% if item.description %} &mdash; {{ item.description }}{% endif %}
      </div>
      <div class="item-meta">
        {% if item.due_date %}<span>Due: {{ item.due_date }}</span>{% endif %}
        {% if item.entity_key %}<span class="entity-tag">{{ item.entity_key }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<!-- Money You Owe -->
{% if payables %}
<div class="cat-financial">
  <h2>Money You Owe <span class="count">{{ payables|length }}</span></h2>
  {% for item in payables %}
  <div class="item">
    <div class="item-body">
      <div class="item-desc">
        <span class="amount payable">{{ item.currency or '$' }}{{ "%.2f"|format(item.amount or 0) }}</span>
        {% if item.counterparty %} to {{ item.counterparty }}{% endif %}
        {% if item.description %} &mdash; {{ item.description }}{% endif %}
      </div>
      <div class="item-meta">
        {% if item.due_date %}<span>Due: {{ item.due_date }}</span>{% endif %}
        {% if item.entity_key %}<span class="entity-tag">{{ item.entity_key }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<!-- Tasks -->
{% if tasks %}
<div class="cat-task">
  <h2>Tasks <span class="count">{{ tasks|length }}</span></h2>
  {% for item in tasks %}
  <div class="item">
    <input type="checkbox" name="items" value="tasks:{{ item.id }}:done">
    <div class="item-body">
      <div class="item-desc">{{ item.title }}</div>
      <div class="item-meta">
        {% if item.priority and item.priority != 'normal' %}
          <span class="priority-{{ item.priority }}">{{ item.priority }}</span>
        {% endif %}
        {% if item.due_date %}<span>Due: {{ item.due_date }}</span>{% endif %}
        {% if item.notes %}<span>{{ item.notes[:80] }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<!-- Follow-Ups -->
{% if follow_ups %}
<div class="cat-followup">
  <h2>Follow-Ups Needed <span class="count">{{ follow_ups|length }}</span></h2>
  {% for item in follow_ups %}
  <div class="item">
    <input type="checkbox" name="items" value="follow_ups:{{ item.id }}:dismissed">
    <div class="item-body">
      <div class="item-desc">{{ item.subject }}</div>
      <div class="item-meta">
        {% if item.recipient %}<span>To: {{ item.recipient }}</span>{% endif %}
        {% if item.days_waiting %}<span>Waiting {{ item.days_waiting }}d</span>{% endif %}
        {% if item.entity_key %}<span class="entity-tag">{{ item.entity_key }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<!-- Agreements -->
{% if agreements %}
<div class="cat-agreement">
  <h2>Active Agreements <span class="count">{{ agreements|length }}</span></h2>
  {% for item in agreements %}
  <div class="item">
    <div class="item-body">
      <div class="item-desc">{{ item.summary }}</div>
      <div class="item-meta">
        {% if item.entity_key %}<span class="entity-tag">{{ item.entity_key }}</span>{% endif %}
        {% if item.terms %}<span>{{ item.terms[:80] }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

<!-- Calendar Events (read-only) -->
{% if events %}
<div class="cat-calendar">
  <h2>Upcoming Calendar Events <span class="count">{{ events|length }}</span></h2>
  {% for item in events %}
  <div class="item">
    <div class="item-body">
      <div class="item-desc">
        {% if item.start_datetime %}
          <span class="cal-time">{{ item.start_datetime[11:16] }}</span>
        {% elif item.all_day %}
          <span class="cal-time">All day</span>
        {% endif %}
        {{ item.title }}
      </div>
      <div class="item-meta">
        {% if item.start_date %}<span>{{ item.start_date }}</span>{% endif %}
        {% if item.start_datetime %}<span>{{ item.start_datetime[:10] }}</span>{% endif %}
        {% if item.location %}<span>{{ item.location[:60] }}</span>{% endif %}
        {% if item.calendar_name %}<span class="entity-tag">{{ item.calendar_name }}</span>{% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>
{% endif %}

{% if not actions and not deadlines and not receivables and not payables and not tasks and not follow_ups and not agreements and not events %}
  <p class="empty" style="margin-top: 40px; text-align: center; font-size: 1.1em;">All clear — nothing pending!</p>
{% endif %}

</form>

<div class="submit-bar">
  <span class="selected-count" id="sel-count">0 selected</span>
  <button type="submit" form="dashboard-form">Mark Selected as Done</button>
</div>

<script>
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('dashboard-form');
  const counter = document.getElementById('sel-count');
  function update() {
    const n = form.querySelectorAll('input[type="checkbox"]:checked').length;
    counter.textContent = n + ' selected';
  }
  form.addEventListener('change', update);
});
</script>
</body>
</html>
"""


def create_app():
    app = Flask(__name__)
    app.secret_key = "briefing-dashboard-local"

    @app.before_request
    def _ensure_db():
        # Reset the singleton so this thread gets its own connection
        if db._conn is not None:
            try:
                db._conn.close()
            except Exception:
                pass
            db._conn = None
        db.init_db()

    @app.route("/")
    def index():
        return render_template_string(
            TEMPLATE,
            actions=db.get_pending_actions(owner_name="Daniel"),
            deadlines=db.get_pending_deadlines(),
            receivables=db.get_pending_financial(direction="receivable"),
            payables=db.get_pending_financial(direction="payable"),
            tasks=db.get_pending_tasks(),
            follow_ups=db.get_pending_follow_ups(),
            agreements=db.get_active_agreements(),
            events=db.get_upcoming_events(days_ahead=14),
        )

    @app.route("/mark-done", methods=["POST"])
    def mark_done():
        items = request.form.getlist("items")
        marked = 0
        for item in items:
            parts = item.split(":")
            if len(parts) != 3:
                continue
            table, item_id, status = parts
            if table not in DONE_STATUS:
                continue
            try:
                db.update_item_status(table, int(item_id), status)
                marked += 1
            except (ValueError, Exception):
                continue
        if marked:
            flash(f"Marked {marked} item{'s' if marked != 1 else ''} as done.")
        return redirect(url_for("index"))

    return app
