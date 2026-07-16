import pytest

from app.services.global_chunk_sequence import (
    ChunkExecution,
    ChunkState,
    run_chunk_sequence,
)


def _chunk(
    index: int,
    *,
    status: str = "planned",
    input_revision: str | None = None,
    output_url: str | None = None,
) -> ChunkState:
    return ChunkState(
        id=f"chunk-{index}",
        index=index,
        status=status,
        input_revision=input_revision,
        output_url=output_url,
    )


@pytest.mark.asyncio
async def test_chunks_run_in_order_and_receive_previous_output():
    calls = []
    events = []

    async def execute(chunk, previous):
        calls.append((chunk.id, previous))
        return ChunkExecution(f"output-{chunk.index}")

    async def started(chunk, revision):
        events.append(("started", chunk.id, revision))

    async def succeeded(chunk, revision, result):
        events.append(("succeeded", chunk.id, revision, result.output_url))

    async def failed(chunk, error):
        raise AssertionError((chunk, error))

    outcome = await run_chunk_sequence(
        [_chunk(0), _chunk(1), _chunk(2)],
        source_revision="source-v1",
        execute=execute,
        mark_started=started,
        mark_succeeded=succeeded,
        mark_failed=failed,
    )

    assert outcome.completed is True
    assert outcome.output_urls == ("output-0", "output-1", "output-2")
    assert calls == [
        ("chunk-0", None),
        ("chunk-1", "output-0"),
        ("chunk-2", "output-1"),
    ]
    assert events[0] == ("started", "chunk-0", "source-v1")


@pytest.mark.asyncio
async def test_valid_generated_prefix_is_reused():
    calls = []

    async def execute(chunk, previous):
        calls.append((chunk.id, previous))
        return ChunkExecution("output-1")

    async def no_op(*args):
        return None

    outcome = await run_chunk_sequence(
        [
            _chunk(
                0,
                status="generated",
                input_revision="source-v1",
                output_url="output-0",
            ),
            _chunk(1),
        ],
        source_revision="source-v1",
        execute=execute,
        mark_started=no_op,
        mark_succeeded=no_op,
        mark_failed=no_op,
    )

    assert outcome.completed is True
    assert calls == [("chunk-1", "output-0")]


@pytest.mark.asyncio
async def test_changed_dependency_regenerates_successor():
    calls = []

    async def execute(chunk, previous):
        calls.append((chunk.id, previous))
        return ChunkExecution(f"new-{chunk.index}")

    async def no_op(*args):
        return None

    outcome = await run_chunk_sequence(
        [
            _chunk(
                0,
                status="generated",
                input_revision="source-v1",
                output_url="new-0",
            ),
            _chunk(
                1,
                status="generated",
                input_revision="old-output-0",
                output_url="old-output-1",
            ),
        ],
        source_revision="source-v1",
        execute=execute,
        mark_started=no_op,
        mark_succeeded=no_op,
        mark_failed=no_op,
    )

    assert outcome.completed is True
    assert calls == [("chunk-1", "new-0")]


@pytest.mark.asyncio
async def test_failure_stops_before_dependent_chunks():
    calls = []
    failures = []

    async def execute(chunk, previous):
        calls.append(chunk.id)
        if chunk.index == 1:
            raise RuntimeError("provider unavailable")
        return ChunkExecution(f"output-{chunk.index}")

    async def no_op(*args):
        return None

    async def failed(chunk, error):
        failures.append((chunk.id, error))

    outcome = await run_chunk_sequence(
        [_chunk(0), _chunk(1), _chunk(2)],
        source_revision="source-v1",
        execute=execute,
        mark_started=no_op,
        mark_succeeded=no_op,
        mark_failed=failed,
    )

    assert outcome.completed is False
    assert calls == ["chunk-0", "chunk-1"]
    assert failures == [("chunk-1", "provider unavailable")]
    assert outcome.failed_chunk_id == "chunk-1"


@pytest.mark.asyncio
async def test_non_contiguous_indexes_are_rejected():
    async def no_op(*args):
        return None

    with pytest.raises(ValueError, match="contiguous"):
        await run_chunk_sequence(
            [_chunk(0), _chunk(2)],
            source_revision="source-v1",
            execute=no_op,
            mark_started=no_op,
            mark_succeeded=no_op,
            mark_failed=no_op,
        )
