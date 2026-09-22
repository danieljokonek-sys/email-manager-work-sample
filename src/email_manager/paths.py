"""Where the app keeps its files.

Everything user-specific (config, credentials, databases, logs) lives under one
application directory so the tool is a portable folder. The directory is the
repository checkout by default and can be overridden with ``EMAIL_MANAGER_HOME``
for tests or for running the installed package from elsewhere. Nothing here is
resolved relative to the current working directory, so scheduled runs behave
the same no matter where the scheduler starts them.
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_VAR = "EMAIL_MANAGER_HOME"


def app_dir() -> Path:
    override = os.environ.get(_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    # src/email_manager/paths.py -> src/email_manager -> src -> repo root
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    return app_dir() / "config.yaml"


def data_dir() -> Path:
    return app_dir() / "data"


def logs_dir() -> Path:
    return app_dir() / "logs"


def credentials_dir() -> Path:
    return app_dir() / "credentials"


def tracker_db_path() -> Path:
    return data_dir() / "tracker.db"


def cleanup_db_path() -> Path:
    return data_dir() / "organized.db"
