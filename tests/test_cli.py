"""End-to-end CLI runs against a temporary home, with Claude faked."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from email_manager.cli import cli
from email_manager.cli.usage_report import parse_usage_log
from email_manager.llm import ClaudeClient
from tests.conftest import FakeAnthropic


@pytest.fixture
def configured_home(home: Path) -> Path:
    (home / "config.yaml").write_text(
        "owner:\n  name: Sam Rivera\naccounts: []\ndigest:\n  send_to: sam@example.com\n",
        encoding="utf-8",
    )
    return home


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "digest", "status", "cleanup", "tasks", "demo", "usage"):
        assert command in result.output


def test_status_on_fresh_home(configured_home: Path) -> None:
    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert "Emails tracked" in result.output


def test_missing_config_is_a_clean_error(home: Path) -> None:
    result = CliRunner().invoke(cli, ["status"])
    assert result.exit_code == 1
    assert "config.yaml" in result.output and "Traceback" not in result.output


def test_tasks_lifecycle(configured_home: Path) -> None:
    runner = CliRunner()
    assert (
        runner.invoke(
            cli, ["tasks", "add", "File taxes", "--due", "2026-10-15", "--priority", "high"]
        ).exit_code
        == 0
    )
    listed = runner.invoke(cli, ["tasks", "list"])
    assert "File taxes" in listed.output and "2026-10-15" in listed.output
    assert runner.invoke(cli, ["tasks", "done", "1"]).exit_code == 0
    assert "No pending tasks" in runner.invoke(cli, ["tasks", "list"]).output
    assert runner.invoke(cli, ["tasks", "done", "1"]).exit_code == 1  # already done


def test_followups_list_empty(configured_home: Path) -> None:
    result = CliRunner().invoke(cli, ["followups", "list"])
    assert result.exit_code == 0 and "No follow-ups" in result.output


def test_cleanup_stats_empty(configured_home: Path) -> None:
    result = CliRunner().invoke(cli, ["cleanup", "stats"])
    assert result.exit_code == 0 and "No organized emails" in result.output


def test_digest_dry_run_and_demo_with_fake_claude(
    configured_home: Path,
    fake_sdk: FakeAnthropic,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from email_manager.cli import common

    monkeypatch.setattr(
        "email_manager.cli.briefing.claude_for",
        lambda model: ClaudeClient(model=model, client=fake_sdk, tracker=common.tracker()),  # type: ignore[arg-type]
    )
    fake_sdk.messages.parse_handler = lambda schema, kw: schema(merges=[])
    fake_sdk.messages.text_handler = lambda kw: "<h2>Today</h2><ul><li>Demo item</li></ul>"
    runner = CliRunner()

    result = runner.invoke(cli, ["demo", "--out", str(configured_home / "docs")])
    assert result.exit_code == 0, result.output
    assert (configured_home / "docs" / "sample-digest.html").exists()
    assert "Claude usage this run" in result.output and "bulletin" in result.output

    result = runner.invoke(cli, ["digest", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Nothing on the board" in (configured_home / "logs" / "last_digest.html").read_text(
        encoding="utf-8"
    )


def test_usage_report_parses_log_lines(tmp_path: Path) -> None:
    log = tmp_path / "email_manager.log"
    log.write_text(
        "2026-09-22 08:01:12,345 [INFO] claude-usage: extract: claude-sonnet-5 in=5210 out=880 cache_read=0 cache_write=1200 stop=end_turn cost=$0.0189\n"
        "2026-09-22 08:01:20,000 [INFO] claude-usage: extract: claude-sonnet-5 in=100 out=10 cache_read=1200 cache_write=0 stop=end_turn cost=$0.0005\n"
        "2026-09-22 08:02:00,000 [INFO] email_manager.pipeline: Analyzing batch of 3 emails\n"
        "2026-09-23 08:01:00,000 [INFO] claude-usage: bulletin: claude-sonnet-5 in=4000 out=900 cache_read=0 cache_write=0 stop=end_turn cost=$0.0170\n",
        encoding="utf-8",
    )
    data = parse_usage_log(log)
    assert set(data) == {"2026-09-22", "2026-09-23"}
    assert data["2026-09-22"]["extract"]["calls"] == 2
    assert data["2026-09-22"]["extract"]["cache_read"] == 1200
    assert data["2026-09-23"]["bulletin"]["cost"] == pytest.approx(0.017)
    assert parse_usage_log(tmp_path / "missing.log") == {}


def test_usage_command_with_no_log(configured_home: Path) -> None:
    result = CliRunner().invoke(cli, ["usage"])
    assert result.exit_code == 0
