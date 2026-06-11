"""
Backward-compatible re-export — all code now lives in src/providers/gmail.py
and src/client_factory.py. This file exists so existing imports still work.
"""
from src.providers.gmail import GmailProvider as GmailClient, GoogleCalendarClient
from src.client_factory import build_clients, get_send_client

__all__ = ["GmailClient", "GoogleCalendarClient", "build_clients", "get_send_client"]
