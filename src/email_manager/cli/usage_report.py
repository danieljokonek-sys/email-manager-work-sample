"""Summarise Claude spend from the log file, per day and per stage."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from email_manager import paths
from email_manager.logging_setup import LOG_FILENAME

# 2026-09-22 08:01:12,345 [INFO] claude-usage: extract: claude-sonnet-5 in=5210 out=880 cache_read=0 cache_write=1200 stop=end_turn cost=$0.0189
_LINE = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2}) .*?claude-usage: (?P<tag>[\w-]+): (?P<model>\S+) "
    r"in=(?P<inp>\d+) out=(?P<out>\d+) cache_read=(?P<cr>\d+) cache_write=(?P<cw>\d+) "
    r"stop=(?P<stop>\S+) cost=\$(?P<cost>[\d.]+)"
)


def parse_usage_log(path: Path) -> dict[str, dict[str, dict[str, float]]]:
    """{day: {tag: {calls, input, output, cache_read, cost}}} from the log file."""
    days: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    if not path.exists():
        return {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        row = days[m["day"]][m["tag"]]
        row["calls"] += 1
        row["input"] += int(m["inp"])
        row["output"] += int(m["out"])
        row["cache_read"] += int(m["cr"])
        row["cost"] += float(m["cost"])
    return {d: {t: dict(r) for t, r in tags.items()} for d, tags in days.items()}


def print_usage_report(console: Console, days: int = 14) -> None:
    data = parse_usage_log(paths.logs_dir() / LOG_FILENAME)
    if not data:
        console.print("[dim]No Claude usage recorded yet.[/dim]")
        return
    table = Table(title=f"Claude usage, last {days} days with activity")
    table.add_column("Day")
    table.add_column("Stage", style="cyan")
    table.add_column("Calls", justify="right")
    table.add_column("Input", justify="right")
    table.add_column("Cached", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Cost", justify="right", style="green")
    total = 0.0
    for day in sorted(data)[-days:]:
        day_total = 0.0
        for tag, row in sorted(data[day].items()):
            table.add_row(
                day,
                tag,
                str(int(row["calls"])),
                f"{int(row['input']):,}",
                f"{int(row['cache_read']):,}",
                f"{int(row['output']):,}",
                f"${row['cost']:.4f}",
            )
            day_total += row["cost"]
        table.add_row("", "[dim]day total[/dim]", "", "", "", "", f"[dim]${day_total:.4f}[/dim]")
        total += day_total
    table.add_section()
    table.add_row("", "[bold]total[/bold]", "", "", "", "", f"[bold]${total:.4f}[/bold]")
    console.print(table)
