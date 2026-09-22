"""Setup, the pipeline stages, and the full daily run."""

from __future__ import annotations

from pathlib import Path

import click

from email_manager import db, pipeline
from email_manager.cli.common import (
    claude_for,
    config_and_db,
    console,
    friendly_errors,
    print_usage_summary,
)
from email_manager.client_factory import build_clients


@click.command(name="setup")
@friendly_errors
def setup_cmd() -> None:
    """Initialize the database."""
    db.init_db()
    console.print("[green]Database initialized.[/green]")
    console.print("Next: [bold]email-manager setup-accounts[/bold] to authorize your accounts.")


@click.command(name="setup-accounts")
@friendly_errors
def setup_accounts() -> None:
    """Authenticate each configured account (opens a browser when needed)."""
    config = config_and_db()
    clients = build_clients(config)
    console.print(f"[bold]Authorizing {len(clients)} account(s)...[/bold]\n")
    has_gmail = False
    for i, client in enumerate(clients, 1):
        console.print(f"[{i}/{len(clients)}] {client.account_email} [{client.provider}]")
        try:
            # interactive=True: a dead refresh token falls back to the browser flow.
            client.authenticate(interactive=True)
            console.print("  [green]Authorized[/green]\n")
            has_gmail = has_gmail or client.provider == "gmail"
        except Exception as e:
            console.print(f"  [red]{e}[/red]\n")
    if has_gmail:
        console.print("[bold]Also enable the Google Calendar API:[/bold]")
        console.print(
            "  console.cloud.google.com > APIs & Services > Library > Calendar API > Enable\n"
        )
    console.print(
        "[green]Done. Run [bold]email-manager run[/bold] to fetch, analyze, and send.[/green]"
    )


@click.command()
@friendly_errors
def fetch() -> None:
    """Fetch new emails and calendar events from every account."""
    config = config_and_db()
    auth = pipeline.authenticate_all(build_clients(config))
    for account, error in auth.failed:
        console.print(f"[red]{account}: {error}[/red]")
    result = pipeline.fetch_emails(config, auth.healthy)
    events = pipeline.fetch_calendar(config, auth.healthy)
    for account, n in result.per_account.items():
        console.print(f"  {account}: {n} new")
    console.print(
        f"[green]{result.stored} new emails stored, {events} calendar events refreshed[/green]"
    )


@click.command()
@friendly_errors
def analyze() -> None:
    """Extract deadlines, money, and action items from unprocessed emails."""
    config = config_and_db()
    result = pipeline.analyze(config, claude_for(config.analysis.model))
    if result.emails_considered == 0:
        console.print("No unprocessed emails to analyze.")
        return
    console.print(
        f"[green]Extracted {result.items_extracted} items from "
        f"{result.emails_processed}/{result.emails_considered} emails[/green]"
    )
    if result.failed_batches:
        console.print(
            f"[yellow]{result.failed_batches} batch(es) failed and will be retried next run[/yellow]"
        )
    auth = pipeline.authenticate_all(build_clients(config))
    labeled = pipeline.apply_labels(config, auth.healthy)
    if labeled:
        console.print(f"Applied entity labels to {labeled} email(s)")
    print_usage_summary()


@click.command()
@click.option("--type", "digest_type", default="daily", type=click.Choice(["daily", "weekly"]))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Write the board to logs/last_digest.html instead of emailing it.",
)
@friendly_errors
def digest(digest_type: str, dry_run: bool) -> None:
    """Build today's bulletin board and email it."""
    config = config_and_db()
    result = pipeline.build_digest(
        config, claude_for(config.analysis.model), dry_run=dry_run, digest_type=digest_type
    )
    if result.merged_duplicates:
        console.print(f"[dim]Merged {result.merged_duplicates} duplicate item(s)[/dim]")
    if result.written_to:
        console.print(f"[green]Dry run: board written to {result.written_to} (not sent)[/green]")
    else:
        console.print(f"[green]Digest sent to {result.sent_to}: {result.subject}[/green]")
    print_usage_summary()


@click.command()
@click.option("--skip-cleanup", is_flag=True, help="Stop after the digest.")
@click.option("--no-wait", is_flag=True, help="Do not pause between digest and cleanup.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Write the digest to disk and preview cleanup without changing mail.",
)
@friendly_errors
def run(skip_cleanup: bool, no_wait: bool, dry_run: bool) -> None:
    """Fetch, analyze, send the digest, then clean the inbox. The daily pipeline."""
    config = config_and_db()
    report = pipeline.run_all(
        config,
        claude_for(config.analysis.model),
        claude_for(config.cleanup.model),
        skip_cleanup=skip_cleanup,
        wait_before_cleanup=not no_wait,
        dry_run=dry_run,
    )
    for account, error in report.auth.failed:
        console.print(f"[red]Skipped {account}: {error}[/red]")
    if report.fetch:
        console.print(
            f"Fetched {report.fetch.stored} new emails; {report.calendar_events} calendar events"
        )
    if report.analyze:
        console.print(
            f"Extracted {report.analyze.items_extracted} items from {report.analyze.emails_processed} emails"
        )
    if report.digest:
        target = report.digest.written_to or report.digest.sent_to
        console.print(f"Digest: {target}")
    if report.cleanup:
        console.print(f"Cleanup: {report.cleanup.as_dict()}")
    print_usage_summary()
    if report.errors:
        for err in report.errors:
            console.print(f"[red]{err}[/red]")
        raise SystemExit(1)


@click.command()
@click.option(
    "--out",
    "out_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Also copy the board here as sample-digest.html.",
)
@friendly_errors
def demo(out_dir: Path | None) -> None:
    """Build a board from bundled sample data (no mailbox needed; one Claude call)."""
    from email_manager.demo import run_demo

    path = run_demo(claude_for, out_dir=out_dir)
    console.print(f"[green]Demo board written to {path}[/green]")
    print_usage_summary()
