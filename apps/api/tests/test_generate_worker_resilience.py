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


@pytest.mark.asyncio
async def test_transition_review_does_not_invent_a_missing_project_edge(monkeypatch):
    calls = []

    async def score_seams(**frames):
        calls.append(frames)
        return {
            "entry_continuity": 2,
            "exit_continuity": 9,
            "evidence": [],
        }

    monkeypatch.setattr(generate_job.ai.gemini, "score_seams", score_seams)
    review = await generate_job._score_assembled_transitions_safe(
        {
            "entry_before_paths": [],
            "entry_after_paths": [],
            "exit_before_paths": ["generated-before.jpg"],
            "exit_after_paths": ["source-after.jpg"],
        }
    )

    assert len(calls) == 1
    assert review == {
        "entry_continuity": 10,
        "exit_continuity": 9,
        "entry_applicable": False,
        "exit_applicable": True,
        "evidence": [],
    }


@pytest.mark.asyncio
async def test_transition_review_skips_model_when_there_are_no_assembled_cuts(
    monkeypatch,
):
    async def score_seams(**_frames):
        pytest.fail("boundary reviewer should not run without a boundary")

    monkeypatch.setattr(generate_job.ai.gemini, "score_seams", score_seams)
    review = await generate_job._score_assembled_transitions_safe(
        {
            "entry_before_paths": [],
            "entry_after_paths": [],
            "exit_before_paths": [],
            "exit_after_paths": [],
        }
    )

    assert review == {
        "entry_continuity": 10,
        "exit_continuity": 10,
        "entry_applicable": False,
        "exit_applicable": False,
        "evidence": [],
    }
