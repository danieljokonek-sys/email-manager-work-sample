"""Shared CLI plumbing: console, config loading, error translation, Claude clients."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

import click
from rich.console import Console
from rich.table import Table

from email_manager import db
from email_manager.config import Config, ConfigError, load_config
from email_manager.llm import ClaudeClient, UsageTracker
from email_manager.logging_setup import setup_logging
from email_manager.pipeline import PipelineError

console = Console()
_tracker = UsageTracker()

P = ParamSpec("P")
R = TypeVar("R")


def setup(verbose: bool) -> None:
    setup_logging(verbose=verbose)


def tracker() -> UsageTracker:
    return _tracker


def config_and_db() -> Config:
    """Load config.yaml and make sure the database schema is current."""
    config = load_config()
    db.init_db()
    return config


def claude_for(model: str) -> ClaudeClient:
    return ClaudeClient(model=model, tracker=_tracker)


def friendly_errors(func: Callable[P, R]) -> Callable[P, R]:
    """Turn operator-facing errors into a clean message and exit code 1."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except (ConfigError, PipelineError) as e:
            raise click.ClickException(str(e)) from e

    return wrapper


def print_usage_summary() -> None:
    """One table of Claude usage for the calls made in this process."""
    rows = _tracker.by_tag()
    if not rows:
        return
    table = Table(title="Claude usage this run")
    table.add_column("Stage", style="cyan")
    table.add_column("Calls", justify="right")
    table.add_column("Input tok", justify="right")
    table.add_column("Cached", justify="right")
    table.add_column("Output tok", justify="right")
    table.add_column("Cost", justify="right", style="green")
    for tag, row in rows.items():
        table.add_row(
            tag,
            str(int(row["calls"])),
            f"{int(row['input_tokens']):,}",
            f"{int(row['cache_read_tokens']):,}",
            f"{int(row['output_tokens']):,}",
            f"${row['cost_usd']:.4f}",
        )
    table.add_section()
    table.add_row("total", "", "", "", "", f"${_tracker.total_cost_usd:.4f}")
    console.print(table)


def kv_table(title: str, rows: dict[str, Any]) -> Table:
    table = Table(title=title)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    for k, v in rows.items():
        table.add_row(k, str(v))
    return table
