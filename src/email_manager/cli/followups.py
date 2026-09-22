"""Threads you sent last and have not heard back on."""

from __future__ import annotations

import click
from rich.table import Table

from email_manager import db, pipeline
from email_manager.cli.common import config_and_db, console, friendly_errors
from email_manager.client_factory import build_clients


@click.group()
def followups() -> None:
    """View and manage emails awaiting a reply."""


@followups.command(name="list")
@friendly_errors
def followups_list() -> None:
    """Show threads where you sent the last message."""
    db.init_db()
    items = db.get_pending_follow_ups()
    if not items:
        console.print("No follow-ups waiting.")
        return
    table = Table(title="Awaiting reply")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Subject")
    table.add_column("Sent to")
    table.add_column("Account")
    table.add_column("Last sent")
    table.add_column("Waiting", justify="right")
    for f in items:
        days = int(f.get("days_waiting", 0))
        style = "red" if days >= 7 else "yellow" if days >= 3 else ""
        table.add_row(
            str(f["id"]),
            (f.get("subject") or "")[:45],
            (f.get("recipient") or "")[:30],
            f.get("account_email", ""),
            f.get("last_sent_date", ""),
            f"[{style}]{days}d[/{style}]" if style else f"{days}d",
        )
    console.print(table)


@followups.command(name="dismiss")
@click.argument("followup_id", type=int)
@friendly_errors
def followups_dismiss(followup_id: int) -> None:
    """Dismiss a follow-up you no longer need."""
    db.init_db()
    if db.dismiss_follow_up(followup_id):
        console.print(f"[green]Follow-up #{followup_id} dismissed.[/green]")
    else:
        raise click.ClickException(f"No follow-up #{followup_id}")


@followups.command(name="scan")
@friendly_errors
def followups_scan() -> None:
    """Scan sent folders now."""
    config = config_and_db()
    auth = pipeline.authenticate_all(build_clients(config))
    found = pipeline.detect_follow_ups(config, auth.healthy)
    console.print(f"[green]{found} thread(s) awaiting reply[/green]")
