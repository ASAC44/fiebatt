"""Output formatting utilities."""

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()

# Global output format — set by --json / --human flags in main.py
FORMAT: str = "human"


def print_json(data: Any) -> None:
    """Dump data as pretty-printed JSON to stdout."""
    sys.stdout.write(json.dumps(data, indent=2, default=str))
    sys.stdout.write("\n")


def print_table(data: list[dict[str, Any]], columns: list[str]) -> None:
    """Render a list of dicts as a rich Table."""
    if FORMAT == "json":
        print_json(data)
        return

    table = Table()
    for col in columns:
        table.add_column(col)

    for row in data:
        table.add_row(*[str(row.get(col, "")) for col in columns])

    console.print(table)


def print_status(label: str, value: str) -> None:
    """Print a formatted key-value pair."""
    if FORMAT == "json":
        print_json({label: value})
        return

    console.print(f"[bold]{label}:[/bold] {value}")


def print_result(data: Any) -> None:
    """Print a dict result — dispatches based on FORMAT."""
    if FORMAT == "json":
        print_json(data)
    else:
        if isinstance(data, dict):
            for key, value in data.items():
                console.print(f"[bold]{key}:[/bold] {value}")
        else:
            console.print(data)
