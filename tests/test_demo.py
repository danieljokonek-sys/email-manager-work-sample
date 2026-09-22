from pathlib import Path

from email_manager import db
from email_manager.briefing.digest import collect_board
from email_manager.demo import DEMO_CONFIG, run_demo, seed
from email_manager.llm import ClaudeClient
from tests.conftest import FakeAnthropic


def test_seed_produces_a_full_board(home: Path) -> None:
    seed()
    board = collect_board(DEMO_CONFIG)
    assert len(board["events"]) == 4
    assert (
        len(board["financial_items"]) == 3
    )  # two Northwind rows (a duplicate pair) plus utilities
    assert len(board["deadlines"]) == 1 and len(board["action_items"]) == 1
    assert len(board["tasks"]) == 2 and len(board["follow_ups"]) == 1
    assert db.get_status_summary("Sam")["money_owed_to_you"] == 4800


def test_run_demo_uses_a_throwaway_database(
    home: Path, claude: ClaudeClient, fake_sdk: FakeAnthropic, tmp_path: Path
) -> None:
    fake_sdk.messages.parse_handler = lambda schema, kw: schema(merges=[])
    fake_sdk.messages.text_handler = lambda kw: "<h2>Today</h2><ul><li>Demo</li></ul>"
    out = run_demo(lambda model: claude, out_dir=tmp_path / "docs")
    assert out == tmp_path / "docs" / "sample-digest.html"
    assert "Demo" in out.read_text(encoding="utf-8")
    assert [c["kind"] for c in fake_sdk.messages.calls] == [
        "parse",
        "create",
    ]  # reconcile, then write
    assert db.get_status_summary("Sam")["total_emails"] == 0  # the real database was not touched
