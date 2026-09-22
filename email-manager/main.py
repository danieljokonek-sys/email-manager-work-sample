#!/usr/bin/env python3
"""
Email Cleanup & Brief Manager — unified CLI entry point.

Combines the Briefing Bot (email analysis + daily digest) with the
Email Cleanup Bot (Gmail classification, labeling, and cleanup).

Usage:
    python main.py setup          # Initialize DB, authenticate Gmail
    python main.py fetch          # Fetch new emails from Gmail
    python main.py analyze        # Analyze unprocessed emails with Claude
    python main.py digest         # Build and send today's bulletin board
    python main.py digest --dry-run   # Build it to logs/last_digest.html, don't send
    python main.py run            # Fetch + analyze + digest + cleanup in one shot
    python main.py status         # Show a summary of what's tracked
    python main.py schedule       # Run on recurring schedule (daemon mode)

The digest is a read-only bulletin board (Today / Next Two Weeks). There is no
check-off: items age off by date. The old web dashboard, `mark`, and `snooze`
commands were retired on 2026-09-16 (see _deprecated/dashboard/).

    # Chorus Crafters order tracking
    python main.py orders list              # Show active order pipeline
    python main.py orders view <id>         # Full order detail
    python main.py orders add               # Manually create an order
    python main.py orders update <id>       # Update order fields
    python main.py orders pipeline          # Revenue pipeline summary

    # Email cleanup
    python main.py cleanup inbox [--dry-run]    # Organize current inbox
    python main.py cleanup history [--dry-run]  # Deep clean historical email
    python main.py cleanup stats                # Show cleanup stats
    python main.py cleanup fix-parents          # Create missing parent labels
    python main.py cleanup migrate-labels ...   # Remap/merge labels
"""
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.version_info < (3, 10):
    print(f"ERROR: Python 3.10 or newer is required. You have {sys.version}")
    sys.exit(1)

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from src.entities import load_config, load_entities
from datetime import date as date_type

from src.briefing.database import (
    init_db,
    store_email,
    store_extractions,
    get_unprocessed_emails,
    get_dashboard_summary,
    get_pending_deadlines,
    get_pending_financial,
    get_active_agreements,
    get_pending_actions,
    get_sync_state,
    set_sync_state,
    mark_email_processed,
    # order tracking
    upsert_song_order,
    update_order,
    get_order,
    get_all_orders,
    get_orders_by_status,
    get_orders_pipeline_summary,
    # calendar
    store_calendar_events,
    get_upcoming_events,
    # tasks
    add_task,
    get_pending_tasks,
    complete_task,
    delete_task,
    # follow-ups
    upsert_follow_up,
    get_pending_follow_ups,
    dismiss_follow_up,
    mark_follow_up_replied,
    get_stale_action_items,
    # labeling
    get_unlabeled_emails,
    mark_emails_labeled,
    # horizon (CLI view)
    get_horizon_data,
    # bulletin board (the emailed digest)
    get_bulletin_items,
)
from src.client_factory import build_clients, build_calendar_clients, get_send_client
from src.email_client import EmailClient
from src.briefing.analyzer import Analyzer
from src.briefing.digest import DigestGenerator
from src.briefing.scheduler import TrackerScheduler
from src.briefing.orders import (
    STATUS_LABELS,
    STATUS_ORDER,
    EVENT_TYPE_LABELS,
    format_order_row,
    format_order_detail,
)
from src.cleanup.organizer import EmailOrganizer, build_label_config
from src.cleanup import database as cleanup_db

load_dotenv()
console = Console(force_terminal=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("email-manager")

# Model defaults when config.yaml doesn't say. Sonnet 5 is the cheapest
# current-generation model ($2 in / $10 out per M tokens as of 2026-09).
DEFAULT_ANALYSIS_MODEL = "claude-sonnet-5"
DEFAULT_CLEANUP_MODEL = "claude-sonnet-5"


SMS_FORWARD_INDICATORS = [
    "fwd: text from", "forwarded text", "sms from", "text message from",
    "message from +1", "new text from",
]


def get_config():
    config_path = Path("config.yaml")
    if not config_path.exists():
        console.print("[red]config.yaml not found.[/red]")
        sys.exit(1)
    return load_config(str(config_path))


def _is_sms_forward(email: dict, config: dict) -> bool:
    """Detect if an email is a forwarded SMS."""
    subject = (email.get("subject") or "").lower()
    prefix = config.get("sms", {}).get(
        "forwarding_email_subject_prefix", "fwd: text from"
    ).lower()
    return subject.startswith(prefix) or any(
        ind in subject for ind in SMS_FORWARD_INDICATORS
    )


# ── Briefing helper functions ───────────────────────────────────────────────

def do_fetch(config, clients=None):
    """Fetch new emails from all configured email accounts."""
    entities = load_entities(config)
    google_cfg = config.get("google", {})
    max_emails = google_cfg.get("max_emails_per_fetch", 100)

    # Determine lookback window
    last_fetch = get_sync_state("last_email_fetch")
    if last_fetch:
        since = datetime.fromisoformat(last_fetch) - timedelta(hours=1)
    else:
        lookback = google_cfg.get("lookback_days", 30)
        since = datetime.now() - timedelta(days=lookback)

    if clients is None:
        clients = build_clients(config)
    total_stored = 0

    for client in clients:
        console.print(f"  Fetching {client.account_email} since {since.strftime('%Y-%m-%d')}...")
        try:
            client.authenticate()
            emails = client.fetch_emails(since=since, max_results=max_emails)
        except Exception as e:
            log.error(f"  Failed to fetch {client.account_email}: {e}")
            continue

        console.print(f"    Found {len(emails)} emails")
        stored = 0
        for email in emails:
            # Detect SMS forwards
            email["is_sms_forward"] = _is_sms_forward(email, config)

            # Keyword-based entity hint using account's primary entities + content
            combined = f"{email.get('subject', '')} {email.get('body_snippet', '')}"
            entity_key = None

            # First check account-level hints from config
            account_cfg = next(
                (a for a in config.get("accounts", []) if a["email"] == client.account_email),
                {}
            )
            primary = account_cfg.get("primary_entities", [])

            # Try keyword match within primary entities first, then all
            candidates = [
                (k, entities[k]) for k in primary if k in entities
            ] + [
                (k, v) for k, v in entities.items() if k not in primary
            ]
            for key, entity in candidates:
                if entity.matches_text(combined):
                    entity_key = key
                    break

            email["entity_key"] = entity_key
            store_email(email)
            stored += 1
            total_stored += 1

        console.print(f"    Stored {stored}")

    set_sync_state("last_email_fetch", datetime.now().isoformat())
    console.print(f"[green]Total emails stored: {total_stored}[/green]")
    return total_stored


def do_fetch_calendar(config, clients=None):
    """Fetch upcoming calendar events from accounts that support calendars."""
    google_cfg = config.get("google", {})
    days_ahead = google_cfg.get("calendar_days_ahead", 30)
    if clients is None:
        clients = build_clients(config)
    cal_clients = build_calendar_clients(config, clients)
    total = 0

    if not cal_clients:
        console.print("  No accounts with calendar support configured.")
        return 0

    for cal in cal_clients:
        console.print(f"  Calendar: {cal.account_email}...")
        try:
            events = cal.fetch_upcoming_events(days_ahead=days_ahead)
            store_calendar_events(events)
            console.print(f"    {len(events)} events")
            total += len(events)
        except Exception as e:
            log.error(f"  Calendar fetch failed for {cal.account_email}: {e}")

    return total



def do_detect_follow_ups(config, clients=None):
    """Scan each account's Sent folder and flag threads with no reply."""
    if not config.get("features", {}).get("follow_up_detection", True):
        return
    follow_up_days = config.get("features", {}).get("follow_up_days", 3)
    entities = load_entities(config)
    if clients is None:
        clients = build_clients(config)
    now_utc = datetime.now(timezone.utc)
    since = now_utc - timedelta(days=follow_up_days * 5)
    cutoff = now_utc - timedelta(days=follow_up_days)
    found = 0

    for client in clients:
        try:
            client.authenticate()
            sent = client.fetch_sent_emails(since=since, max_results=40)
        except Exception as e:
            log.error(f"  Sent fetch failed for {client.account_email}: {e}")
            continue

        # Keep only the most-recently-sent message per thread
        threads: dict[str, dict] = {}
        for email in sent:
            tid = email.get("thread_id")
            if not tid:
                continue
            if tid not in threads or email["date"] > threads[tid]["date"]:
                threads[tid] = email

        for thread_id, last_sent in threads.items():
            # Skip our own briefing digest emails (and replies to them) —
            # they contain follow-up items and would recursively create new follow-ups
            subj = last_sent.get("subject", "")
            bare_subj = subj.lstrip("Re: ").lstrip("Fwd: ")
            if bare_subj.startswith(("Daily Briefing", "Weekly Briefing")):
                continue

            try:
                sent_dt = datetime.fromisoformat(last_sent["date"])
                if sent_dt.tzinfo is None:
                    sent_dt = sent_dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if sent_dt > cutoff:
                continue

            try:
                thread_msgs = client.get_thread_messages(thread_id)
            except Exception:
                continue

            thread_msgs.sort(key=lambda m: m.get("date", ""))
            has_reply = any(
                m["date"] > last_sent["date"]
                and client.account_email.lower() not in m.get("sender", "").lower()
                for m in thread_msgs
            )
            if has_reply:
                mark_follow_up_replied(thread_id)
                continue

            entity_key = None
            subj = last_sent.get("subject", "")
            for key, entity in entities.items():
                if entity.matches_text(subj):
                    entity_key = key
                    break

            days_waiting = (now_utc - sent_dt).days
            recipient = last_sent.get("to", "").split(",")[0].strip()
            upsert_follow_up({
                "thread_id": thread_id,
                "account_email": client.account_email,
                "subject": last_sent.get("subject", "(no subject)"),
                "recipient": recipient,
                "last_sent_date": sent_dt.date().isoformat(),
                "entity_key": entity_key,
                "days_waiting": days_waiting,
            })
            found += 1

    console.print(f"[green]Follow-up detection: {found} thread(s) awaiting reply[/green]")


def do_apply_labels(config, clients=None):
    """Apply Gmail entity labels to classified emails (requires gmail.modify scope)."""
    if not config.get("features", {}).get("auto_label_emails", True):
        return
    entities = load_entities(config)
    entity_label_map = {
        key: f"Tracker/{entity.name}"
        for key, entity in entities.items()
    }
    unlabeled = get_unlabeled_emails(limit=200)
    if not unlabeled:
        return

    by_account: dict[str, list[dict]] = {}
    for email in unlabeled:
        acct = email.get("account_email", "")
        by_account.setdefault(acct, []).append(email)

    if clients is None:
        clients = build_clients(config)
    client_map = {c.account_email: c for c in clients}
    labeled_ids = []

    for account_email, emails in by_account.items():
        client = client_map.get(account_email)
        if not client:
            continue
        if not client.supports_labels:
            # Mark as done even though we can't label (provider doesn't support it)
            labeled_ids.extend(e["id"] for e in emails)
            continue
        try:
            client.authenticate()
        except Exception as e:
            log.error(f"  Label auth failed for {account_email}: {e}")
            continue

        for email in emails:
            entity_key = email.get("entity_key")
            label_name = entity_label_map.get(entity_key)
            if not label_name:
                labeled_ids.append(email["id"])
                continue
            try:
                label_id = client.get_or_create_label(label_name)
                client.apply_label(email["id"], label_id)
                labeled_ids.append(email["id"])
            except Exception as e:
                log.warning(f"  Label failed for {email['id']}: {e}")

    if labeled_ids:
        mark_emails_labeled(labeled_ids)
        console.print(f"[green]Auto-labeled {len(labeled_ids)} email(s) in Gmail[/green]")


def do_analyze(config):
    """Analyze unprocessed emails using Claude."""
    entities = load_entities(config)
    analysis_cfg = config.get("analysis", {})
    model = analysis_cfg.get("model", DEFAULT_ANALYSIS_MODEL)
    effort = analysis_cfg.get("effort", "low")
    batch_size = analysis_cfg.get("batch_size", 20)

    owner = config.get("owner", {})
    analyzer = Analyzer(entities, model=model, effort=effort,
                        owner_name=owner.get("name", ""),
                        owner_profile=owner.get("profile", ""),
                        briefing_priorities=owner.get("briefing_priorities", ""))
    unprocessed = get_unprocessed_emails(limit=batch_size * 5)

    # Skip our own briefing digest emails (and replies/forwards) — analyzing
    # them creates duplicate action items from items already in the brief
    def _is_briefing_email(subj):
        bare = (subj or "").lstrip("Re: ").lstrip("Fwd: ")
        return bare.startswith(("Daily Briefing", "Weekly Briefing"))

    unprocessed = [e for e in unprocessed if not _is_briefing_email(e.get("subject"))]

    if not unprocessed:
        console.print("No unprocessed emails to analyze.")
        return 0

    console.print(f"Analyzing {len(unprocessed)} emails in batches of {batch_size}...")
    total_items = 0

    for i in range(0, len(unprocessed), batch_size):
        batch = unprocessed[i : i + batch_size]
        console.print(f"  Processing batch {i // batch_size + 1} ({len(batch)} emails)...")

        try:
            results = analyzer.analyze_batch(batch)
        except Exception as e:
            log.error(f"Analysis failed for batch: {e}")
            continue

        for email in batch:
            email_id = email["id"]
            if email_id in results:
                extractions = results[email_id]

                if extractions.get("entity_key"):
                    from src.briefing.database import get_connection

                    conn = get_connection()
                    conn.execute(
                        "UPDATE emails SET entity_key = ? WHERE id = ?",
                        (extractions["entity_key"], email_id),
                    )
                    conn.commit()

                store_extractions(email_id, extractions)
                n = (
                    len(extractions.get("agreements", []))
                    + len(extractions.get("deadlines", []))
                    + len(extractions.get("financial_items", []))
                    + len(extractions.get("action_items", []))
                )
                total_items += n
            else:
                mark_email_processed(email_id)

    console.print(f"[green]Extracted {total_items} items from {len(unprocessed)} emails[/green]")
    return total_items


def do_digest(config, digest_type="daily", clients=None, dry_run=False):
    """Build the bulletin board and email it.

    With ``dry_run`` the board is written to logs/last_digest.html instead of
    being sent (no email account is touched; Claude is still called).
    """
    entities = load_entities(config)
    analysis_cfg = config.get("analysis", {})
    digest_cfg = config.get("digest", {})
    model = analysis_cfg.get("model", DEFAULT_ANALYSIS_MODEL)
    effort = analysis_cfg.get("effort", "low")
    digest_effort = digest_cfg.get("effort", "medium")
    days_ahead = int(digest_cfg.get("days_ahead", 14))
    send_to = digest_cfg.get("send_to", "")

    if not send_to and not dry_run:
        console.print("[red]No digest.send_to configured in config.yaml[/red]")
        return

    owner = config.get("owner", {})
    owner_name = owner.get("name", "") or "Daniel"
    first_name = owner_name.split()[0] if owner_name else "Daniel"
    # Sent threads addressed to one of the owner's own accounts are notes to
    # self, not replies being waited on; keep them off the board.
    own_addresses = tuple(a.get("email", "") for a in config.get("accounts", []) if a.get("email"))
    board_kwargs = dict(days_ahead=days_ahead, owner_name=first_name,
                        exclude_recipients=own_addresses)

    board = get_bulletin_items(**board_kwargs)

    # Merge duplicate board items into one canonical entry so an obligation
    # mentioned across several emails shows up once. Only board items are sent.
    if config.get("features", {}).get("reconcile_duplicates", True):
        try:
            from src.briefing.reconcile import reconcile_duplicates
            merged = reconcile_duplicates(model=model, effort=effort, board=board)
            if merged:
                console.print(f"[dim]Merged {merged} duplicate item(s) into canonical entries[/dim]")
                board = get_bulletin_items(**board_kwargs)
        except Exception as e:
            log.error(f"Duplicate reconciliation failed (continuing with digest): {e}")

    analyzer = Analyzer(entities, model=model, effort=effort,
                        owner_name=first_name,
                        owner_profile=owner.get("profile", ""),
                        briefing_priorities=owner.get("briefing_priorities", ""))

    if dry_run:
        generator = DigestGenerator(analyzer, None, entities, send_to,
                                    days_ahead=days_ahead, effort=digest_effort)
        html = generator.build_html(board)
        out = Path("logs/last_digest.html")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(generator._wrap_html(html), encoding="utf-8")
        console.print(f"[green]Dry run: board written to {out} (not sent)[/green]")
        return html

    if clients is None:
        clients = build_clients(config)
    send_client = get_send_client(config, clients)
    send_client.authenticate()

    generator = DigestGenerator(analyzer, send_client, entities, send_to,
                                days_ahead=days_ahead, effort=digest_effort)
    html = generator.generate_and_send(digest_type, board=board)
    console.print(f"[green]Digest sent to {send_to}[/green]")
    return html


# ── Cleanup helper functions ────────────────────────────────────────────────

def _build_cleanup_client(config: dict) -> EmailClient:
    """Build an EmailClient for cleanup operations."""
    cleanup_accounts = config.get("cleanup", {}).get("accounts", [])
    if not cleanup_accounts:
        console.print("[red]No cleanup accounts configured in config.yaml[/red]")
        sys.exit(1)
    account_email = cleanup_accounts[0]
    # Build all clients and find the matching one
    clients = build_clients(config)
    for client in clients:
        if client.account_email == account_email:
            return client
    console.print(f"[red]Cleanup account {account_email} not found in accounts list[/red]")
    sys.exit(1)


def _build_organizer(config: dict, model_override: str | None = None) -> EmailOrganizer:
    cleanup_cfg = config.get("cleanup", {})
    cleanup_accounts = cleanup_cfg.get("accounts", [])
    label_cfg = build_label_config(config)
    return EmailOrganizer(
        owner_name=config["owner"]["name"],
        account_emails=cleanup_accounts,
        model=model_override or cleanup_cfg.get("model", DEFAULT_CLEANUP_MODEL),
        batch_size=cleanup_cfg.get("batch_size", 25),
        label_cfg=label_cfg,
        effort=cleanup_cfg.get("effort", "low"),
    )


def _print_cleanup_stats(stats: dict, dry_run: bool):
    prefix = "[DRY RUN] " if dry_run else ""
    table = Table(title=f"{prefix}Cleanup Results")
    table.add_column("Action", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for k, v in stats.items():
        table.add_row(k.capitalize(), str(v))
    console.print(table)


def _is_own_briefing(email) -> bool:
    """True for the digest the bot emails to the owner.

    The digest lands in the same inbox cleanup sweeps ~20 min later, so without
    this guard cleanup classifies and archives the briefing itself — the user
    never sees it. Subject is set in digest.py as "Daily/Weekly Briefing — …".
    """
    subject = (email.get("subject") or "").strip().lower()
    return subject.startswith("daily briefing") or subject.startswith("weekly briefing")


def do_cleanup_inbox(config, dry_run=False, limit=100):
    """Organize current inbox emails using Claude classification."""
    client = _build_cleanup_client(config)
    client.authenticate()
    organizer = _build_organizer(config)
    conn = cleanup_db.init_db()
    account_email = config.get("cleanup", {}).get("accounts", [""])[0]

    console.print(f"Fetching up to {limit} inbox emails...")
    emails, _ = client.fetch_inbox_emails(max_results=limit)
    if not emails:
        console.print("[yellow]No inbox emails found.[/yellow]")
        return

    seen = cleanup_db.is_email_organized(conn, [e["id"] for e in emails])
    new_emails = [e for e in emails if e["id"] not in seen]
    briefings = [e for e in new_emails if _is_own_briefing(e)]
    if briefings:
        new_emails = [e for e in new_emails if not _is_own_briefing(e)]
        console.print(f"Leaving {len(briefings)} briefing digest email(s) untouched in the inbox.")
    if not new_emails:
        console.print("[yellow]All inbox emails already organized.[/yellow]")
        return

    console.print(f"Organizing {len(new_emails)} new emails ({len(seen)} skipped)...")
    total_stats = {"labeled": 0, "archived": 0, "trashed": 0, "skipped": 0, "errors": 0}

    for i in range(0, len(new_emails), organizer.batch_size):
        batch = new_emails[i:i + organizer.batch_size]
        console.print(f"  Batch {i // organizer.batch_size + 1}: {len(batch)} emails...")
        stats, classifications = organizer.classify_and_act(batch, client, dry_run=dry_run)
        if not dry_run:
            cleanup_db.store_organized_email(conn, account_email, classifications)
        for k in total_stats:
            total_stats[k] += stats[k]

    _print_cleanup_stats(total_stats, dry_run)


# ── CLI ──────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Email Cleanup & Brief Manager — AI-powered email digest and cleanup."""
    pass


@cli.command()
def setup():
    """Initialize the database. Run setup-accounts to authenticate Gmail."""
    init_db()
    console.print("[green]Database initialized.[/green]")
    console.print("Next: run [bold]python main.py setup-accounts[/bold] to authorize your Gmail accounts.")


@cli.command(name="setup-accounts")
def setup_accounts():
    """Authenticate each configured email account."""
    config = get_config()
    init_db()
    clients = build_clients(config)
    accounts = config.get("accounts", [])

    console.print(f"[bold]Authorizing {len(clients)} email account(s)...[/bold]\n")
    has_gmail = False
    for i, client in enumerate(clients):
        acct_name = accounts[i]["name"] if i < len(accounts) else client.account_email
        provider = getattr(client, "provider", "unknown")
        console.print(f"[{i+1}/{len(clients)}] {acct_name} ({client.account_email}) [{provider}]")
        try:
            # interactive=True: if a stored token's refresh is dead, fall back to
            # the browser flow automatically instead of requiring a manual move
            # of the token file out of credentials/.
            client.authenticate(interactive=True)
            console.print(f"  [green]Authorized[/green]\n")
            if provider == "gmail":
                has_gmail = True
        except Exception as e:
            console.print(f"  [red]{e}[/red]\n")

    if has_gmail:
        console.print("[bold]Also enable the Google Calendar API:[/bold]")
        console.print("  console.cloud.google.com > APIs & Services > Library > Calendar API > Enable\n")
    console.print("[green]Done! Run [bold]python main.py run[/bold] to fetch emails and calendar.[/green]")


@cli.command()
def fetch():
    """Fetch new emails from all Gmail accounts."""
    config = get_config()
    init_db()
    console.print("[bold]Fetching emails...[/bold]")
    do_fetch(config)
    console.print("[bold]Fetching calendar events...[/bold]")
    do_fetch_calendar(config)


@cli.command()
def analyze():
    """Analyze unprocessed emails with Claude."""
    config = get_config()
    init_db()
    do_analyze(config)
    console.print("[bold]Applying Gmail labels...[/bold]")
    do_apply_labels(config)


@cli.command()
@click.option("--type", "digest_type", default="daily", type=click.Choice(["daily", "weekly"]))
@click.option("--dry-run", is_flag=True, help="Write the board to logs/last_digest.html instead of emailing it")
def digest(digest_type, dry_run):
    """Build and send today's bulletin board."""
    config = get_config()
    init_db()
    do_digest(config, digest_type, dry_run=dry_run)


def notify_auth_failure(config, failed, total):
    """Alert the owner that one or more accounts need re-authorization.

    Layered so a failure is never silent again, even if every account is down:
      1. email the owner via any account that still authenticates,
      2. write a logs/AUTH_FAILURE.txt marker,
      3. best-effort Windows desktop toast.
    """
    accounts = ", ".join(email for email, _ in failed)
    cmd = "python main.py setup-accounts"
    summary = (
        f"{len(failed)} of {total} email account(s) failed to authenticate and "
        f"were skipped: {accounts}.\n\nRe-authorize by running:\n  {cmd}\n"
    )
    log.error(summary.replace("\n", " "))

    # 1. Email alert via the first account that still works.
    send_to = config.get("digest", {}).get("send_to", "")
    if send_to:
        for client in build_clients(config):
            try:
                client.authenticate()
            except Exception:
                continue
            try:
                client.send_email(
                    to=send_to,
                    subject=f"[Email Manager] {len(failed)} account(s) need re-authorization",
                    body_html=(
                        "<p>The daily email automation could not authenticate some accounts "
                        "and skipped them. The digest/cleanup ran for the rest.</p>"
                        f"<p><b>Failed:</b> {accounts}</p>"
                        f"<p>Fix it by running <code>{cmd}</code> in the email-manager folder.</p>"
                        "<p>(Likely cause: the Google OAuth app's 7-day token expiry — confirm "
                        "the consent screen is published \"In production\".)</p>"
                    ),
                )
                log.info(f"Auth-failure alert emailed to {send_to} via {client.account_email}")
                break
            except Exception as e:
                log.error(f"Could not send alert via {client.account_email}: {e}")

    # 2. Marker file.
    try:
        marker = Path("logs/AUTH_FAILURE.txt")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{datetime.now().isoformat()}\n\n{summary}")
    except Exception as e:
        log.error(f"Could not write AUTH_FAILURE marker: {e}")

    # 3. Best-effort Windows toast.
    if sys.platform == "win32":
        try:
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
                "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$x=$t.GetElementsByTagName('text'); "
                "$x.Item(0).AppendChild($t.CreateTextNode('Email Manager: re-auth needed')) | Out-Null; "
                f"$x.Item(1).AppendChild($t.CreateTextNode('{len(failed)} account(s) failed. Run setup-accounts.')) | Out-Null; "
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Email Manager').Show("
                "[Windows.UI.Notifications.ToastNotification]::new($t))"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=15,
                           capture_output=True)
        except Exception as e:
            log.error(f"Could not show desktop toast: {e}")


@cli.command()
def run():
    """Fetch emails + calendar, analyze, digest, and cleanup in one shot."""
    config = get_config()
    init_db()
    # Build clients once and reuse across all stages (avoids 7x OAuth reads + label cache loss)
    clients = build_clients(config)
    # Authenticate per-account in isolation: one dead token must not abort the
    # whole pipeline. Healthy accounts still get briefed and cleaned.
    healthy, failed = [], []
    for client in clients:
        try:
            client.authenticate()
            healthy.append(client)
        except Exception as e:
            log.error(f"Auth failed for {client.account_email}: {e}")
            failed.append((client.account_email, str(e)))
    if failed:
        notify_auth_failure(config, failed, total=len(clients))
    if not healthy:
        console.print("[red]No accounts could authenticate — aborting. Run: python main.py setup-accounts[/red]")
        raise SystemExit(1)
    clients = healthy
    console.print("[bold]Fetching emails from all accounts...[/bold]")
    do_fetch(config, clients=clients)
    console.print("[bold]Fetching calendar events...[/bold]")
    do_fetch_calendar(config, clients=clients)
    console.print("[bold]Analyzing with Claude...[/bold]")
    do_analyze(config)
    console.print("[bold]Applying Gmail labels...[/bold]")
    do_apply_labels(config, clients=clients)
    console.print("[bold]Detecting follow-ups needed...[/bold]")
    do_detect_follow_ups(config, clients=clients)
    console.print("[bold]Sending digest...[/bold]")
    do_digest(config, clients=clients)
    delay_minutes = config.get("cleanup", {}).get("delay_after_digest_minutes", 20)
    console.print(f"[bold]Waiting {delay_minutes} minutes before cleanup (lets digest settle)...[/bold]")
    time.sleep(delay_minutes * 60)
    console.print("[bold]Running email cleanup...[/bold]")
    do_cleanup_inbox(config)


@cli.command()
def status():
    """Show dashboard summary."""
    init_db()
    summary = get_dashboard_summary()

    table = Table(title="Email Manager Status")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total Emails Tracked", str(summary["total_emails"]))
    table.add_row("Unprocessed Emails", str(summary["unprocessed_emails"]))
    table.add_row("Calendar Events (7 days)", str(summary["upcoming_events_7d"]))
    table.add_row("Active Agreements", str(summary["active_agreements"]))
    table.add_row("Pending Deadlines", str(summary["pending_deadlines"]))
    table.add_row("Money Owed to You", f"${summary['money_owed_to_you']:,.2f}")
    table.add_row("Money You Owe", f"${summary['money_you_owe']:,.2f}")
    table.add_row("Pending Action Items", str(summary["pending_actions"]))
    table.add_row("Stale Actions (3+ days)", str(summary.get("stale_actions", 0)))
    table.add_row("Tasks (floating)", str(summary.get("pending_tasks", 0)))
    table.add_row("Awaiting Reply", str(summary.get("follow_ups_waiting", 0)))
    table.add_section()
    table.add_row("Active Song Orders (CC)", str(summary["active_song_orders"]))
    table.add_row("Song Orders Pipeline Value", f"${summary['song_orders_pipeline_value']:,.2f}")
    console.print(table)

    # Show upcoming calendar events
    events = get_upcoming_events(days_ahead=7)
    if events:
        console.print("\n[bold]Calendar -- Next 7 Days:[/bold]")
        for ev in events:
            when = ev.get("start_datetime") or ev.get("start_date") or "?"
            when = when[:10]
            cal = ev.get("calendar_name", "")
            console.print(f"  {when}  {ev['title']}  [dim]({cal})[/dim]")

    # Show upcoming deadlines
    deadlines = get_pending_deadlines(days_ahead=7)
    if deadlines:
        console.print("\n[bold]Upcoming Deadlines (7 days):[/bold]")
        for d in deadlines:
            entity = d.get("entity_key", "personal") or "personal"
            console.print(f"  [{d.get('priority', 'medium')}] {d['description']} -- due {d.get('due_date', '?')} ({entity})")

    # Show money owed to you
    receivables = get_pending_financial(direction="receivable")
    if receivables:
        console.print("\n[bold]Money Owed to You:[/bold]")
        for r in receivables:
            amt = f"${r['amount']:,.2f}" if r.get("amount") else "amount TBD"
            entity = r.get("entity_key", "personal") or "personal"
            console.print(f"  {r.get('counterparty', '?')} -- {amt} for {r.get('description', '?')} ({entity})")

    # Show pending actions
    actions = get_pending_actions()
    if actions:
        console.print("\n[bold]Action Items:[/bold]")
        for a in actions[:10]:
            entity = a.get("entity_key", "personal") or "personal"
            console.print(f"  [{a.get('priority', 'medium')}] {a['description']} ({entity})")

    # Show floating tasks
    tasks_list = get_pending_tasks()
    if tasks_list:
        console.print("\n[bold]Your Task List:[/bold]")
        for t in tasks_list:
            due = f" -- due {t['due_date']}" if t.get("due_date") else ""
            entity = t.get("entity_key", "personal") or "personal"
            console.print(f"  #{t['id']} [{t.get('priority','medium')}] {t['title']}{due} ({entity})")

    # Show follow-ups
    followups_items = get_pending_follow_ups()
    if followups_items:
        console.print("\n[bold]Awaiting Reply:[/bold]")
        for f in followups_items:
            console.print(f"  #{f['id']} {f['subject']} -> {f.get('recipient','?')} "
                          f"[dim]({f['days_waiting']}d waiting)[/dim]")

    # Show cleanup stats
    try:
        conn = cleanup_db.init_db()
        total_organized = conn.execute("SELECT COUNT(*) FROM organized").fetchone()[0]
        if total_organized:
            console.print(f"\n[bold]Cleanup Stats:[/bold] {total_organized} emails organized total")
    except Exception:
        pass


@cli.command(name="schedule")
def schedule_cmd():
    """Run the tracker on a recurring schedule (daemon mode)."""
    config = get_config()
    init_db()

    def _full_fetch():
        do_fetch(config)
        do_fetch_calendar(config)
        do_apply_labels(config)
        do_detect_follow_ups(config)

    scheduler = TrackerScheduler(
        fetch_fn=_full_fetch,
        analyze_fn=lambda: do_analyze(config),
        digest_fn=lambda dt: do_digest(config, dt),
        config=config,
    )

    console.print("[bold]Starting scheduled tracker...[/bold]")
    console.print("Running initial fetch + analyze cycle...")
    do_fetch(config)
    do_fetch_calendar(config)
    do_analyze(config)
    console.print("[green]Initial cycle complete. Entering schedule loop.[/green]")
    scheduler.run_forever()


# ── Chorus Crafters Order Tracking ──────────────────────────────────────────

@cli.group()
def orders():
    """Chorus Crafters custom song order pipeline."""
    pass


@orders.command(name="list")
@click.option("--all", "show_all", is_flag=True, help="Include completed and cancelled orders")
@click.option("--status", "filter_status", default=None, help="Filter by status")
def orders_list(show_all, filter_status):
    """Show the order pipeline."""
    init_db()

    if filter_status:
        rows = get_orders_by_status(filter_status)
    else:
        rows = get_all_orders(include_closed=show_all)

    if not rows:
        console.print("No orders found.")
        return

    table = Table(title="Chorus Crafters -- Song Order Pipeline")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Client")
    table.add_column("Event")
    table.add_column("Event Date")
    table.add_column("Status")
    table.add_column("Price", justify="right")
    table.add_column("Dep.", justify="center")
    table.add_column("Revs", justify="center")

    for order in rows:
        table.add_row(*format_order_row(order))

    console.print(table)
    console.print(f"\n{len(rows)} order(s) shown. Use [bold]orders view <id>[/bold] for full detail.")


@orders.command(name="view")
@click.argument("order_id", type=int)
def orders_view(order_id):
    """Show full detail for a single order."""
    init_db()
    order = get_order(order_id)
    if not order:
        console.print(f"[red]Order #{order_id} not found.[/red]")
        return
    console.print(format_order_detail(order))


@orders.command(name="add")
def orders_add():
    """Manually create a new song order via interactive prompts."""
    init_db()
    console.print("[bold]New Chorus Crafters Order[/bold]")
    console.print("Press Enter to skip any field.\n")

    def ask(label, default=None):
        val = click.prompt(label, default=default or "", show_default=bool(default))
        return val.strip() or None

    def ask_float(label):
        val = click.prompt(label, default="", show_default=False)
        try:
            return float(val.strip())
        except (ValueError, AttributeError):
            return None

    def ask_int(label, default):
        val = click.prompt(label, default=str(default), show_default=True)
        try:
            return int(val.strip())
        except (ValueError, AttributeError):
            return default

    event_type = click.prompt(
        "Event type",
        type=click.Choice(["wedding", "memorial", "birthday", "anniversary", "other"]),
        default="wedding",
    )

    order = {
        "client_name": ask("Client name"),
        "client_email": ask("Client email"),
        "client_phone": ask("Client phone"),
        "event_type": event_type,
        "event_date": ask("Event date (YYYY-MM-DD)"),
        "honoree_names": ask("Honoree name(s)"),
        "event_notes": ask("Event notes (venue, context)"),
        "song_style": ask("Song style/vibe"),
        "song_story": ask("Story/details for the song"),
        "price": ask_float("Price ($)"),
        "deposit_amount": ask_float("Deposit amount ($)"),
        "revisions_included": ask_int("Revisions included", 2),
        "status": click.prompt(
            "Status",
            type=click.Choice(STATUS_ORDER),
            default="inquiry",
        ),
        "notes": ask("Notes"),
    }

    order_id = upsert_song_order(order)
    console.print(f"\n[green]Order #{order_id} created.[/green]")
    console.print(format_order_detail(get_order(order_id)))


@orders.command(name="update")
@click.argument("order_id", type=int)
@click.option("--status", default=None, type=click.Choice(STATUS_ORDER + ["cancelled"]))
@click.option("--deposit-paid", "deposit_paid", is_flag=True, default=None)
@click.option("--deposit-date", "deposit_date", default=None)
@click.option("--balance-paid", "balance_paid", is_flag=True, default=None)
@click.option("--balance-date", "balance_date", default=None)
@click.option("--demo-sent", "demo_delivered", is_flag=True, default=None)
@click.option("--demo-date", "demo_date", default=None)
@click.option("--final-sent", "final_delivered", is_flag=True, default=None)
@click.option("--final-date", "final_date", default=None)
@click.option("--revision-used", "add_revision", is_flag=True, help="Increment revisions_used by 1")
@click.option("--price", default=None, type=float)
@click.option("--notes", default=None)
@click.option("--event-date", "event_date", default=None)
def orders_update(order_id, status, deposit_paid, deposit_date, balance_paid,
                  balance_date, demo_delivered, demo_date, final_delivered,
                  final_date, add_revision, price, notes, event_date):
    """Update fields on an existing order."""
    init_db()
    order = get_order(order_id)
    if not order:
        console.print(f"[red]Order #{order_id} not found.[/red]")
        return

    fields = {}
    if status is not None:
        fields["status"] = status
    if deposit_paid:
        fields["deposit_paid"] = 1
        if not deposit_date:
            from datetime import date
            deposit_date = date.today().isoformat()
    if deposit_date:
        fields["deposit_date"] = deposit_date
    if balance_paid:
        fields["balance_paid"] = 1
        if not balance_date:
            from datetime import date
            balance_date = date.today().isoformat()
    if balance_date:
        fields["balance_date"] = balance_date
    if demo_delivered:
        fields["demo_delivered"] = 1
        if not demo_date:
            from datetime import date
            demo_date = date.today().isoformat()
    if demo_date:
        fields["demo_date"] = demo_date
    if final_delivered:
        fields["final_delivered"] = 1
        if not final_date:
            from datetime import date
            final_date = date.today().isoformat()
    if final_date:
        fields["final_date"] = final_date
    if add_revision:
        fields["revisions_used"] = (order.get("revisions_used") or 0) + 1
    if price is not None:
        fields["price"] = price
    if notes is not None:
        fields["notes"] = notes
    if event_date is not None:
        fields["event_date"] = event_date

    if not fields:
        console.print("[yellow]Nothing to update -- provide at least one option.[/yellow]")
        return

    update_order(order_id, fields)
    console.print(f"[green]Order #{order_id} updated.[/green]")
    console.print(format_order_detail(get_order(order_id)))


@orders.command(name="pipeline")
def orders_pipeline():
    """Show Chorus Crafters revenue pipeline summary."""
    init_db()
    pipeline = get_orders_pipeline_summary()

    table = Table(title="Chorus Crafters -- Revenue Pipeline")
    table.add_column("Status")
    table.add_column("Orders", justify="right")
    table.add_column("Total Value", justify="right")
    table.add_column("Deposits In", justify="right")
    table.add_column("Balances In", justify="right")

    total_orders = 0
    total_value = 0.0
    total_deps = 0.0
    total_bals = 0.0

    for status in STATUS_ORDER:
        if status not in pipeline:
            continue
        row = pipeline[status]
        label = STATUS_LABELS.get(status, status)
        table.add_row(
            label,
            str(row["count"]),
            f"${row['total_value']:,.2f}",
            f"${row['deposits_collected']:,.2f}",
            f"${row['balances_collected']:,.2f}",
        )
        total_orders += row["count"]
        total_value += row["total_value"]
        total_deps += row["deposits_collected"]
        total_bals += row["balances_collected"]

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total_orders}[/bold]",
        f"[bold]${total_value:,.2f}[/bold]",
        f"[bold]${total_deps:,.2f}[/bold]",
        f"[bold]${total_bals:,.2f}[/bold]",
    )

    console.print(table)
    collected = total_deps + total_bals
    outstanding = total_value - collected
    console.print(f"\nCollected: [green]${collected:,.2f}[/green]   "
                  f"Outstanding: [yellow]${outstanding:,.2f}[/yellow]")


# ── 14-Day Horizon ──────────────────────────────────────────────────────────

@cli.command()
@click.option("--days", default=14, help="How many days ahead to show")
def horizon(days):
    """Show upcoming calendar events cross-referenced with pending items."""
    init_db()
    data = get_horizon_data(days_ahead=days)
    events = data["events"]

    if not events:
        console.print(f"[dim]No calendar events in the next {days} days.[/dim]")
        return

    def _items_for_event(event: dict) -> dict:
        entity = event.get("entity_key")
        raw_date = event.get("start_date") or (event.get("start_datetime") or "")[:10]
        try:
            ev_date = date_type.fromisoformat(raw_date)
        except Exception:
            ev_date = None

        results = {"actions": [], "financial": [], "deadlines": [], "follow_ups": [], "tasks": []}

        for item in data["action_items"]:
            if entity and item.get("entity_key") == entity:
                results["actions"].append(item)
            elif ev_date and item.get("due_date"):
                try:
                    delta = abs((ev_date - date_type.fromisoformat(item["due_date"])).days)
                    if delta <= 5:
                        results["actions"].append(item)
                except Exception:
                    pass

        for item in data["financial_items"]:
            if entity and item.get("entity_key") == entity:
                results["financial"].append(item)

        for item in data["deadlines"]:
            if entity and item.get("entity_key") == entity:
                results["deadlines"].append(item)
            elif ev_date and item.get("due_date"):
                try:
                    delta = abs((ev_date - date_type.fromisoformat(item["due_date"])).days)
                    if delta <= 5:
                        results["deadlines"].append(item)
                except Exception:
                    pass

        for item in data["follow_ups"]:
            if entity and item.get("entity_key") == entity:
                results["follow_ups"].append(item)

        for item in data["tasks"]:
            if entity and item.get("entity_key") == entity:
                results["tasks"].append(item)
            elif ev_date and item.get("due_date"):
                try:
                    delta = abs((ev_date - date_type.fromisoformat(item["due_date"])).days)
                    if delta <= 7:
                        results["tasks"].append(item)
                except Exception:
                    pass

        return results

    console.print(f"\n[bold]14-Day Horizon -- Next {days} Days[/bold]\n")

    today = date_type.today()
    seen_item_ids: dict[str, set] = {k: set() for k in ["actions", "financial", "deadlines", "follow_ups", "tasks"]}

    for event in events:
        raw_date = event.get("start_date") or (event.get("start_datetime") or "")[:10]
        try:
            ev_date = date_type.fromisoformat(raw_date)
            days_away = (ev_date - today).days
            when = f"{raw_date} ({'+' if days_away >= 0 else ''}{days_away}d)"
        except Exception:
            when = raw_date

        cal = event.get("calendar_name", "")
        entity = event.get("entity_key", "")
        entity_tag = f"[dim]{entity}[/dim]" if entity else ""
        console.print(f"[bold cyan]{when}  {event['title']}[/bold cyan]  {entity_tag}  [dim]({cal})[/dim]")

        items = _items_for_event(event)
        has_any = any(v for v in items.values())

        for item in items["deadlines"]:
            if item["id"] in seen_item_ids["deadlines"]:
                continue
            seen_item_ids["deadlines"].add(item["id"])
            due = item.get("due_date", "?")
            pri = item.get("priority", "medium")
            pri_color = "red" if pri == "high" else "yellow"
            console.print(f"   [{pri_color}]{item['description']}[/{pri_color}]  due {due}")

        for item in items["financial"]:
            if item["id"] in seen_item_ids["financial"]:
                continue
            seen_item_ids["financial"].add(item["id"])
            amt = f"${item['amount']:,.2f}" if item.get("amount") else "amount TBD"
            direction = "-> owed to you" if item["direction"] == "receivable" else "<- you owe"
            console.print(f"   {item.get('counterparty','?')} -- {amt}  {direction}  [dim]{item.get('description','')}[/dim]")

        for item in items["actions"]:
            if item["id"] in seen_item_ids["actions"]:
                continue
            seen_item_ids["actions"].add(item["id"])
            due = f"  due {item['due_date']}" if item.get("due_date") else ""
            pri = item.get("priority", "medium")
            pri_color = "red" if pri == "high" else ""
            txt = f"[{pri_color}]{item['description']}[/{pri_color}]" if pri_color else item["description"]
            console.print(f"   {txt}{due}")

        for item in items["tasks"]:
            if item["id"] in seen_item_ids["tasks"]:
                continue
            seen_item_ids["tasks"].add(item["id"])
            due = f"  due {item['due_date']}" if item.get("due_date") else ""
            console.print(f"   [bold]{item['title']}[/bold]{due}  [dim](task #{item['id']})[/dim]")

        for item in items["follow_ups"]:
            if item["id"] in seen_item_ids["follow_ups"]:
                continue
            seen_item_ids["follow_ups"].add(item["id"])
            console.print(f"   No reply from {item.get('recipient','?')}  [dim]{item.get('subject','')}  ({item['days_waiting']}d waiting)[/dim]")

        if not has_any:
            console.print("   [dim]-- nothing pending[/dim]")
        console.print()


# ── Floating Tasks ──────────────────────────────────────────────────────────

@cli.group()
def tasks():
    """Manage your floating task list (todos not tied to any email)."""
    pass


@tasks.command(name="add")
@click.argument("title")
@click.option("--notes", default=None)
@click.option("--entity", "entity_key", default="personal")
@click.option("--due", "due_date", default=None, help="Due date YYYY-MM-DD")
@click.option("--priority", default="medium", type=click.Choice(["high", "medium", "low"]))
def tasks_add(title, notes, entity_key, due_date, priority):
    """Add a new task. Example: tasks add \"File Q1 taxes\" --due 2026-04-15 --priority high"""
    init_db()
    task_id = add_task(title, notes=notes, entity_key=entity_key, due_date=due_date, priority=priority)
    console.print(f"[green]Task #{task_id} added: {title}[/green]")


@tasks.command(name="list")
def tasks_list_cmd():
    """List all pending tasks."""
    init_db()
    items = get_pending_tasks()
    if not items:
        console.print("No pending tasks.")
        return
    table = Table(title="Your Task List")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Priority")
    table.add_column("Title")
    table.add_column("Entity")
    table.add_column("Due Date")
    table.add_column("Notes")
    for t in items:
        priority_style = {"high": "red", "medium": "yellow", "low": "green"}.get(t["priority"], "")
        table.add_row(
            str(t["id"]),
            f"[{priority_style}]{t['priority']}[/{priority_style}]",
            t["title"],
            t.get("entity_key") or "personal",
            t.get("due_date") or "--",
            (t.get("notes") or "")[:50],
        )
    console.print(table)


@tasks.command(name="done")
@click.argument("task_id", type=int)
def tasks_done(task_id):
    """Mark a task as complete."""
    init_db()
    complete_task(task_id)
    console.print(f"[green]Task #{task_id} marked done.[/green]")


@tasks.command(name="delete")
@click.argument("task_id", type=int)
def tasks_delete(task_id):
    """Delete a task permanently."""
    init_db()
    delete_task(task_id)
    console.print(f"[green]Task #{task_id} deleted.[/green]")


# ── Follow-up Tracking ─────────────────────────────────────────────────────

@cli.group()
def followups():
    """View and manage emails awaiting a reply."""
    pass


@followups.command(name="list")
def followups_list():
    """Show all threads where you sent last and haven't heard back."""
    init_db()
    items = get_pending_follow_ups()
    if not items:
        console.print("No follow-ups waiting.")
        return
    table = Table(title="Awaiting Reply")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Subject")
    table.add_column("Sent To")
    table.add_column("Account")
    table.add_column("Last Sent")
    table.add_column("Waiting", justify="right")
    table.add_column("Entity")
    for f in items:
        days = f.get("days_waiting", 0)
        days_style = "red" if days >= 7 else "yellow" if days >= 3 else ""
        table.add_row(
            str(f["id"]),
            (f.get("subject") or "")[:45],
            (f.get("recipient") or "")[:30],
            f.get("account_email", ""),
            f.get("last_sent_date", ""),
            f"[{days_style}]{days}d[/{days_style}]" if days_style else f"{days}d",
            f.get("entity_key") or "personal",
        )
    console.print(table)


@followups.command(name="dismiss")
@click.argument("followup_id", type=int)
def followups_dismiss(followup_id):
    """Dismiss a follow-up (mark as not needed)."""
    init_db()
    dismiss_follow_up(followup_id)
    console.print(f"[green]Follow-up #{followup_id} dismissed.[/green]")


@followups.command(name="scan")
def followups_scan():
    """Manually trigger a follow-up scan of sent emails."""
    config = get_config()
    init_db()
    do_detect_follow_ups(config)


# ── Email Cleanup ───────────────────────────────────────────────────────────

@cli.group()
def cleanup():
    """Email cleanup -- classify, label, archive, and delete emails."""
    pass


@cleanup.command()
@click.option("--dry-run", is_flag=True, help="Preview without making changes")
@click.option("--limit", default=100, help="Max emails to process")
def inbox(dry_run, limit):
    """Organize current inbox emails."""
    config = get_config()
    do_cleanup_inbox(config, dry_run=dry_run, limit=limit)


@cleanup.command()
@click.option("--dry-run", is_flag=True, help="Preview without making changes")
@click.option("--batch-size", default=50, help="Emails per API page")
@click.option("--max-pages", default=20, help="Max pages to process (0=unlimited)")
@click.option("--all-mail", is_flag=True, help="Process all mail, not just inbox")
@click.option("--model", default=None, help="Override Claude model")
@click.option("--delete-older-than", default=None, type=int, help="Delete non-vital emails older than N months")
@click.option("--delete-older-days", default=None, type=int, help="Delete non-vital emails older than N days")
@click.option("--strict", is_flag=True, help="Ultra-aggressive: only strict-vital categories survive")
@click.option("--after", default=None, help="Only process emails after YYYY/MM/DD")
@click.option("--before", default=None, help="Only process emails before YYYY/MM/DD")
def history(dry_run, batch_size, max_pages, all_mail, model, delete_older_than, delete_older_days, strict, after, before):
    """Deep clean historical email."""
    config = get_config()
    client = _build_cleanup_client(config)
    client.authenticate()
    organizer = _build_organizer(config, model_override=model)
    conn = cleanup_db.init_db()
    account_email = config.get("cleanup", {}).get("accounts", [""])[0]

    extra_parts = []
    if after:
        extra_parts.append(f"after:{after}")
    if before:
        extra_parts.append(f"before:{before}")
    extra_query = " ".join(extra_parts)

    delete_days = delete_older_days
    if delete_older_than and not delete_days:
        delete_days = delete_older_than * 30

    fetch_fn = client.fetch_all_emails_paged if all_mail else client.fetch_inbox_emails

    total_stats = {"labeled": 0, "archived": 0, "trashed": 0, "skipped": 0, "errors": 0}
    page_token = None
    page_num = 0

    while True:
        page_num += 1
        if max_pages and page_num > max_pages:
            console.print(f"[yellow]Reached max pages ({max_pages}). Stopping.[/yellow]")
            break

        console.print(f"Page {page_num}: fetching up to {batch_size} emails...")
        emails, page_token = fetch_fn(max_results=batch_size, page_token=page_token, extra_query=extra_query)
        if not emails:
            console.print("[yellow]No more emails.[/yellow]")
            break

        seen = cleanup_db.is_email_organized(conn, [e["id"] for e in emails])
        new_emails = [e for e in emails if e["id"] not in seen]
        if not new_emails:
            console.print(f"  All {len(emails)} emails already organized, skipping page.")
            if not page_token:
                break
            continue

        console.print(f"  Organizing {len(new_emails)} new emails ({len(seen)} skipped)...")
        for i in range(0, len(new_emails), organizer.batch_size):
            batch = new_emails[i:i + organizer.batch_size]
            stats, classifications = organizer.classify_and_act(
                batch, client, dry_run=dry_run, delete_older_than_days=delete_days, strict=strict,
            )
            if not dry_run:
                cleanup_db.store_organized_email(conn, account_email, classifications)
            for k in total_stats:
                total_stats[k] += stats[k]

        if not page_token:
            break

    _print_cleanup_stats(total_stats, dry_run)


@cleanup.command()
def stats():
    """Show category breakdown of organized emails."""
    conn = cleanup_db.init_db()
    rows = cleanup_db.get_organized_summary(conn)
    if not rows:
        console.print("[yellow]No organized emails yet.[/yellow]")
        return

    table = Table(title="Email Cleanup Stats")
    table.add_column("Category", style="cyan")
    table.add_column("Action", style="magenta")
    table.add_column("Count", justify="right", style="green")
    for category, action, count in rows:
        table.add_row(category or "?", action or "?", str(count))

    total = conn.execute("SELECT COUNT(*) FROM organized").fetchone()[0]
    table.add_row("", "[bold]TOTAL[/bold]", f"[bold]{total}[/bold]")
    console.print(table)


@cleanup.command("fix-parents")
def fix_parents():
    """Create missing parent labels for Gmail nesting."""
    config = get_config()
    client = _build_cleanup_client(config)
    client.authenticate()

    label_cfg = build_label_config(config)
    existing = {l["name"]: l["id"] for l in client.list_labels() if l.get("name")}
    parents_needed = set()
    for label_path in label_cfg["label_path_map"].values():
        if "/" in label_path:
            parent = label_path.rsplit("/", 1)[0]
            if parent not in existing:
                parents_needed.add(parent)

    if not parents_needed:
        console.print("[green]All parent labels already exist.[/green]")
        return

    for parent in sorted(parents_needed):
        console.print(f"  Creating parent label: {parent}")
        client.get_or_create_label(parent)
    console.print(f"[green]Created {len(parents_needed)} parent labels.[/green]")


@cleanup.command("migrate-labels")
@click.argument("mappings", nargs=-1, required=True)
def migrate_labels(mappings):
    """Remap/merge labels. Each argument: "Old Label=New/Path", "Old Label=TRASH", or "Old Label=RECLASSIFY"."""
    config = get_config()
    client = _build_cleanup_client(config)
    client.authenticate()

    existing = {l["name"]: l["id"] for l in client.list_labels() if l.get("name")}

    for mapping in mappings:
        if "=" not in mapping:
            console.print(f"[red]Invalid mapping (no '='): {mapping}[/red]")
            continue

        old_name, new_target = mapping.split("=", 1)
        old_name, new_target = old_name.strip(), new_target.strip()

        if old_name not in existing:
            console.print(f"[yellow]Label not found: {old_name}[/yellow]")
            continue

        old_id = existing[old_name]
        msg_ids = client.get_messages_by_label(old_id)
        console.print(f"  {old_name}: {len(msg_ids)} messages")

        if new_target == "TRASH":
            for mid in msg_ids:
                client.trash_email(mid)
            client.delete_label(old_id)
            console.print(f"  [red]Trashed {len(msg_ids)} messages, deleted label.[/red]")

        elif new_target == "RECLASSIFY":
            conn = cleanup_db.init_db()
            for mid in msg_ids:
                client.apply_labels_and_actions(mid, remove_label_ids=[old_id])
                conn.execute("DELETE FROM organized WHERE email_id = ?", (mid,))
            conn.commit()
            client.delete_label(old_id)
            console.print(f"  [cyan]Unlabeled {len(msg_ids)} messages for reclassification, deleted label.[/cyan]")

        else:
            label_cfg = build_label_config(config)
            color = label_cfg["label_colors"].get(new_target)
            new_id = client.get_or_create_label(new_target, color=color)
            for mid in msg_ids:
                client.apply_labels_and_actions(mid, add_label_ids=[new_id], remove_label_ids=[old_id])
            client.delete_label(old_id)
            console.print(f"  [green]Moved {len(msg_ids)} messages to {new_target}, deleted old label.[/green]")


if __name__ == "__main__":
    cli()
