"""Logging for interactive and scheduled runs.

Progress goes to stderr as plain text (no ANSI codes, so a redirected log stays
readable) and to a rotating file under ``logs/``. Every Claude call also lands
in that file via the ``claude-usage`` logger, so one file answers both "did it
run" and "what did it cost".
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from email_manager import paths

LOG_FILENAME = "email_manager.log"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_configured = False


def setup_logging(verbose: bool = False, to_file: bool = True) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.addHandler(console)

    if to_file:
        try:
            paths.logs_dir().mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                paths.logs_dir() / LOG_FILENAME, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter(_FORMAT))
            handler.setLevel(logging.INFO)
            root.addHandler(handler)
        except OSError:
            root.warning("Could not open log file; logging to console only", exc_info=True)

    # Third-party chatter that drowns the useful lines.
    for noisy in ("googleapiclient.discovery_cache", "httpx", "httpcore", "msal", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
