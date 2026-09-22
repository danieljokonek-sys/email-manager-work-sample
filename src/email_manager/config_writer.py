"""Writes config.yaml, .env, and the OS scheduler definitions for the setup wizard.

Pulled out of the tkinter code so it can be tested without a window: the
wizard collects plain values into :class:`WizardData` and everything here is
a pure function of that. YAML goes through ``yaml.safe_dump`` so a quote or a
colon in an owner's profile cannot corrupt the file.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import yaml

from email_manager.llm import DEFAULT_MODEL

IMAP_FAMILY = ("yahoo", "imap", "aol", "icloud")


@dataclass
class WizardAccount:
    name: str
    email: str
    provider: str = "gmail"
    imap_server: str | None = None
    smtp_server: str | None = None
    password: str | None = None

    @property
    def slug(self) -> str:
        return self.name.lower().replace(" ", "_")


@dataclass
class WizardEntity:
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class WizardData:
    owner_name: str = ""
    owner_phone: str = ""
    owner_profile: str = ""
    briefing_priorities: str = ""
    api_key: str = ""
    ms_client_id: str = ""
    accounts: list[WizardAccount] = field(default_factory=list)
    entities: list[WizardEntity] = field(default_factory=list)
    labels: list[dict[str, Any]] = field(default_factory=list)
    digest_email: str = ""
    digest_time: str = "08:00"
    digest_account: str = ""


def entity_key(name: str) -> str:
    """'My Business' -> 'my_business'."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    return re.sub(r"[^a-z0-9_]", "", key)


def build_config(data: WizardData) -> dict[str, Any]:
    """The config.yaml structure as a plain dict, in the order the file is written."""
    accounts = []
    for acct in data.accounts:
        entry: dict[str, Any] = {
            "name": acct.name,
            "provider": acct.provider,
            "email": acct.email,
            "token_file": f"credentials/token_{acct.slug}.json",
            "primary_entities": ["personal"],
        }
        if acct.provider in IMAP_FAMILY:
            if acct.imap_server:
                entry["imap_server"] = acct.imap_server
            if acct.smtp_server:
                entry["smtp_server"] = acct.smtp_server
        accounts.append(entry)

    entities = {
        entity_key(e.name): {
            "name": e.name,
            "description": e.description,
            "keywords": list(e.keywords),
            "accounts_receivable_from": [],
            "accounts_payable_to": [],
        }
        for e in data.entities
        if e.name.strip()
    }

    labels = []
    for lbl in data.labels:
        item: dict[str, Any] = {
            "name": lbl["name"],
            "group": lbl.get("group", ""),
            "description": lbl.get("description", ""),
            "protection": lbl.get("protection", "standard"),
        }
        if lbl.get("auto_delete"):
            item["auto_delete"] = True
        labels.append(item)

    config: dict[str, Any] = {
        "owner": {
            "name": data.owner_name,
            "phone": data.owner_phone,
            "profile": data.owner_profile,
            "briefing_priorities": data.briefing_priorities,
        },
        "accounts": accounts,
        "digest": {
            "send_to": data.digest_email,
            "send_from_account": data.digest_account or (accounts[0]["name"] if accounts else ""),
            "days_ahead": 14,
            "effort": "medium",
            "schedule": {
                "daily_summary": data.digest_time or "08:00",
                "weekly_deep_dive": "monday 09:00",
            },
        },
        "entities": entities,
        "labels": labels,
    }
    if any(a["provider"] == "gmail" for a in accounts):
        config["google"] = {
            "credentials_file": "credentials/credentials.json",
            "max_emails_per_fetch": 100,
            "lookback_days": 90,
            "calendar_days_ahead": 14,
        }
    if any(a["provider"] == "outlook" for a in accounts) and data.ms_client_id:
        config["microsoft"] = {"client_id": data.ms_client_id, "tenant_id": "common"}
    config["analysis"] = {"model": DEFAULT_MODEL, "effort": "low", "batch_size": 20}
    config["cleanup"] = {
        "model": DEFAULT_MODEL,
        "effort": "low",
        "batch_size": 25,
        "delay_after_digest_minutes": 20,
        "accounts": [a["email"] for a in accounts[:1]],
    }
    config["features"] = {
        "auto_label_emails": True,
        "follow_up_detection": True,
        "follow_up_days": 3,
        "reconcile_duplicates": True,
        "excluded_calendars": [],
    }
    return config


class _LiteralDumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LiteralDumper.add_representer(str, _str_presenter)


def render_config_yaml(data: WizardData) -> str:
    body = yaml.dump(
        build_config(data),
        Dumper=_LiteralDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return (
        "# Email Manager configuration\n# Generated by the setup wizard. Safe to edit by hand.\n\n"
        + body
    )


def _env_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_env(data: WizardData) -> str:
    """The .env file. Values are quoted so a password with quotes or backslashes survives."""
    lines = [f"ANTHROPIC_API_KEY={_env_quote(data.api_key.strip())}"]
    if data.ms_client_id.strip():
        lines.append(f"MS_CLIENT_ID={_env_quote(data.ms_client_id.strip())}")
    for acct in data.accounts:
        if acct.provider in IMAP_FAMILY and acct.password:
            name = acct.name.strip().upper().replace(" ", "_") or "ACCOUNT"
            lines.append(f"{acct.provider.upper()}_PASSWORD_{name}={_env_quote(acct.password)}")
    return "\n".join(lines) + "\n"


def write_config_files(app_dir: Path, data: WizardData) -> tuple[Path, Path]:
    """Write config.yaml and .env under ``app_dir``. Returns their paths."""
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "credentials").mkdir(exist_ok=True)
    config_path = app_dir / "config.yaml"
    env_path = app_dir / ".env"
    config_path.write_text(render_config_yaml(data), encoding="utf-8")
    env_path.write_text(render_env(data), encoding="utf-8")
    with contextlib.suppress(OSError):
        env_path.chmod(0o600)
    return config_path, env_path


# ── OS schedulers ───────────────────────────────────────────────────────────


def _ps_quote(value: str) -> str:
    """Single-quoted PowerShell literal; a quote inside is doubled."""
    return "'" + value.replace("'", "''") + "'"


def windows_task_script(app_dir: Path, time_str: str, task_name: str = "EmailManager") -> str:
    """PowerShell that registers the daily task with the settings that matter for an unattended PC.

    StartWhenAvailable catches runs missed while the PC was off; the battery
    flags keep laptops working; the two-hour limit kills a hung run before it
    blocks tomorrow's; IgnoreNew stops a second trigger from starting a
    duplicate while one is running.
    """
    bat = app_dir / "daily_run.bat"
    return "\n".join(
        [
            f"$action = New-ScheduledTaskAction -Execute {_ps_quote(str(bat))} -WorkingDirectory {_ps_quote(str(app_dir))}",
            f"$t1 = New-ScheduledTaskTrigger -Daily -At {_ps_quote(time_str)}",
            "$t2 = New-ScheduledTaskTrigger -AtLogOn",
            "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries "
            "-AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew",
            f"Register-ScheduledTask -TaskName {_ps_quote(task_name)} -Action $action -Trigger $t1,$t2 "
            "-Settings $settings -Description 'Email Manager daily run. Logs to logs/daily_run.log' -Force",
            "",
        ]
    )


def launchd_plist(app_dir: Path, python_bin: str, hour: int, minute: int) -> str:
    """A LaunchAgent that runs ``main.py run`` daily and catches up after sleep."""
    main_py = app_dir / "main.py"
    logs = app_dir / "logs"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.emailmanager.daily</string>
    <key>ProgramArguments</key>
    <array>
        <string>{xml_escape(python_bin)}</string>
        <string>{xml_escape(main_py.as_posix())}</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key><string>{xml_escape(app_dir.as_posix())}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{int(hour)}</integer>
        <key>Minute</key><integer>{int(minute)}</integer>
    </dict>
    <key>StandardOutPath</key><string>{xml_escape((logs / "run.log").as_posix())}</string>
    <key>StandardErrorPath</key><string>{xml_escape((logs / "run_error.log").as_posix())}</string>
</dict>
</plist>
"""
