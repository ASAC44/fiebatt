"""fiebatt job — inspect a job."""

import typer

from fiebatt_cli.client import FiebattClient
from fiebatt_cli.config import get_client_kwargs
from fiebatt_cli.output import print_result


def _client() -> FiebattClient:
    return FiebattClient(**get_client_kwargs())


def get_job(
    job_id: str = typer.Argument(..., help="Job ID"),
) -> None:
    """Show job status and variants."""
    data = _client().get_job(job_id)
    print_result(data)
