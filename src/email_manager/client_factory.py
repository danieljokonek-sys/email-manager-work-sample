"""Builds the right EmailClient and calendar client for each configured account."""

from __future__ import annotations

import logging
import os

from email_manager.config import AccountConfig, Config
from email_manager.email_client import CalendarClientBase, EmailClient

log = logging.getLogger(__name__)

IMAP_FAMILY = ("yahoo", "imap", "aol", "icloud")


def _gmail(config: Config, account: AccountConfig) -> EmailClient:
    from email_manager.providers.gmail import GmailClient

    token_file = account.token_file or f"credentials/token_{account.slug}.json"
    return GmailClient(
        credentials_file=str(config.resolve(config.google.credentials_file)),
        token_file=str(config.resolve(token_file)),
        account_email=account.email,
    )


def _outlook(config: Config, account: AccountConfig) -> EmailClient | None:
    from email_manager.providers.outlook import OutlookClient

    client_id = config.microsoft.client_id or os.environ.get("MS_CLIENT_ID", "")
    if not client_id:
        log.warning("Skipping %s: no microsoft.client_id configured", account.email)
        return None
    token_file = account.token_file or f"credentials/token_outlook_{account.slug}.json"
    return OutlookClient(
        client_id=client_id,
        tenant_id=config.microsoft.tenant_id,
        token_file=str(config.resolve(token_file)),
        account_email=account.email,
    )


def _imap(account: AccountConfig) -> EmailClient | None:
    from email_manager.providers.imap_client import PROVIDER_DEFAULTS, ImapClient

    defaults = PROVIDER_DEFAULTS.get(account.provider, PROVIDER_DEFAULTS["imap"])
    # Passwords come from .env as <PROVIDER>_PASSWORD_<ACCOUNT_NAME>; the
    # config.yaml `password` field is a fallback for people who prefer it.
    env_key = f"{account.provider.upper()}_PASSWORD_{account.name.upper().replace(' ', '_')}"
    password = os.environ.get(env_key) or account.password or ""
    if not password:
        log.warning("Skipping %s: no password found (set %s in .env)", account.email, env_key)
        return None
    return ImapClient(
        account_email=account.email,
        password=password,
        imap_server=account.imap_server or defaults.get("imap_server", ""),
        smtp_server=account.smtp_server or defaults.get("smtp_server", ""),
        imap_port=account.imap_port or defaults["imap_port"],
        smtp_port=account.smtp_port or defaults["smtp_port"],
        trash_folder=account.trash_folder or defaults["trash_folder"],
        provider_name=account.provider,
    )


def build_clients(config: Config) -> list[EmailClient]:
    """One EmailClient per configured account. Accounts that cannot be built are skipped with a warning."""
    clients: list[EmailClient] = []
    for account in config.accounts:
        try:
            if account.provider == "gmail":
                client: EmailClient | None = _gmail(config, account)
            elif account.provider == "outlook":
                client = _outlook(config, account)
            elif account.provider in IMAP_FAMILY:
                client = _imap(account)
            else:
                log.warning("Unknown provider %r for %s, skipping", account.provider, account.email)
                client = None
        except Exception:
            log.error(
                "Failed to create client for %s (%s)",
                account.email,
                account.provider,
                exc_info=True,
            )
            client = None
        if client is not None:
            clients.append(client)
    return clients


def build_calendar_clients(
    config: Config, email_clients: list[EmailClient]
) -> list[CalendarClientBase]:
    """Calendar clients for the accounts whose provider has a calendar."""
    calendars: list[CalendarClientBase] = []
    excluded = tuple(config.features.excluded_calendars)
    for client in email_clients:
        if not client.supports_calendar:
            continue
        if client.provider == "gmail":
            from email_manager.providers.gmail import GmailClient, GoogleCalendarClient

            assert isinstance(client, GmailClient)
            calendars.append(GoogleCalendarClient(client, excluded_calendars=excluded))
        elif client.provider == "outlook":
            from email_manager.providers.outlook import OutlookCalendarClient, OutlookClient

            assert isinstance(client, OutlookClient)
            calendars.append(OutlookCalendarClient(client))
    return calendars


def get_send_client(config: Config, clients: list[EmailClient]) -> EmailClient:
    """The client that sends the digest.

    Matches by account email rather than list position, so it stays correct
    when ``clients`` has been filtered to the accounts that authenticated.
    Falls back to the first available client.
    """
    if not clients:
        raise RuntimeError("No authenticated accounts available to send the digest.")
    wanted = config.digest.send_from_account
    account = next((a for a in config.accounts if a.name == wanted), None)
    if account is not None:
        for client in clients:
            if client.account_email == account.email:
                return client
    return clients[0]


def find_client(clients: list[EmailClient], account_email: str) -> EmailClient | None:
    return next((c for c in clients if c.account_email == account_email), None)
