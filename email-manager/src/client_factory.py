"""
Client factory — builds the right EmailClient and CalendarClient for each
configured account based on its provider type.

Supports: gmail, outlook, yahoo, imap
Only creates clients for providers the user has configured.
"""
import os
import logging
from src.email_client import EmailClient, CalendarClientBase

log = logging.getLogger(__name__)


def build_clients(config: dict) -> list[EmailClient]:
    """Build one EmailClient per configured account, using the right provider."""
    clients = []
    creds_file = config.get("google", {}).get("credentials_file", "credentials/credentials.json")
    ms_cfg = config.get("microsoft", {})

    for account in config.get("accounts", []):
        provider = account.get("provider", "gmail")
        account_email = account["email"]

        try:
            if provider == "gmail":
                from src.providers.gmail import GmailProvider
                clients.append(GmailProvider(
                    credentials_file=creds_file,
                    token_file=account["token_file"],
                    account_email=account_email,
                ))

            elif provider == "outlook":
                from src.providers.outlook import OutlookClient
                client_id = ms_cfg.get("client_id") or os.environ.get("MS_CLIENT_ID", "")
                tenant_id = ms_cfg.get("tenant_id", "common")
                if not client_id:
                    log.warning(f"Skipping {account_email}: no microsoft.client_id configured")
                    continue
                clients.append(OutlookClient(
                    client_id=client_id,
                    tenant_id=tenant_id,
                    token_file=account.get("token_file", f"credentials/token_outlook_{account['name'].lower().replace(' ', '_')}.json"),
                    account_email=account_email,
                ))

            elif provider in ("yahoo", "imap", "aol", "icloud"):
                from src.providers.imap_client import ImapClient, PROVIDER_DEFAULTS
                defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["imap"])
                # Password from .env: IMAP_PASSWORD_<name> or YAHOO_PASSWORD_<name>
                env_key = f"{provider.upper()}_PASSWORD_{account['name'].upper().replace(' ', '_')}"
                password = os.environ.get(env_key, account.get("password", ""))
                if not password:
                    log.warning(f"Skipping {account_email}: no password found (set {env_key} in .env)")
                    continue
                clients.append(ImapClient(
                    account_email=account_email,
                    password=password,
                    imap_server=account.get("imap_server", defaults.get("imap_server", "")),
                    smtp_server=account.get("smtp_server", defaults.get("smtp_server", "")),
                    imap_port=account.get("imap_port", defaults["imap_port"]),
                    smtp_port=account.get("smtp_port", defaults["smtp_port"]),
                    trash_folder=account.get("trash_folder", defaults["trash_folder"]),
                    provider_name=provider,
                ))

            else:
                log.warning(f"Unknown provider '{provider}' for {account_email}, skipping")

        except Exception as e:
            log.error(f"Failed to create client for {account_email} ({provider}): {e}")

    return clients


def build_calendar_clients(config: dict, email_clients: list[EmailClient]) -> list:
    """Build calendar clients for providers that support calendars."""
    calendars = []
    for client in email_clients:
        if not client.supports_calendar:
            continue
        try:
            if client.provider == "gmail":
                from src.calendar_client import CalendarClient
                client.authenticate()
                calendars.append(CalendarClient(client))
            elif client.provider == "outlook":
                from src.providers.outlook import OutlookCalendarClient
                client.authenticate()
                calendars.append(OutlookCalendarClient(client))
        except Exception as e:
            log.error(f"Failed to create calendar client for {client.account_email}: {e}")
    return calendars


def get_send_client(config: dict, clients: list[EmailClient]) -> EmailClient:
    """Return the EmailClient that should send digest emails."""
    send_from = config.get("digest", {}).get("send_from_account", "Personal")
    accounts = config.get("accounts", [])
    for i, acct in enumerate(accounts):
        if acct["name"] == send_from and i < len(clients):
            return clients[i]
    return clients[0]
