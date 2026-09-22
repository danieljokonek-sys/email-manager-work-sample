"""Alerting for the unattended run.

An account that cannot authenticate must never fail silently, because the
symptom is simply "no briefing arrived". Three channels are layered so at
least one lands even when every mailbox is down: an email through any account
that still works, a marker file the owner (and ``get-help``) can find, and a
desktop toast on Windows.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime

from email_manager import paths
from email_manager.config import Config
from email_manager.email_client import EmailClient

log = logging.getLogger(__name__)

MARKER_FILENAME = "AUTH_FAILURE.txt"
REAUTH_COMMAND = "python main.py setup-accounts"


def notify_auth_failure(
    config: Config, clients: Sequence[EmailClient], failed: Sequence[tuple[str, str]], total: int
) -> None:
    accounts = ", ".join(email for email, _ in failed)
    summary = (
        f"{len(failed)} of {total} email account(s) failed to authenticate and were skipped: "
        f"{accounts}.\n\nRe-authorize by running:\n  {REAUTH_COMMAND}\n"
    )
    log.error("%s", summary.replace("\n", " "))
    _email_alert(config, clients, accounts, len(failed))
    _write_marker(summary)
    if sys.platform == "win32":
        _windows_toast(len(failed))


def _email_alert(config: Config, clients: Sequence[EmailClient], accounts: str, count: int) -> None:
    send_to = config.digest.send_to
    if not send_to:
        return
    for client in clients:
        try:
            client.send_email(
                to=send_to,
                subject=f"[Email Manager] {count} account(s) need re-authorization",
                body_html=(
                    "<p>The daily email automation could not authenticate some accounts and skipped them. "
                    "The digest and cleanup ran for the rest.</p>"
                    f"<p><b>Failed:</b> {accounts}</p>"
                    f"<p>Fix it by running <code>{REAUTH_COMMAND}</code> in the email-manager folder.</p>"
                    "<p>Likely cause: the Google OAuth app's 7-day token expiry. Confirm the consent "
                    'screen is published "In production".</p>'
                ),
            )
            log.info("Auth-failure alert emailed to %s via %s", send_to, client.account_email)
            return
        except Exception:
            log.warning("Could not send alert via %s", client.account_email, exc_info=True)


def _write_marker(summary: str) -> None:
    try:
        marker = paths.logs_dir() / MARKER_FILENAME
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{datetime.now().isoformat()}\n\n{summary}", encoding="utf-8")
    except OSError:
        log.warning("Could not write %s", MARKER_FILENAME, exc_info=True)


def _windows_toast(count: int) -> None:
    body = f"{int(count)} account(s) failed. Run setup-accounts."
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$x=$t.GetElementsByTagName('text'); "
        "$x.Item(0).AppendChild($t.CreateTextNode('Email Manager: re-auth needed')) | Out-Null; "
        f"$x.Item(1).AppendChild($t.CreateTextNode('{body}')) | Out-Null; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Email Manager').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            timeout=15,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.warning("Could not show desktop toast", exc_info=True)
