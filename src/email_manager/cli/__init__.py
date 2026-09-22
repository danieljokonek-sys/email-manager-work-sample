"""Click command tree. One module per command group; shared helpers in :mod:`common`."""

from __future__ import annotations

import click

from email_manager.cli import briefing, cleanup, followups, tasks, views
from email_manager.cli.common import setup


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-v", "--verbose", is_flag=True, help="Debug logging on the console.")
@click.version_option(package_name="email-manager")
def cli(verbose: bool) -> None:
    """Email Manager: a daily AI briefing and inbox cleanup across your mailboxes."""
    setup(verbose)


for command in (
    briefing.setup_cmd,
    briefing.setup_accounts,
    briefing.fetch,
    briefing.analyze,
    briefing.digest,
    briefing.run,
    briefing.demo,
    views.status,
    views.horizon,
    views.usage,
    tasks.tasks,
    followups.followups,
    cleanup.cleanup,
):
    cli.add_command(command)


def main() -> None:
    cli()
