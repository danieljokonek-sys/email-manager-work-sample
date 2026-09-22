"""Read-only views: status, horizon, usage."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

import click

from email_manager import db
from email_manager import horizon as horizon_logic
from email_manager.cli.common import config_and_db, console, friendly_errors, kv_table
from email_manager.db import cleanup_ledger


@click.command()
@friendly_errors
def status() -> None:
    """Counts of everything tracked, plus what is coming up."""
    config = config_and_db()
    owner = config.owner.first_name
    s = db.get_status_summary(owner)
    console.print(
        kv_table(
            "Email Manager status",
            {
                "Emails tracked": s["total_emails"],
                "Unprocessed emails": s["unprocessed_emails"],
                "Calendar events (7 days)": s["upcoming_events_7d"],
                "Active agreements": s["active_agreements"],
                "Pending deadlines": s["pending_deadlines"],
                "Money owed to you": f"${s['money_owed_to_you']:,.2f}",
                "Money you owe": f"${s['money_you_owe']:,.2f}",
                "Pending action items": s["pending_actions"],
                "Stale actions (3+ days)": s["stale_actions"],
                "Tasks": s["pending_tasks"],
                "Awaiting reply": s["follow_ups_waiting"],
                "Emails organized (cleanup)": cleanup_ledger.total(),
            },
        )
    )

    events = db.get_upcoming_events(days_ahead=7)
    if events:
        console.print("\n[bold]Calendar, next 7 days:[/bold]")
        for ev in events:
            when = (ev.get("start_datetime") or ev.get("start_date") or "?")[:16].replace("T", " ")
            console.print(f"  {when}  {ev['title']}  [dim]({ev.get('calendar_name', '')})[/dim]")

    deadlines = db.get_pending_deadlines(days_ahead=7)
    if deadlines:
        console.print("\n[bold]Deadlines, next 7 days:[/bold]")
        for d in deadlines:
            console.print(
                f"  [{d.get('priority', 'medium')}] {d['description']}  due {d.get('due_date', '?')}"
            )

    receivables = db.get_pending_financial(direction="receivable")
    if receivables:
        console.print("\n[bold]Money owed to you:[/bold]")
        for r in receivables:
            amt = f"${r['amount']:,.2f}" if r.get("amount") else "amount TBD"
            console.print(f"  {r.get('counterparty', '?')}: {amt} for {r.get('description', '?')}")

    actions = db.get_pending_actions(owner)
    if actions:
        console.print("\n[bold]Action items:[/bold]")
        for a in actions[:10]:
            console.print(f"  [{a.get('priority', 'medium')}] {a['description']}")

    tasks = db.get_pending_tasks()
    if tasks:
        console.print("\n[bold]Tasks:[/bold]")
        for t in tasks:
            due = f"  due {t['due_date']}" if t.get("due_date") else ""
            console.print(f"  #{t['id']} [{t.get('priority', 'medium')}] {t['title']}{due}")

    follow_ups = db.get_pending_follow_ups()
    if follow_ups:
        console.print("\n[bold]Awaiting reply:[/bold]")
        for f in follow_ups:
            console.print(
                f"  #{f['id']} {f['subject']} -> {f.get('recipient', '?')} [dim]({f['days_waiting']}d)[/dim]"
            )


@click.command()
@click.option("--days", default=14, show_default=True, help="How many days ahead to show.")
@friendly_errors
def horizon(days: int) -> None:
    """Upcoming calendar events with the pending items that relate to each."""
    config = config_and_db()
    data = db.get_horizon_data(days_ahead=days, owner_name=config.owner.first_name)
    if not data["events"]:
        console.print(f"[dim]No calendar events in the next {days} days.[/dim]")
        return

    console.print(f"\n[bold]Next {days} days[/bold]\n")
    today = date.today()
    seen: dict[str, set[int]] = {
        k: set() for k in ("deadlines", "financial", "actions", "tasks", "follow_ups")
    }
    for event in data["events"]:
        ev_date = horizon_logic.event_date(event)
        when = f"{ev_date} ({(ev_date - today).days:+d}d)" if ev_date else "?"
        console.print(
            f"[bold cyan]{when}  {event['title']}[/bold cyan]  [dim]{event.get('entity_key') or ''} ({event.get('calendar_name', '')})[/dim]"
        )
        related = horizon_logic.items_for_event(event, data)
        shown = 0
        for kind, items in related.items():
            for item in items:
                if item["id"] in seen[kind]:
                    continue
                seen[kind].add(item["id"])
                shown += 1
                console.print(f"   {_describe(kind, item)}")
        if not shown:
            console.print("   [dim]nothing pending[/dim]")
        console.print()


def _describe(kind: str, item: Mapping[str, Any]) -> str:
    if kind == "deadlines":
        return f"[yellow]{item['description']}[/yellow]  due {item.get('due_date', '?')}"
    if kind == "financial":
        amt = f"${item['amount']:,.2f}" if item.get("amount") else "amount TBD"
        arrow = "+" if item.get("direction") == "receivable" else "-"
        return f"{arrow} {amt}  {item.get('counterparty', '?')}  [dim]{item.get('description', '')}[/dim]"
    if kind == "tasks":
        due = f"  due {item['due_date']}" if item.get("due_date") else ""
        return f"[bold]{item['title']}[/bold]{due}  [dim](task #{item['id']})[/dim]"
    if kind == "follow_ups":
        return f"No reply from {item.get('recipient', '?')}  [dim]{item.get('subject', '')} ({item['days_waiting']}d)[/dim]"
    due = f"  due {item['due_date']}" if item.get("due_date") else ""
    return f"{item['description']}{due}"


@click.command()
@click.option("--days", default=14, show_default=True, help="How many days with activity to show.")
def usage(days: int) -> None:
    """Claude token usage and cost per day and stage, from the log file."""
    from email_manager.cli.usage_report import print_usage_report

    print_usage_report(console, days=days)
