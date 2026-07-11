"""Polling helpers for long-running jobs."""

import time
from collections.abc import Callable
from typing import Any

from rich.console import Console

from iris_cli.client import IrisClient

console = Console()


def poll_job(
    client: IrisClient,
    job_id: str,
    interval: float = 2.0,
    on_update: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Poll GET /api/jobs/{id} until status is 'done' or 'error'."""
    while True:
        data = client.get_job(job_id)
        status = data.get("status", "unknown")

        if on_update:
            on_update(data)
        else:
            console.print(f"  [dim]job {job_id[:8]}... status=[/dim]{status}")

        if status in ("done", "error"):
            return data

        time.sleep(interval)


def poll_export(
    client: IrisClient,
    export_job_id: str,
    interval: float = 2.0,
    on_update: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Poll GET /api/export/{id} until status is 'done' or 'error'."""
    while True:
        data = client.get_export(export_job_id)
        status = data.get("status", "unknown")

        if on_update:
            on_update(data)
        else:
            console.print(f"  [dim]export {export_job_id[:8]}... status=[/dim]{status}")

        if status in ("done", "error"):
            return data

        time.sleep(interval)


def poll_propagation(
    client: IrisClient,
    prop_job_id: str,
    interval: float = 2.0,
    on_update: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Poll GET /api/propagate/{id} until status is 'done' or 'error'."""
    while True:
        data = client.get_propagation(prop_job_id)
        status = data.get("status", "unknown")

        if on_update:
            on_update(data)
        else:
            console.print(f"  [dim]propagation {prop_job_id[:8]}... status=[/dim]{status}")

        if status in ("done", "error"):
            return data

        time.sleep(interval)
