import pytest

from app.services.job_progress import persist_job_progress


class BrokenSession:
    async def __aenter__(self):
        raise RuntimeError("database temporarily unavailable")

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_progress_write_failure_does_not_abort_generation():
    await persist_job_progress(
        "job-1",
        stage="gen_submit",
        message="render accepted",
        session_factory=lambda: BrokenSession(),
    )
