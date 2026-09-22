"""Compatibility entry point.

``python main.py <command>`` behaves exactly like ``email-manager <command>``.
It exists so the double-click launchers (run.bat, run.sh, daily_run.bat) work
from a plain checkout without an editable install.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from email_manager.cli import cli

if __name__ == "__main__":
    cli()
