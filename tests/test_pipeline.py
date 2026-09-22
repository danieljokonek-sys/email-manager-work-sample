from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from email_manager import db, pipeline
from email_manager.config import Config
from email_manager.db import cleanup_ledger
from email_manager.llm import ClaudeClient, LLMOutputError
from email_manager.schemas import ExtractionBatch
from tests.conftest import FakeAnthropic, FakeEmailClient, make_email


def _extraction_for(email_ids: list[str]):  # type: ignore[no-untyped-def]
    def handler(schema: type, kw: dict) -> object:
        return schema.model_validate(
            {
                "emails": [
                    {
                        "email_id": eid,
                        "entity_key": "studio",
                        "summary": "s",
                        "deadlines": [
                            {
                                "entity_key": "studio",
                                "description": f"Deadline from {eid}",
                                "due_date": "2026-10-01",
                            }
                        ],
                    }
                    for eid in email_ids
                ]
            }
        )

    return handler


# ── auth isolation ──────────────────────────────────────────────────────────


def test_authenticate_all_isolates_failures() -> None:
    good, bad = FakeEmailClient("a@x.com"), FakeEmailClient("b@x.com", fail_auth=True)
    result = pipeline.authenticate_all([good, bad])
    assert [c.account_email for c in result.healthy] == ["a@x.com"]
    assert result.failed == [("b@x.com", "token expired")]
    assert result.total == 2


# ── fetch ───────────────────────────────────────────────────────────────────


def test_fetch_stores_new_emails_with_keyword_entity(
    home: Path, config: Config, mailbox: FakeEmailClient
) -> None:
    mailbox.inbox = [
        make_email("m1", subject="Logo round two"),
        make_email("m2", subject="Newsletter"),
    ]
    result = pipeline.fetch_emails(config, [mailbox])
    assert result.stored == 2
    rows = {r["id"]: r for r in db.get_unprocessed_emails()}
    assert rows["m1"]["entity_key"] == "studio" and rows["m2"]["entity_key"] is None
    assert pipeline.fetch_emails(config, [mailbox]).stored == 0  # idempotent
    assert db.get_sync_state("last_email_fetch")


def test_fetch_survives_one_broken_account(home: Path, config: Config) -> None:
    ok = FakeEmailClient("a@x.com")
    ok.inbox = [make_email("m1")]
    broken = FakeEmailClient("b@x.com")

    def boom(**kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("imap down")

    broken.fetch_emails = boom  # type: ignore[method-assign]
    result = pipeline.fetch_emails(config, [ok, broken])
    assert result.stored == 1 and result.failed == ["b@x.com"]


# ── analyze ─────────────────────────────────────────────────────────────────


def test_analyze_stores_extractions_and_leaves_omitted_emails_for_next_run(
    home: Path, config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    for eid in ("m1", "m2", "m3"):
        db.store_email({"id": eid, "subject": eid})
    fake_sdk.messages.parse_handler = _extraction_for(["m1", "m2"])  # m3 omitted by the model
    result = pipeline.analyze(config, claude)
    assert (
        result.emails_considered == 3
        and result.emails_processed == 2
        and result.items_extracted == 2
    )
    assert [e["id"] for e in db.get_unprocessed_emails()] == ["m3"]
    assert len(fake_sdk.messages.calls) == 2  # batch_size 2 -> two calls
    assert fake_sdk.messages.calls[0]["output_format"] is ExtractionBatch


def test_analyze_skips_the_bots_own_digest(
    home: Path, config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    db.store_email({"id": "d1", "subject": "Re: Daily Briefing: Sep 22, 2026"})
    fake_sdk.messages.parse_handler = _extraction_for([])
    result = pipeline.analyze(config, claude)
    assert result.emails_considered == 0 and fake_sdk.messages.calls == []


def test_failed_batch_is_counted_and_retried_later(
    home: Path, config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    db.store_email({"id": "m1", "subject": "x"})

    def cut_off(schema: type, kw: dict) -> object:
        raise LLMOutputError("cut off")

    fake_sdk.messages.parse_handler = cut_off
    result = pipeline.analyze(config, claude)
    assert result.failed_batches == 1 and result.emails_processed == 0
    assert [e["id"] for e in db.get_unprocessed_emails()] == ["m1"]


# ── digest ──────────────────────────────────────────────────────────────────


def test_dry_run_digest_writes_wrapped_html(
    home: Path, config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    db.add_task("Ship it", due_date=(datetime.now().date() + timedelta(days=1)).isoformat())
    fake_sdk.messages.text_handler = lambda kw: (
        "```html\n<h2>Today</h2><ul><li>Ship it</li></ul>\n```"
    )
    result = pipeline.build_digest(config, claude, dry_run=True)
    assert result.html == "<h2>Today</h2><ul><li>Ship it</li></ul>"  # fences stripped
    assert result.written_to == home / "logs" / "last_digest.html"
    text = result.written_to.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in text and "Ship it" in text
    assert fake_sdk.messages.calls[-1]["output_config"] == {"effort": "medium"}
    assert "Sam" in fake_sdk.messages.calls[-1]["system"][0]["text"]


def test_empty_board_skips_claude(
    home: Path, config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    result = pipeline.build_digest(config, claude, dry_run=True)
    assert "Nothing on the board" in result.html
    assert fake_sdk.messages.calls == []


def test_digest_is_sent_from_the_configured_account(
    home: Path, config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    db.add_task("Ship it", due_date=(datetime.now().date() + timedelta(days=1)).isoformat())
    studio, personal = FakeEmailClient("sam@example.com"), FakeEmailClient("sam.home@example.com")
    result = pipeline.build_digest(config, claude, clients=[studio, personal])
    assert result.sent_to == "sam@example.com"
    assert personal.outgoing and not studio.outgoing
    assert personal.outgoing[0][1].startswith("Daily Briefing")


def test_digest_without_recipient_is_a_clear_error(home: Path, claude: ClaudeClient) -> None:
    with pytest.raises(pipeline.PipelineError, match="send_to"):
        pipeline.build_digest(Config(), claude)


def test_reconcile_merges_duplicates_before_writing(
    home: Path, config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    conn = db.get_connection()
    due = (datetime.now().date() + timedelta(days=2)).isoformat()
    for desc in ("Invoice 1042", "Invoice 1042 past due"):
        conn.execute(
            "INSERT INTO financial_items (direction, counterparty, amount, description, due_date) VALUES ('receivable','Northwind',2400,?,?)",
            (desc, due),
        )

    def plan(schema: type, kw: dict) -> object:
        return schema.model_validate(
            {
                "merges": [
                    {
                        "type": "financial_items",
                        "ids": [1, 2],
                        "canonical": {"description": "Invoice 1042"},
                    }
                ]
            }
        )

    fake_sdk.messages.parse_handler = plan
    result = pipeline.build_digest(config, claude, dry_run=True)
    assert result.merged_duplicates == 1
    assert len(result.board["financial_items"]) == 1


# ── follow-ups ──────────────────────────────────────────────────────────────


def test_follow_up_detection_records_unanswered_threads(
    home: Path, config: Config, mailbox: FakeEmailClient
) -> None:
    sent_at = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    mailbox.sent = [
        make_email(
            "s1",
            thread_id="t1",
            subject="Quote for logo",
            recipients=["client@x.com"],
            date=sent_at,
            sender="sam@example.com",
        ),
        make_email(
            "s2",
            thread_id="t2",
            subject="Lunch?",
            recipients=["friend@x.com"],
            date=sent_at,
            sender="sam@example.com",
        ),
        make_email(
            "s3",
            thread_id="t3",
            subject="Daily Briefing: Sep 1",
            recipients=["sam@example.com"],
            date=sent_at,
            sender="sam@example.com",
        ),
    ]
    mailbox.threads = {
        "t1": [mailbox.sent[0]],
        "t2": [
            mailbox.sent[1],
            make_email(
                "r2", thread_id="t2", sender="friend@x.com", date=datetime.now(UTC).isoformat()
            ),
        ],
    }
    found = pipeline.detect_follow_ups(config, [mailbox])
    assert found == 1
    pending = db.get_pending_follow_ups()
    assert [f["thread_id"] for f in pending] == ["t1"]
    assert pending[0]["entity_key"] == "studio" and pending[0]["recipient"] == "client@x.com"


# ── labels ──────────────────────────────────────────────────────────────────


def test_apply_labels_uses_entity_label_and_marks_done(
    home: Path, config: Config, mailbox: FakeEmailClient
) -> None:
    db.store_email({"id": "m1", "account_email": "sam@example.com", "entity_key": "studio"})
    db.mark_email_processed("m1")
    assert pipeline.apply_labels(config, [mailbox]) == 1
    assert mailbox.labels == {"Tracker/Rivera Studio": "id-1"} and mailbox.labeled == [
        ("m1", "id-1")
    ]
    assert db.get_unlabeled_emails() == []


def test_apply_labels_skips_providers_without_labels(home: Path, config: Config) -> None:
    db.store_email({"id": "m1", "account_email": "sam@example.com", "entity_key": "studio"})
    db.mark_email_processed("m1")
    client = FakeEmailClient(labels=False)
    assert pipeline.apply_labels(config, [client]) == 1
    assert client.labeled == [] and db.get_unlabeled_emails() == []


# ── cleanup ─────────────────────────────────────────────────────────────────


def test_cleanup_inbox_records_ledger_and_skips_seen_and_own_digest(
    home: Path,
    config: Config,
    claude: ClaudeClient,
    fake_sdk: FakeAnthropic,
    mailbox: FakeEmailClient,
) -> None:
    mailbox.inbox = [
        make_email("m1"),
        make_email("m2", subject="Daily Briefing: today"),
        make_email("m3"),
    ]
    cleanup_ledger.record_organized(
        "sam@example.com", {"m3": {"category": "Tax", "action": "label"}}
    )
    fake_sdk.messages.parse_handler = lambda schema, kw: schema.model_validate(
        {
            "classifications": [
                {
                    "email_id": "m1",
                    "category": "Junk",
                    "action": "label",
                    "confidence": "high",
                    "reason": "r",
                }
            ]
        }
    )
    stats = pipeline.cleanup_inbox(config, claude, clients=[mailbox])
    assert stats.trashed == 1 and mailbox.trashed == ["m1"]
    assert cleanup_ledger.already_organized(["m1", "m2", "m3"]) == {"m1", "m3"}
    assert "m2" not in fake_sdk.messages.calls[0]["messages"][0]["content"]


def test_cleanup_requires_a_configured_account(home: Path, claude: ClaudeClient) -> None:
    with pytest.raises(pipeline.PipelineError, match=r"cleanup\.accounts"):
        pipeline.cleanup_inbox(Config(), claude, clients=[])
