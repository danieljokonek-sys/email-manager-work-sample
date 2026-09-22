"""Manual tasks that show on the board until their date passes."""

from __future__ import annotations

import click
from rich.table import Table

from email_manager import db
from email_manager.cli.common import console, friendly_errors


@click.group()
def tasks() -> None:
    """Add, list, complete, or delete manual tasks."""


@tasks.command(name="add")
@click.argument("title")
@click.option("--notes", default=None)
@click.option("--entity", "entity_key", default="personal", show_default=True)
@click.option("--due", "due_date", default=None, help="Due date YYYY-MM-DD.")
@click.option(
    "--priority", default="medium", type=click.Choice(["high", "medium", "low"]), show_default=True
)
@friendly_errors
def tasks_add(
    title: str, notes: str | None, entity_key: str, due_date: str | None, priority: str
) -> None:
    """Add a task. Example: tasks add "File Q1 taxes" --due 2026-04-15 --priority high"""
    db.init_db()
    task_id = db.add_task(
        title, notes=notes, entity_key=entity_key, due_date=due_date, priority=priority
    )
    console.print(f"[green]Task #{task_id} added: {title}[/green]")


@tasks.command(name="list")
@friendly_errors
def tasks_list() -> None:
    """List pending tasks."""
    db.init_db()
    items = db.get_pending_tasks()
    if not items:
        console.print("No pending tasks.")
        return
    table = Table(title="Tasks")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Priority")
    table.add_column("Title")
    table.add_column("Entity")
    table.add_column("Due")
    table.add_column("Notes")
    styles = {"high": "red", "medium": "yellow", "low": "green"}
    for t in items:
        style = styles.get(t["priority"], "")
        table.add_row(
            str(t["id"]),
            f"[{style}]{t['priority']}[/{style}]" if style else t["priority"],
            t["title"],
            t.get("entity_key") or "personal",
            t.get("due_date") or "",
            (t.get("notes") or "")[:50],
        )
    console.print(table)


@tasks.command(name="done")
@click.argument("task_id", type=int)
@friendly_errors
def tasks_done(task_id: int) -> None:
    """Mark a task complete."""
    db.init_db()
    if db.complete_task(task_id):
        console.print(f"[green]Task #{task_id} done.[/green]")
    else:
        raise click.ClickException(f"No pending task #{task_id}")


@tasks.command(name="delete")
@click.argument("task_id", type=int)
@friendly_errors
def tasks_delete(task_id: int) -> None:
    """Delete a task."""
    db.init_db()
    if db.delete_task(task_id):
        console.print(f"[green]Task #{task_id} deleted.[/green]")
    else:
        raise click.ClickException(f"No task #{task_id}")
