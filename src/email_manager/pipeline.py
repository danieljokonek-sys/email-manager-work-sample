"""The stages of a run, as plain functions that return results.

Nothing here prints. Each stage logs its progress and returns a small result
object; the CLI decides what to show. That split is what lets the stages be
driven from tests with fake clients and a fake Claude.

Order of a full run and why:

    authenticate -> fetch -> calendar -> analyze -> label -> follow-ups -> digest -> (wait) -> cleanup

Analysis runs before cleanup so the briefing sees mail before cleanup can
archive it; the wait lets the digest land in the inbox before cleanup sweeps
it (and cleanup skips the digest itself regardless).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from email_manager import db, paths
from email_manager.briefing.analyzer import Analyzer
from email_manager.briefing.digest import DigestGenerator, collect_board, is_own_briefing
from email_manager.briefing.reconcile import reconcile_duplicates
from email_manager.cleanup.organizer import CleanupStats, EmailOrganizer
from email_manager.client_factory import (
    build_calendar_clients,
    build_clients,
    find_client,
    get_send_client,
)
from email_manager.config import Config
from email_manager.db import cleanup_ledger
from email_manager.email_client import EmailClient, EmailFilter
from email_manager.llm import ClaudeClient, LLMOutputError

log = logging.getLogger(__name__)

DRY_RUN_DIGEST_FILENAME = "last_digest.html"


class PipelineError(RuntimeError):
    """A stage could not complete. The message is meant for the operator."""


# ── authentication ──────────────────────────────────────────────────────────


@dataclass
class AuthResult:
    healthy: list[EmailClient]
    failed: list[tuple[str, str]]

    @property
    def total(self) -> int:
        return len(self.healthy) + len(self.failed)


def authenticate_all(clients: Sequence[EmailClient]) -> AuthResult:
    """Authenticate each account in isolation: one dead token must not stop the others."""
    healthy: list[EmailClient] = []
    failed: list[tuple[str, str]] = []
    for client in clients:
        try:
            client.authenticate()
            healthy.append(client)
        except Exception as e:
            log.error("Auth failed for %s: %s", client.account_email, e, exc_info=True)
            failed.append((client.account_email, str(e)))
    return AuthResult(healthy, failed)


# ── fetch ───────────────────────────────────────────────────────────────────


@dataclass
class FetchResult:
    stored: int = 0
    per_account: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


def _guess_entity(config: Config, client: EmailClient, email: dict[str, Any]) -> str | None:
    """Cheap keyword pass so labeling works even before Claude has seen the email."""
    text = f"{email.get('subject', '')} {email.get('body_snippet', '')}"
    account = config.account_by_email(client.account_email)
    primary = account.primary_entities if account else []
    ordered = [(k, config.entities[k]) for k in primary if k in config.entities]
    ordered += [(k, e) for k, e in config.entities.items() if k not in primary]
    return next((key for key, entity in ordered if entity.matches_text(text)), None)


def fetch_emails(config: Config, clients: Sequence[EmailClient]) -> FetchResult:
    result = FetchResult()
    last_fetch = db.get_sync_state("last_email_fetch")
    if last_fetch:
        since = datetime.fromisoformat(last_fetch) - timedelta(hours=1)
    else:
        since = datetime.now() - timedelta(days=config.google.lookback_days)

    for client in clients:
        log.info("Fetching %s since %s", client.account_email, since.date())
        try:
            emails = client.fetch_emails(
                since=since, max_results=config.google.max_emails_per_fetch
            )
        except Exception:
            log.error("Fetch failed for %s", client.account_email, exc_info=True)
            result.failed.append(client.account_email)
            continue
        stored = 0
        for email in emails:
            row: dict[str, Any] = dict(email)
            row["entity_key"] = _guess_entity(config, client, row)
            if db.store_email(row):
                stored += 1
        result.per_account[client.account_email] = stored
        result.stored += stored
        log.info("  %d fetched, %d new", len(emails), stored)

    db.set_sync_state("last_email_fetch", datetime.now().isoformat())
    return result


def fetch_calendar(config: Config, clients: Sequence[EmailClient]) -> int:
    total = 0
    for cal in build_calendar_clients(config, list(clients)):
        try:
            events = cal.fetch_upcoming_events(days_ahead=config.google.calendar_days_ahead)
            total += db.store_calendar_events(events)
            log.info("Calendar %s: %d events", cal.account_email, len(events))
        except Exception:
            log.error("Calendar fetch failed for %s", cal.account_email, exc_info=True)
    return total


# ── follow-ups ──────────────────────────────────────────────────────────────


def detect_follow_ups(config: Config, clients: Sequence[EmailClient]) -> int:
    """Scan each account's sent folder and record threads with no reply yet."""
    if not config.features.follow_up_detection:
        return 0
    wait_days = config.features.follow_up_days
    now = datetime.now(UTC)
    since = now - timedelta(days=wait_days * 5)
    cutoff = now - timedelta(days=wait_days)
    found = 0

    for client in clients:
        try:
            sent = client.fetch_sent_emails(since=since, max_results=40)
        except Exception:
            log.error("Sent fetch failed for %s", client.account_email, exc_info=True)
            continue

        latest_per_thread: dict[str, dict[str, Any]] = {}
        for email in sent:
            tid = email.get("thread_id")
            if tid and (
                tid not in latest_per_thread or email["date"] > latest_per_thread[tid]["date"]
            ):
                latest_per_thread[tid] = dict(email)

        for thread_id, last_sent in latest_per_thread.items():
            if is_own_briefing(last_sent.get("subject")):
                continue
            try:
                sent_at = datetime.fromisoformat(last_sent["date"])
            except (KeyError, ValueError):
                continue
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=UTC)
            if sent_at > cutoff:
                continue
            try:
                thread = client.get_thread_messages(thread_id)
            except Exception:
                log.warning("Could not load thread %s", thread_id, exc_info=True)
                continue
            me = client.account_email.lower()
            replied = any(
                m.get("date", "") > last_sent["date"] and me not in m.get("sender", "").lower()
                for m in thread
            )
            if replied:
                db.mark_follow_up_replied(thread_id)
                continue
            subject = last_sent.get("subject", "")
            entity_key = next(
                (k for k, e in config.entities.items() if e.matches_text(subject)), None
            )
            recipients = last_sent.get("recipients") or []
            db.upsert_follow_up(
                {
                    "thread_id": thread_id,
                    "account_email": client.account_email,
                    "subject": subject or "(no subject)",
                    "recipient": recipients[0] if recipients else "",
                    "last_sent_date": sent_at.date().isoformat(),
                    "entity_key": entity_key,
                    "days_waiting": (now - sent_at).days,
                }
            )
            found += 1
    log.info("Follow-up detection: %d thread(s) awaiting reply", found)
    return found


# ── labels ──────────────────────────────────────────────────────────────────


def apply_labels(config: Config, clients: Sequence[EmailClient]) -> int:
    """Apply entity labels in providers that support them. Returns how many emails were marked done."""
    if not config.features.auto_label_emails:
        return 0
    label_for_entity = {key: f"Tracker/{entity.name}" for key, entity in config.entities.items()}
    unlabeled = db.get_unlabeled_emails(limit=200)
    if not unlabeled:
        return 0

    by_account: dict[str, list[dict[str, Any]]] = {}
    for email in unlabeled:
        by_account.setdefault(email.get("account_email", ""), []).append(email)

    done: list[str] = []
    for account_email, emails in by_account.items():
        client = find_client(list(clients), account_email)
        if client is None:
            continue
        if not client.supports_labels:
            done.extend(e["id"] for e in emails)  # nothing to do on this provider
            continue
        for email in emails:
            label_name = label_for_entity.get(email.get("entity_key") or "")
            if not label_name:
                done.append(email["id"])
                continue
            try:
                label_id = client.get_or_create_label(label_name)
                if label_id:
                    client.apply_label(email["id"], label_id)
                done.append(email["id"])
            except Exception:
                log.warning("Label failed for %s", email["id"], exc_info=True)
    db.mark_emails_labeled(done)
    return len(done)


# ── analyze ─────────────────────────────────────────────────────────────────


@dataclass
class AnalyzeResult:
    emails_considered: int = 0
    emails_processed: int = 0
    items_extracted: int = 0
    failed_batches: int = 0


def analyze(config: Config, claude: ClaudeClient) -> AnalyzeResult:
    """Run extraction on unprocessed emails in batches.

    A batch whose call fails is left unprocessed and retried next run. An email
    the model leaves out of its answer is likewise left for next time; only
    emails with a returned extraction are marked processed.
    """
    analyzer = Analyzer(claude, config)
    batch_size = config.analysis.batch_size
    pending = [
        e
        for e in db.get_unprocessed_emails(limit=batch_size * 5)
        if not is_own_briefing(e.get("subject"))
    ]
    result = AnalyzeResult(emails_considered=len(pending))
    if not pending:
        return result

    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        log.info("Analyzing batch of %d emails", len(batch))
        try:
            extractions = analyzer.analyze_batch(batch)
        except LLMOutputError:
            log.error("Extraction batch failed; will retry next run", exc_info=True)
            result.failed_batches += 1
            continue
        except Exception:
            log.error("Extraction batch raised; will retry next run", exc_info=True)
            result.failed_batches += 1
            continue
        for email in batch:
            extraction = extractions.get(email["id"])
            if extraction is None:
                continue
            if extraction.entity_key:
                db.set_email_entity(email["id"], extraction.entity_key)
            result.items_extracted += db.store_extractions(email["id"], extraction)
            result.emails_processed += 1
    return result


# ── digest ──────────────────────────────────────────────────────────────────


@dataclass
class DigestResult:
    html: str
    board: dict[str, list[dict[str, Any]]]
    merged_duplicates: int = 0
    sent_to: str | None = None
    subject: str | None = None
    written_to: Path | None = None


def build_digest(
    config: Config,
    claude: ClaudeClient,
    *,
    clients: Sequence[EmailClient] | None = None,
    dry_run: bool = False,
    digest_type: str = "daily",
) -> DigestResult:
    """Assemble today's board, merge duplicates, write it, and send it (or write it to disk)."""
    if not config.digest.send_to and not dry_run:
        raise PipelineError("digest.send_to is not set in config.yaml")

    board = collect_board(config)
    merged = 0
    if config.features.reconcile_duplicates:
        try:
            merged = reconcile_duplicates(claude, config, board)
            if merged:
                board = collect_board(config)
        except Exception:
            log.error(
                "Duplicate reconciliation failed; continuing with the unmerged board", exc_info=True
            )

    analyzer = Analyzer(claude, config)
    result = DigestResult(html="", board=board, merged_duplicates=merged)

    if dry_run:
        generator = DigestGenerator(analyzer, config)
        result.html = generator.build_html(board)
        out = paths.logs_dir() / DRY_RUN_DIGEST_FILENAME
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(generator.wrap_html(result.html), encoding="utf-8")
        result.written_to = out
        return result

    client_list = list(clients) if clients is not None else build_clients(config)
    send_client = get_send_client(config, client_list)
    if clients is None:
        send_client.authenticate()
    generator = DigestGenerator(analyzer, config, send_client)
    result.html = generator.build_html(board)
    result.subject = generator.send(result.html, digest_type)
    result.sent_to = config.digest.send_to
    return result


# ── cleanup ─────────────────────────────────────────────────────────────────


def _cleanup_client(
    config: Config, clients: Sequence[EmailClient] | None
) -> tuple[EmailClient, str]:
    if not config.cleanup.accounts:
        raise PipelineError("cleanup.accounts is empty in config.yaml")
    account_email = config.cleanup.accounts[0]
    client_list = list(clients) if clients is not None else build_clients(config)
    client = find_client(client_list, account_email)
    if client is None:
        raise PipelineError(f"Cleanup account {account_email} is not in the accounts list")
    if clients is None:
        client.authenticate()
    return client, account_email


def cleanup_inbox(
    config: Config,
    claude: ClaudeClient,
    *,
    clients: Sequence[EmailClient] | None = None,
    limit: int = 100,
    dry_run: bool = False,
) -> CleanupStats:
    """Classify and organize the current inbox, skipping anything already in the ledger."""
    client, account_email = _cleanup_client(config, clients)
    organizer = EmailOrganizer(claude, config)
    emails, _ = client.fetch_inbox_emails(max_results=limit)
    seen = cleanup_ledger.already_organized(e["id"] for e in emails)
    fresh = [e for e in emails if e["id"] not in seen and not is_own_briefing(e.get("subject"))]
    total = CleanupStats()
    if not fresh:
        return total
    log.info("Organizing %d new emails (%d already organized)", len(fresh), len(seen))
    for start in range(0, len(fresh), organizer.batch_size):
        batch = fresh[start : start + organizer.batch_size]
        stats, classifications = organizer.classify_and_act(batch, client, dry_run=dry_run)
        if not dry_run and classifications:
            cleanup_ledger.record_organized(
                account_email, {k: v.model_dump() for k, v in classifications.items()}
            )
        total.add(stats)
    return total


def cleanup_history(
    config: Config,
    claude: ClaudeClient,
    *,
    clients: Sequence[EmailClient] | None = None,
    dry_run: bool = False,
    page_size: int = 50,
    max_pages: int = 20,
    all_mail: bool = False,
    delete_older_than_days: int | None = None,
    email_filter: EmailFilter | None = None,
) -> CleanupStats:
    """Walk older mail page by page and organize it under the same guards."""
    client, account_email = _cleanup_client(config, clients)
    organizer = EmailOrganizer(claude, config)
    fetch = client.fetch_all_emails_paged if all_mail else client.fetch_inbox_emails
    total = CleanupStats()
    page_token: str | None = None
    for page in range(1, (max_pages or 10**6) + 1):
        emails, page_token = fetch(
            max_results=page_size, page_token=page_token, email_filter=email_filter
        )
        if not emails:
            break
        seen = cleanup_ledger.already_organized(e["id"] for e in emails)
        fresh = [e for e in emails if e["id"] not in seen and not is_own_briefing(e.get("subject"))]
        log.info("Page %d: %d emails, %d new", page, len(emails), len(fresh))
        for start in range(0, len(fresh), organizer.batch_size):
            batch = fresh[start : start + organizer.batch_size]
            stats, classifications = organizer.classify_and_act(
                batch, client, dry_run=dry_run, delete_older_than_days=delete_older_than_days
            )
            if not dry_run and classifications:
                cleanup_ledger.record_organized(
                    account_email, {k: v.model_dump() for k, v in classifications.items()}
                )
            total.add(stats)
        if not page_token:
            break
    return total


# ── full run ────────────────────────────────────────────────────────────────


@dataclass
class RunReport:
    auth: AuthResult
    fetch: FetchResult | None = None
    calendar_events: int = 0
    analyze: AnalyzeResult | None = None
    labeled: int = 0
    follow_ups: int = 0
    digest: DigestResult | None = None
    cleanup: CleanupStats | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def run_all(
    config: Config,
    analysis_claude: ClaudeClient,
    cleanup_claude: ClaudeClient,
    *,
    skip_cleanup: bool = False,
    wait_before_cleanup: bool = True,
    dry_run: bool = False,
) -> RunReport:
    """The daily pipeline. Returns a report; raises only if no account works at all."""
    from email_manager.notify import notify_auth_failure

    clients = build_clients(config)
    auth = authenticate_all(clients)
    if auth.failed:
        notify_auth_failure(config, auth.healthy, auth.failed, total=auth.total)
    if not auth.healthy:
        raise PipelineError("No accounts could authenticate. Run: python main.py setup-accounts")

    report = RunReport(auth=auth)
    report.fetch = fetch_emails(config, auth.healthy)
    report.calendar_events = fetch_calendar(config, auth.healthy)
    report.analyze = analyze(config, analysis_claude)
    if report.analyze.failed_batches:
        report.errors.append(f"{report.analyze.failed_batches} extraction batch(es) failed")
    report.labeled = apply_labels(config, auth.healthy)
    report.follow_ups = detect_follow_ups(config, auth.healthy)
    try:
        report.digest = build_digest(config, analysis_claude, clients=auth.healthy, dry_run=dry_run)
    except Exception as e:
        log.error("Digest failed", exc_info=True)
        report.errors.append(f"digest failed: {e}")

    if skip_cleanup or not config.cleanup.accounts:
        return report
    if wait_before_cleanup and config.cleanup.delay_after_digest_minutes > 0:
        import time

        log.info("Waiting %d minutes before cleanup", config.cleanup.delay_after_digest_minutes)
        time.sleep(config.cleanup.delay_after_digest_minutes * 60)
    try:
        report.cleanup = cleanup_inbox(
            config, cleanup_claude, clients=auth.healthy, dry_run=dry_run
        )
    except Exception as e:
        log.error("Cleanup failed", exc_info=True)
        report.errors.append(f"cleanup failed: {e}")
    return report
