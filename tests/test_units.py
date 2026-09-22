"""Small pure functions: digest helpers, horizon correlation, prompts, client factory, writers."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from email_manager import prompts
from email_manager.briefing.digest import is_own_briefing, localize_times, strip_fences
from email_manager.client_factory import build_clients, get_send_client
from email_manager.config import Config, load_config
from email_manager.config_writer import (
    WizardAccount,
    WizardData,
    WizardEntity,
    build_config,
    entity_key,
    launchd_plist,
    render_config_yaml,
    render_env,
    windows_task_script,
    write_config_files,
)
from email_manager.email_client import EmailFilter, parse_address_list
from email_manager.horizon import items_for_event
from email_manager.providers.gmail import _filter_query
from email_manager.providers.imap_client import _imap_quoted, _search_criteria
from email_manager.providers.outlook import _filter_clauses, _odata_literal
from tests.conftest import FakeEmailClient

# ── digest helpers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Daily Briefing: Sep 22, 2026", True),
        ("Re: Fwd: daily briefing: yesterday", True),
        ("FW: Weekly Briefing", True),
        ("Reserve the room", False),  # would have broken the old lstrip() code
        ("", False),
        (None, False),
    ],
)
def test_is_own_briefing(subject: str | None, expected: bool) -> None:
    assert is_own_briefing(subject) is expected


def test_localize_times_only_touches_datetime_keys() -> None:
    out = localize_times(
        {"events": [{"start_datetime": "2026-09-17T14:30:00", "title": "2026-09-17T14:30:00"}]}
    )
    assert out["events"][0]["start_datetime"] == "Sep 17, 2026 at 2:30 PM"
    assert out["events"][0]["title"] == "2026-09-17T14:30:00"


def test_strip_fences() -> None:
    assert strip_fences("```html\n<p>x</p>\n```") == "<p>x</p>"
    assert strip_fences("<p>x</p>") == "<p>x</p>"


# ── horizon ─────────────────────────────────────────────────────────────────


def test_items_for_event_by_entity_and_by_date() -> None:
    event = {"entity_key": "studio", "start_date": "2026-10-10"}
    data = {
        "deadlines": [
            {"id": 1, "entity_key": "rental", "due_date": "2026-10-12"},
            {"id": 2, "entity_key": "rental", "due_date": "2026-11-01"},
        ],
        "action_items": [{"id": 3, "entity_key": "studio", "due_date": None}],
        "financial_items": [{"id": 4, "entity_key": "rental"}],
        "tasks": [{"id": 5, "entity_key": "rental", "due_date": "2026-10-16"}],
        "follow_ups": [{"id": 6, "entity_key": "studio"}],
    }
    related = items_for_event(event, data)
    assert [d["id"] for d in related["deadlines"]] == [1]  # within 5 days
    assert [a["id"] for a in related["actions"]] == [3]  # same entity
    assert related["financial"] == []  # money only matches by entity
    assert [t["id"] for t in related["tasks"]] == [5]  # within 7 days
    assert [f["id"] for f in related["follow_ups"]] == [6]


# ── prompts ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "placeholders"),
    [
        ("extraction_system", {"entities_description": "e", "today": "t"}),
        ("extraction_user", {"entity_keys": "k", "emails_json": "[]"}),
        (
            "bulletin_system",
            {"owner": "o", "profile": "", "priorities": "", "entities": "e", "horizon_days": 14},
        ),
        ("reconcile_system", {}),
        ("reconcile_user", {"data_json": "{}"}),
        (
            "classification_system",
            {
                "owner_name": "o",
                "account_emails": "a",
                "categories_text": "c",
                "fallback": "f",
                "today": "t",
            },
        ),
        ("classification_user", {"categories": "c", "emails_json": "[]"}),
    ],
)
def test_prompt_templates_format_cleanly(name: str, placeholders: dict) -> None:
    text = prompts.load(name)
    assert not text.startswith("<!--")
    rendered = text.format(**placeholders)  # raises KeyError if a placeholder is missing
    assert rendered.strip()


# ── provider filters ────────────────────────────────────────────────────────


def test_filter_translations_per_provider() -> None:
    f = EmailFilter(after=datetime(2026, 1, 5), before=datetime(2026, 2, 1))
    assert _filter_query(f) == "after:2026/01/05 before:2026/02/01"
    # Naive datetimes are local time; Graph wants UTC.
    ge = f.after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # type: ignore[union-attr]
    lt = f.before.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # type: ignore[union-attr]
    assert _filter_clauses(f) == [f"receivedDateTime ge {ge}", f"receivedDateTime lt {lt}"]
    assert _search_criteria(email_filter=f) == ["(SINCE 05-Jan-2026 BEFORE 01-Feb-2026)"]
    assert _filter_query(EmailFilter()) == "" and _search_criteria() == ["ALL"]


def test_query_escaping() -> None:
    assert _odata_literal("O'Brien") == "O''Brien"
    assert _imap_quoted('<a"b\\c>\r\n') == '"<a\\"b\\\\c>"'


def test_parse_address_list() -> None:
    assert parse_address_list("a@x.com, B <b@y.com>,, ") == ["a@x.com", "B <b@y.com>"]
    assert parse_address_list(None) == []


# ── client factory ──────────────────────────────────────────────────────────


def test_get_send_client_matches_by_email_not_position(config: Config) -> None:
    personal = FakeEmailClient("sam.home@example.com")
    assert get_send_client(config, [personal]) is personal
    other = FakeEmailClient("other@example.com")
    assert get_send_client(config, [other]) is other  # fallback to first available
    with pytest.raises(RuntimeError):
        get_send_client(config, [])


def test_build_clients_skips_accounts_it_cannot_build(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    monkeypatch.delenv("MS_CLIENT_ID", raising=False)
    monkeypatch.delenv("YAHOO_PASSWORD_MAIL", raising=False)
    cfg = Config.model_validate(
        {
            "accounts": [
                {"name": "Work", "email": "w@example.com", "provider": "gmail"},
                {"name": "Old", "email": "o@outlook.com", "provider": "outlook"},  # no client id
                {"name": "Mail", "email": "y@yahoo.com", "provider": "yahoo"},  # no password
            ]
        }
    )
    clients = build_clients(cfg)
    assert [c.account_email for c in clients] == ["w@example.com"]
    assert clients[0].token_file == str(home / "credentials" / "token_work.json")  # type: ignore[attr-defined]


# ── config writer ───────────────────────────────────────────────────────────


def _wizard_data() -> WizardData:
    return WizardData(
        owner_name='Sam "Q" Rivera',
        owner_profile="Line one: with a colon\nLine two",
        api_key='sk-ant-abc"def',
        accounts=[
            WizardAccount(name="Studio Mail", email="sam@example.com"),
            WizardAccount(name="Yahoo", email="s@yahoo.com", provider="yahoo", password='p"w\\d'),
        ],
        entities=[
            WizardEntity(name="Rivera Studio", description="Design", keywords=["logo", "brand"])
        ],
        labels=[
            {"name": "Tax", "group": "Finance", "protection": "vital"},
            {"name": "Junk", "auto_delete": True},
        ],
        digest_email="sam@example.com",
        digest_time="07:30",
    )


def test_entity_key_normalises_names() -> None:
    assert entity_key("My Business!") == "my_business"
    assert entity_key("Elm-Street Rental") == "elm_street_rental"


def test_config_yaml_round_trips_awkward_strings_and_validates(tmp_path: Path) -> None:
    data = _wizard_data()
    text = render_config_yaml(data)
    loaded = yaml.safe_load(text)
    assert loaded["owner"]["name"] == 'Sam "Q" Rivera'
    assert loaded["owner"]["profile"] == "Line one: with a colon\nLine two"
    assert loaded["entities"]["rivera_studio"]["keywords"] == ["logo", "brand"]
    assert loaded["accounts"][0]["token_file"] == "credentials/token_studio_mail.json"
    assert loaded["cleanup"]["accounts"] == ["sam@example.com"]
    assert loaded["digest"]["schedule"]["daily_summary"] == "07:30"
    (tmp_path / "config.yaml").write_text(text, encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")  # the writer and the model agree on keys
    assert cfg.labels[1].auto_delete is True and cfg.accounts[1].provider == "yahoo"


def test_env_quoting_survives_dotenv(tmp_path: Path) -> None:
    from dotenv import dotenv_values

    env_path = tmp_path / ".env"
    env_path.write_text(render_env(_wizard_data()), encoding="utf-8")
    values = dotenv_values(env_path)
    assert values["ANTHROPIC_API_KEY"] == 'sk-ant-abc"def'
    assert values["YAHOO_PASSWORD_YAHOO"] == 'p"w\\d'
    assert "MS_CLIENT_ID" not in values


def test_write_config_files_creates_both(tmp_path: Path) -> None:
    config_path, env_path = write_config_files(tmp_path / "app", _wizard_data())
    assert (
        config_path.exists() and env_path.exists() and (tmp_path / "app" / "credentials").is_dir()
    )
    assert build_config(_wizard_data())["features"]["reconcile_duplicates"] is True


def test_scheduler_definitions_escape_paths() -> None:
    script = windows_task_script(Path("C:/Users/O'Neil/Email Manager"), "08:00")
    assert "-Execute 'C:\\Users\\O''Neil\\Email Manager\\daily_run.bat'" in script.replace(
        "/", "\\"
    )
    assert "MultipleInstances IgnoreNew" in script and "-At '08:00'" in script
    plist = launchd_plist(Path("/Users/sam/Email & Stuff"), "/usr/bin/python3", 8, 30)
    assert "<string>/Users/sam/Email &amp; Stuff</string>" in plist
    assert "<key>Hour</key><integer>8</integer>" in plist
