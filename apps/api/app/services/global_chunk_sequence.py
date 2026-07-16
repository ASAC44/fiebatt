"""Resumable, dependency-aware execution of one occurrence's chunks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable


MAX_CHUNK_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ChunkState:
    id: str
    index: int
    status: str
    input_revision: str | None = None
    output_url: str | None = None


@dataclass(frozen=True, slots=True)
class ChunkExecution:
    output_url: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SequenceOutcome:
    completed: bool
    output_urls: tuple[str, ...]
    failed_chunk_id: str | None = None
    error: str | None = None


ExecuteChunk = Callable[[ChunkState, str | None], Awaitable[ChunkExecution]]
MarkStarted = Callable[[ChunkState, str], Awaitable[None]]
MarkSucceeded = Callable[[ChunkState, str, ChunkExecution], Awaitable[None]]
MarkFailed = Callable[[ChunkState, str], Awaitable[None]]


def _ordered_states(chunks: list[ChunkState]) -> list[ChunkState]:
    ordered = sorted(chunks, key=lambda chunk: chunk.index)
    if [chunk.index for chunk in ordered] != list(range(len(ordered))):
        raise ValueError("chunk indexes must be contiguous and start at zero")
    return ordered


async def run_chunk_sequence(
    chunks: list[ChunkState],
    *,
    source_revision: str,
    execute: ExecuteChunk,
    mark_started: MarkStarted,
    mark_succeeded: MarkSucceeded,
    mark_failed: MarkFailed,
) -> SequenceOutcome:
    """Run chunks in order and reuse only outputs with the same dependency."""
    ordered = _ordered_states(chunks)
    outputs: list[str] = []
    previous_output: str | None = None
    for chunk in ordered:
        expected_input = previous_output or source_revision
        reusable = (
            chunk.status == "generated"
            and bool(chunk.output_url)
            and chunk.input_revision == expected_input
        )
        if reusable:
            assert chunk.output_url is not None
            outputs.append(chunk.output_url)
            previous_output = chunk.output_url
            continue

        try:
            await mark_started(chunk, expected_input)
            result = await execute(chunk, previous_output)
            if not result.output_url:
                raise ValueError("chunk executor returned no output URL")
        except Exception as exc:
            error = str(exc).strip() or type(exc).__name__
            await mark_failed(chunk, error)
            return SequenceOutcome(
                completed=False,
                output_urls=tuple(outputs),
                failed_chunk_id=chunk.id,
                error=error,
            )

        await mark_succeeded(chunk, expected_input, result)
        outputs.append(result.output_url)
        previous_output = result.output_url

    return SequenceOutcome(completed=True, output_urls=tuple(outputs))
