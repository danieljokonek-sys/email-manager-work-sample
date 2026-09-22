"""Typed configuration loaded from ``config.yaml``.

Every knob the app reads is declared here with its default, so a missing key
never turns into a KeyError three stages into a scheduled run, and a wrong
type fails at start-up with a message that names the field. The setup wizard
writes the same keys (see :mod:`email_manager.config_writer`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from email_manager import paths
from email_manager.llm import DEFAULT_MODEL, Effort

Provider = Literal["gmail", "outlook", "yahoo", "imap", "aol", "icloud"]
Protection = Literal["vital", "standard", "none"]


class ConfigError(RuntimeError):
    """config.yaml is missing or invalid. The message is written for a person."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class OwnerConfig(_Model):
    name: str = ""
    phone: str = ""
    profile: str = Field(default="", description="Free text used to personalise the briefing.")
    briefing_priorities: str = ""

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else ""


class AccountConfig(_Model):
    name: str
    email: str
    provider: Provider = "gmail"
    token_file: str | None = None
    primary_entities: list[str] = Field(default_factory=list)
    # IMAP-family overrides; PROVIDER_DEFAULTS fills in the rest.
    imap_server: str | None = None
    smtp_server: str | None = None
    imap_port: int | None = None
    smtp_port: int | None = None
    trash_folder: str | None = None
    password: str | None = Field(
        default=None, description="Prefer the .env variable; see client_factory."
    )

    @property
    def slug(self) -> str:
        return self.name.lower().replace(" ", "_")


class DigestSchedule(_Model):
    daily_summary: str = "08:00"
    weekly_deep_dive: str = "monday 09:00"


class DigestConfig(_Model):
    send_to: str = ""
    send_from_account: str = ""
    days_ahead: int = 14
    effort: Effort = "medium"
    schedule: DigestSchedule = Field(default_factory=DigestSchedule)


class EntityConfig(_Model):
    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    accounts_receivable_from: list[str] = Field(default_factory=list)
    accounts_payable_to: list[str] = Field(default_factory=list)
    partner: str | None = None

    def matches_text(self, text: str) -> bool:
        lower = text.lower()
        return any(kw.lower() in lower for kw in self.keywords if kw)


class LabelConfig(_Model):
    name: str
    group: str = ""
    description: str = ""
    protection: Protection = "standard"
    auto_delete: bool = False


class GoogleConfig(_Model):
    credentials_file: str = "credentials/credentials.json"
    max_emails_per_fetch: int = 100
    lookback_days: int = 90
    calendar_days_ahead: int = 14


class MicrosoftConfig(_Model):
    client_id: str = ""
    tenant_id: str = "common"


class AnalysisConfig(_Model):
    model: str = DEFAULT_MODEL
    effort: Effort = "low"
    batch_size: int = 20


class CleanupConfig(_Model):
    model: str = DEFAULT_MODEL
    effort: Effort = "low"
    batch_size: int = 25
    delay_after_digest_minutes: int = 20
    accounts: list[str] = Field(default_factory=list, description="Account emails cleanup runs on.")


class FeaturesConfig(_Model):
    auto_label_emails: bool = True
    follow_up_detection: bool = True
    follow_up_days: int = 3
    reconcile_duplicates: bool = True
    excluded_calendars: list[str] = Field(
        default_factory=list,
        description="Calendar names to ignore (e.g. third-party calendars that auto-add events).",
    )


class Config(_Model):
    owner: OwnerConfig = Field(default_factory=OwnerConfig)
    accounts: list[AccountConfig] = Field(default_factory=list)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    entities: dict[str, EntityConfig] = Field(default_factory=dict)
    labels: list[LabelConfig] = Field(default_factory=list)
    google: GoogleConfig = Field(default_factory=GoogleConfig)
    microsoft: MicrosoftConfig = Field(default_factory=MicrosoftConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)

    @property
    def own_addresses(self) -> tuple[str, ...]:
        return tuple(a.email for a in self.accounts if a.email)

    def account_by_email(self, email: str) -> AccountConfig | None:
        return next((a for a in self.accounts if a.email == email), None)

    def resolve(self, relative: str) -> Path:
        """Resolve a config path (credentials file, token file) against the app directory."""
        p = Path(relative).expanduser()
        return p if p.is_absolute() else paths.app_dir() / p


def load_config(path: Path | None = None) -> Config:
    """Read and validate config.yaml. Raises :class:`ConfigError` with a readable message."""
    path = path or paths.config_path()
    if not path.exists():
        raise ConfigError(
            f"{path} not found. Run the setup wizard (run.bat / bash run.sh) or copy "
            "config.template.yaml to config.yaml and fill it in."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    try:
        return Config.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise ConfigError(f"{path} has invalid settings: {problems}") from e
