"""Inbox cleanup: classify, label, archive, trash (under the safety guards)."""

from __future__ import annotations

from datetime import datetime

import click
from rich.table import Table

from email_manager import pipeline
from email_manager.cleanup.organizer import CleanupStats, build_taxonomy
from email_manager.cli.common import (
    claude_for,
    config_and_db,
    console,
    friendly_errors,
    print_usage_summary,
)
from email_manager.client_factory import build_clients, find_client
from email_manager.config import Config
from email_manager.db import cleanup_ledger
from email_manager.email_client import EmailClient, EmailFilter


@click.group()
def cleanup() -> None:
    """Classify, label, archive, and trash email."""


def _print_stats(stats: CleanupStats, dry_run: bool) -> None:
    if dry_run:
        for line in stats.decisions:
            console.print(f"  {line}")
    table = Table(title=("[DRY RUN] " if dry_run else "") + "Cleanup results")
    table.add_column("Action", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.as_dict().items():
        table.add_row(k, str(v))
    console.print(table)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise click.BadParameter(f"{value!r} is not YYYY-MM-DD")


@cleanup.command()
@click.option("--dry-run", is_flag=True, help="Show decisions without changing anything.")
@click.option("--limit", default=100, show_default=True, help="Max emails to process.")
@friendly_errors
def inbox(dry_run: bool, limit: int) -> None:
    """Organize the current inbox."""
    config = config_and_db()
    stats = pipeline.cleanup_inbox(
        config, claude_for(config.cleanup.model), limit=limit, dry_run=dry_run
    )
    _print_stats(stats, dry_run)
    print_usage_summary()


@cleanup.command()
@click.option("--dry-run", is_flag=True, help="Show decisions without changing anything.")
@click.option("--page-size", default=50, show_default=True)
@click.option("--max-pages", default=20, show_default=True, help="0 = unlimited.")
@click.option("--all-mail", is_flag=True, help="All mail, not just the inbox (Gmail/Outlook).")
@click.option("--model", default=None, help="Override the cleanup model.")
@click.option(
    "--delete-older-than-days",
    default=None,
    type=int,
    help="Trash non-vital mail older than N days.",
)
@click.option("--after", default=None, help="Only mail after YYYY-MM-DD.")
@click.option("--before", default=None, help="Only mail before YYYY-MM-DD.")
@friendly_errors
def history(
    dry_run: bool,
    page_size: int,
    max_pages: int,
    all_mail: bool,
    model: str | None,
    delete_older_than_days: int | None,
    after: str | None,
    before: str | None,
) -> None:
    """Deep-clean older mail, page by page. Age-based trashing is off unless you ask for it."""
    config = config_and_db()
    stats = pipeline.cleanup_history(
        config,
        claude_for(model or config.cleanup.model),
        dry_run=dry_run,
        page_size=page_size,
        max_pages=max_pages,
        all_mail=all_mail,
        delete_older_than_days=delete_older_than_days,
        email_filter=EmailFilter(after=_parse_date(after), before=_parse_date(before)),
    )
    _print_stats(stats, dry_run)
    print_usage_summary()


@cleanup.command()
def stats() -> None:
    """How many emails have been organized, by category and action."""
    rows = cleanup_ledger.summary()
    if not rows:
        console.print("[yellow]No organized emails yet.[/yellow]")
        return
    table = Table(title="Cleanup stats")
    table.add_column("Category", style="cyan")
    table.add_column("Action", style="magenta")
    table.add_column("Count", justify="right", style="green")
    for category, action, count in rows:
        table.add_row(category or "?", action or "?", str(count))
    table.add_section()
    table.add_row("", "[bold]total[/bold]", f"[bold]{cleanup_ledger.total()}[/bold]")
    console.print(table)


@cleanup.command(name="fix-parents")
@friendly_errors
def fix_parents() -> None:
    """Create any missing parent labels so nested labels display correctly (Gmail)."""
    config = config_and_db()
    client = _cleanup_account_client(config)
    taxonomy = build_taxonomy(config.labels)
    existing = {lbl["name"] for lbl in client.list_labels() if lbl.get("name")}
    needed = sorted(
        {path.rsplit("/", 1)[0] for path in taxonomy.label_paths.values() if "/" in path} - existing
    )
    if not needed:
        console.print("[green]All parent labels already exist.[/green]")
        return
    for parent in needed:
        console.print(f"  Creating parent label: {parent}")
        client.get_or_create_label(parent)
    console.print(f"[green]Created {len(needed)} parent label(s).[/green]")


@cleanup.command(name="migrate-labels")
@click.argument("mappings", nargs=-1, required=True)
@friendly_errors
def migrate_labels(mappings: tuple[str, ...]) -> None:
    """Remap labels: "Old=New/Path", "Old=TRASH", or "Old=RECLASSIFY"."""
    config = config_and_db()
    client = _cleanup_account_client(config)
    taxonomy = build_taxonomy(config.labels)
    existing = {lbl["name"]: lbl["id"] for lbl in client.list_labels() if lbl.get("name")}
    for mapping in mappings:
        if "=" not in mapping:
            console.print(f"[red]Invalid mapping (no '='): {mapping}[/red]")
            continue
        old_name, target = (s.strip() for s in mapping.split("=", 1))
        if old_name not in existing:
            console.print(f"[yellow]Label not found: {old_name}[/yellow]")
            continue
        old_id = existing[old_name]
        msg_ids = client.get_messages_by_label(old_id)
        console.print(f"  {old_name}: {len(msg_ids)} messages")
        if target == "TRASH":
            for mid in msg_ids:
                client.trash_email(mid)
            client.delete_label(old_id)
            console.print(f"  [red]Trashed {len(msg_ids)} messages, deleted label.[/red]")
        elif target == "RECLASSIFY":
            for mid in msg_ids:
                client.apply_labels_and_actions(mid, remove_label_ids=[old_id])
            cleanup_ledger.forget(msg_ids)
            client.delete_label(old_id)
            console.print(f"  [cyan]Unlabeled {len(msg_ids)} messages for reclassification.[/cyan]")
        else:
            new_id = client.get_or_create_label(target, color=taxonomy.colors.get(target))
            for mid in msg_ids:
                client.apply_labels_and_actions(
                    mid, add_label_ids=[new_id] if new_id else None, remove_label_ids=[old_id]
                )
            client.delete_label(old_id)
            console.print(f"  [green]Moved {len(msg_ids)} messages to {target}.[/green]")


def _cleanup_account_client(config: Config) -> EmailClient:
    if not config.cleanup.accounts:
        raise click.ClickException("cleanup.accounts is empty in config.yaml")
    client = find_client(build_clients(config), config.cleanup.accounts[0])
    if client is None:
        raise click.ClickException(
            f"Cleanup account {config.cleanup.accounts[0]} is not configured"
        )
    client.authenticate()
    return client
