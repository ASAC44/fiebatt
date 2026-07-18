import asyncio

import pytest

from app.workers import generate_job


@pytest.mark.asyncio
async def test_unexpected_worker_error_becomes_terminal_failure(monkeypatch):
    failures = []

    async def crash(_job_id):
        raise RuntimeError("unexpected internal detail")

    async def fail(job_id, error):
        failures.append((job_id, error))

    monkeypatch.setattr(generate_job, "_run", crash)
    monkeypatch.setattr(generate_job, "_fail_job", fail)

    await generate_job.run("job-1")

    assert failures == [("job-1", "unexpected internal detail")]


@pytest.mark.asyncio
async def test_worker_cancellation_is_not_changed_into_failure(monkeypatch):
    async def cancel(_job_id):
        raise asyncio.CancelledError

    monkeypatch.setattr(generate_job, "_run", cancel)

    with pytest.raises(asyncio.CancelledError):
        await generate_job.run("job-1")
