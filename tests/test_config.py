from pathlib import Path

import pytest

from email_manager.config import Config, ConfigError, load_config


def test_defaults_fill_in_everything(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("owner:\n  name: Sam Rivera\n", encoding="utf-8")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.owner.first_name == "Sam"
    assert cfg.digest.days_ahead == 14
    assert cfg.analysis.effort == "low"
    assert cfg.digest.effort == "medium"
    assert cfg.features.reconcile_duplicates is True
    assert cfg.accounts == []


def test_missing_file_has_a_readable_message(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_field_names_the_field(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("digest:\n  days_ahead: soon\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"digest\.days_ahead"):
        load_config(tmp_path / "config.yaml")


def test_invalid_yaml_is_reported(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("owner: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(tmp_path / "config.yaml")


def test_unknown_keys_are_ignored_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("sms:\n  enabled: false\nowner: {}\n", encoding="utf-8")
    assert load_config(tmp_path / "config.yaml").owner.name == ""


def test_effort_is_validated(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("analysis:\n  effort: turbo\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"analysis\.effort"):
        load_config(tmp_path / "config.yaml")


def test_own_addresses_and_lookup(config: Config) -> None:
    assert config.own_addresses == ("sam@example.com", "sam.home@example.com")
    assert config.account_by_email("sam.home@example.com").name == "Personal"  # type: ignore[union-attr]
    assert config.account_by_email("nobody@example.com") is None


def test_entity_keyword_match(config: Config) -> None:
    assert config.entities["rental"].matches_text("Your TENANT called about rent")
    assert not config.entities["rental"].matches_text("Logo revisions")


def test_paths_resolve_against_app_dir(config: Config, home: Path) -> None:
    assert config.resolve("credentials/x.json") == home / "credentials" / "x.json"
    absolute = home / "elsewhere.json"
    assert config.resolve(str(absolute)) == absolute
