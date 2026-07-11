"""iris job — inspect a job."""

import typer

from iris_cli.client import IrisClient
from iris_cli.config import get_client_kwargs
from iris_cli.output import print_result


def _client() -> IrisClient:
    return IrisClient(**get_client_kwargs())


def get_job(
    job_id: str = typer.Argument(..., help="Job ID"),
) -> None:
    """Show job status and variants."""
    data = _client().get_job(job_id)
    print_result(data)
