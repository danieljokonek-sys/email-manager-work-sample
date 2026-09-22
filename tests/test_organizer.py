from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from email_manager.cleanup.organizer import (
    DEFAULT_LABELS,
    EmailOrganizer,
    LabelTaxonomy,
    build_classification_system_prompt,
    build_taxonomy,
    decide,
)
from email_manager.config import Config, LabelConfig
from email_manager.llm import ClaudeClient
from email_manager.schemas import Classification, ClassificationBatch, classification_schema
from tests.conftest import FakeAnthropic, FakeEmailClient, make_email


@pytest.fixture
def taxonomy() -> LabelTaxonomy:
    return build_taxonomy(None)


def test_default_taxonomy_shape(taxonomy: LabelTaxonomy) -> None:
    assert taxonomy.junk == ("Junk",)
    assert "Tax" in taxonomy.vital and "Personal" not in taxonomy.vital
    assert taxonomy.label_paths["Tax"] == "Finance/Tax"
    assert taxonomy.label_paths["Junk"] == "Junk"
    assert taxonomy.fallback == "Personal"
    assert len(taxonomy.all_categories) == len(DEFAULT_LABELS)


def test_custom_taxonomy_from_config() -> None:
    tax = build_taxonomy(
        [
            LabelConfig(name="Clients", group="Work", protection="vital"),
            LabelConfig(name="Promo", auto_delete=True),
        ]
    )
    assert tax.keep == ("Clients",) and tax.junk == ("Promo",)
    assert tax.fallback == "Clients"
    assert tax.colors["Work/Clients"]["backgroundColor"].startswith("#")


def test_classification_prompt_lists_every_category(taxonomy: LabelTaxonomy) -> None:
    prompt = build_classification_system_prompt("Sam", ["sam@example.com"], taxonomy)
    for cat in taxonomy.all_categories:
        assert f"- {cat}" in prompt
    assert "## Finance categories:" in prompt
    assert 'use "Personal" as the fallback' in prompt
    assert "{" not in prompt.replace("{}", "")  # no unfilled placeholders


def test_schema_enum_rejects_unknown_category() -> None:
    schema = classification_schema(["Tax", "Junk"])
    ok = schema.model_validate(
        {
            "classifications": [
                {
                    "email_id": "1",
                    "category": "Tax",
                    "action": "label",
                    "confidence": "high",
                    "reason": "r",
                }
            ]
        }
    )
    assert ok.classifications[0].category == "Tax"
    with pytest.raises(ValidationError):
        schema.model_validate(
            {
                "classifications": [
                    {
                        "email_id": "1",
                        "category": "Other",
                        "action": "label",
                        "confidence": "high",
                        "reason": "r",
                    }
                ]
            }
        )


def test_schema_without_categories_falls_back_to_plain_string() -> None:
    assert classification_schema([]) is ClassificationBatch


# ── the guards ──────────────────────────────────────────────────────────────


def _c(category: str, confidence: str = "high", action: str = "label") -> Classification:
    return Classification(
        email_id="m", category=category, action=action, confidence=confidence, reason="r"
    )  # type: ignore[arg-type]


OLD = "2024-01-01T00:00:00+00:00"
CUTOFF = datetime.now(UTC) - timedelta(days=365)


def test_junk_is_trashed(taxonomy: LabelTaxonomy) -> None:
    assert decide(make_email("m"), _c("Junk"), taxonomy, None) == "trash"


def test_low_confidence_junk_is_archived_not_trashed(taxonomy: LabelTaxonomy) -> None:
    assert decide(make_email("m"), _c("Junk", confidence="low"), taxonomy, None) == "archive"


def test_attachments_are_never_trashed(taxonomy: LabelTaxonomy) -> None:
    email = make_email("m", has_attachment=True, date=OLD)
    assert decide(email, _c("Junk"), taxonomy, None) == "archive"
    assert decide(email, _c("Travel"), taxonomy, CUTOFF) == "archive"


def test_age_based_trash_needs_an_explicit_cutoff(taxonomy: LabelTaxonomy) -> None:
    email = make_email("m", date=OLD)
    assert decide(email, _c("Travel", action="archive"), taxonomy, None) == "archive"
    assert decide(email, _c("Travel"), taxonomy, CUTOFF) == "trash"


def test_vital_categories_survive_age_based_cleanup(taxonomy: LabelTaxonomy) -> None:
    assert decide(make_email("m", date=OLD), _c("Tax"), taxonomy, CUTOFF) == "label"


def test_recent_mail_is_not_trashed_by_age(taxonomy: LabelTaxonomy) -> None:
    assert decide(make_email("m"), _c("Travel"), taxonomy, CUTOFF) == "label"


def test_unparseable_date_is_treated_as_not_old(taxonomy: LabelTaxonomy) -> None:
    assert decide(make_email("m", date="yesterday-ish"), _c("Travel"), taxonomy, CUTOFF) == "label"


# ── classify_and_act ────────────────────────────────────────────────────────


def _answer(schema: type, kw: dict) -> object:
    return schema.model_validate(
        {
            "classifications": [
                {
                    "email_id": "m1",
                    "category": "Junk",
                    "action": "label",
                    "confidence": "high",
                    "reason": "spam",
                },
                {
                    "email_id": "m2",
                    "category": "Travel",
                    "action": "archive",
                    "confidence": "high",
                    "reason": "flight",
                },
                {
                    "email_id": "m3",
                    "category": "Tax",
                    "action": "label",
                    "confidence": "high",
                    "reason": "w2",
                },
            ]
        }
    )


def test_classify_and_act_applies_actions_and_reports_omissions(
    config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic, mailbox: FakeEmailClient
) -> None:
    fake_sdk.messages.parse_handler = _answer
    organizer = EmailOrganizer(claude, config)
    emails = [make_email("m1"), make_email("m2"), make_email("m3"), make_email("m4-omitted")]
    stats, classifications = organizer.classify_and_act(emails, mailbox)
    assert stats.as_dict() == {"labeled": 1, "archived": 1, "trashed": 1, "skipped": 1, "errors": 0}
    assert mailbox.trashed == ["m1"]
    assert mailbox.modified == [("m2", ["id-1"], ["INBOX"])]
    assert mailbox.labeled == [("m3", "id-2")]
    assert mailbox.labels == {"Health & Travel/Travel": "id-1", "Finance/Tax": "id-2"}
    assert set(classifications) == {"m1", "m2", "m3"}  # the omitted email is not recorded


def test_dry_run_touches_nothing_but_explains(
    config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic, mailbox: FakeEmailClient
) -> None:
    fake_sdk.messages.parse_handler = _answer
    stats, _ = EmailOrganizer(claude, config).classify_and_act(
        [make_email("m1"), make_email("m2"), make_email("m3")], mailbox, dry_run=True
    )
    assert stats.trashed == 1 and stats.archived == 1 and stats.labeled == 1
    assert mailbox.trashed == [] and mailbox.modified == [] and mailbox.labeled == []
    assert any("TRASH" in line and "Junk" in line for line in stats.decisions)


def test_mailbox_errors_are_counted_not_raised(
    config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic, mailbox: FakeEmailClient
) -> None:
    fake_sdk.messages.parse_handler = _answer

    def boom(message_id: str) -> None:
        raise RuntimeError("api down")

    mailbox.trash_email = boom  # type: ignore[method-assign]
    stats, _ = EmailOrganizer(claude, config).classify_and_act([make_email("m1")], mailbox)
    assert stats.errors == 1 and stats.trashed == 0


def test_prompt_uses_configured_effort_and_schema(
    config: Config, claude: ClaudeClient, fake_sdk: FakeAnthropic, mailbox: FakeEmailClient
) -> None:
    fake_sdk.messages.parse_handler = lambda schema, kw: schema(classifications=[])
    EmailOrganizer(claude, config).classify_batch([make_email("m1")])
    call = fake_sdk.messages.calls[0]
    assert call["output_config"] == {"effort": "low"}
    assert call["output_format"].__name__ == "ConfiguredClassificationBatch"
    assert "m1" in call["messages"][0]["content"]
