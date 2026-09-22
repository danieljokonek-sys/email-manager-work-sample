import logging

import pytest
from pydantic import BaseModel

from email_manager.llm import ClaudeClient, LLMOutputError, Usage, UsageTracker
from tests.conftest import FakeAnthropic


class Answer(BaseModel):
    value: int


def test_extract_returns_validated_instance_and_records_usage(
    claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    fake_sdk.messages.parse_handler = lambda schema, kw: schema(value=7)
    out = claude.extract("t", system="sys", user="u", schema=Answer, effort="low")
    assert out == Answer(value=7)
    call = fake_sdk.messages.calls[0]
    assert call["output_format"] is Answer
    assert call["output_config"] == {"effort": "low"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [u.tag for u in claude.tracker.records] == ["t"]


def test_extract_raises_when_output_was_cut_off(
    claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    fake_sdk.messages.parse_handler = lambda schema, kw: schema(value=1)
    fake_sdk.messages.stop_reason = "max_tokens"
    with pytest.raises(LLMOutputError, match="max_tokens"):
        claude.extract("t", system="s", user="u", schema=Answer)


def test_extract_raises_on_missing_parsed_output(
    claude: ClaudeClient, fake_sdk: FakeAnthropic
) -> None:
    fake_sdk.messages.parse_handler = lambda schema, kw: None
    with pytest.raises(LLMOutputError, match="no parseable"):
        claude.extract("t", system="s", user="u", schema=Answer)


def test_text_returns_first_text_block(claude: ClaudeClient, fake_sdk: FakeAnthropic) -> None:
    fake_sdk.messages.text_handler = lambda kw: "<h2>Today</h2>"
    assert claude.text("bulletin", system="s", user="u", effort="medium") == "<h2>Today</h2>"
    assert fake_sdk.messages.calls[0]["output_config"] == {"effort": "medium"}


def test_text_raises_when_empty(claude: ClaudeClient, fake_sdk: FakeAnthropic) -> None:
    fake_sdk.messages.text_handler = lambda kw: "   "
    with pytest.raises(LLMOutputError, match="no text"):
        claude.text("bulletin", system="s", user="u")


def test_usage_cost_applies_cache_multipliers() -> None:
    u = Usage(
        "t",
        "claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        stop_reason="end_turn",
    )
    # $2 uncached + $0.20 cache read + $2.50 cache write
    assert u.cost_usd == pytest.approx(4.70)


def test_usage_cost_is_zero_for_unknown_model() -> None:
    u = Usage("t", "mystery-model", 1000, 1000, 0, 0, "end_turn")
    assert u.cost_usd == 0.0


def test_tracker_groups_by_tag_and_logs_one_line(caplog: pytest.LogCaptureFixture) -> None:
    tracker = UsageTracker()
    with caplog.at_level(logging.INFO, logger="claude-usage"):
        tracker.record(Usage("extract", "claude-sonnet-5", 100, 10, 0, 0, "end_turn"))
        tracker.record(Usage("extract", "claude-sonnet-5", 100, 10, 50, 0, "end_turn"))
        tracker.record(Usage("bulletin", "claude-sonnet-5", 500, 300, 0, 0, "end_turn"))
    by_tag = tracker.by_tag()
    assert by_tag["extract"]["calls"] == 2
    assert by_tag["extract"]["cache_read_tokens"] == 50
    assert set(by_tag) == {"extract", "bulletin"}
    assert tracker.total_cost_usd == pytest.approx(sum(u.cost_usd for u in tracker.records))
    assert any("claude-usage" in r.name and "extract:" in r.getMessage() for r in caplog.records)


def test_client_uses_injected_sdk_and_configured_model(fake_sdk: FakeAnthropic) -> None:
    client = ClaudeClient(model="claude-haiku-4-5", client=fake_sdk)  # type: ignore[arg-type]
    fake_sdk.messages.parse_handler = lambda schema, kw: schema(value=1)
    client.extract("t", system="s", user="u", schema=Answer)
    assert fake_sdk.messages.calls[0]["model"] == "claude-haiku-4-5"
