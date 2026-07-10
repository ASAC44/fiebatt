"""Tests for the observability logger."""

import pytest
from ai.services.logger import tracked, get_stats


@pytest.mark.asyncio
async def test_tracked_decorator_logs_success():
    @tracked("test_service", "test_op")
    async def dummy():
        return "ok"

    result = await dummy()
    assert result == "ok"

    stats = get_stats()
    assert "test_service:test_op" in stats["services"]
    assert stats["services"]["test_service:test_op"]["calls"] >= 1
    assert stats["services"]["test_service:test_op"]["errors"] == 0


@pytest.mark.asyncio
async def test_tracked_decorator_logs_errors():
    @tracked("test_service", "test_fail")
    async def failing():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await failing()

    stats = get_stats()
    assert "test_service:test_fail" in stats["services"]
    assert stats["services"]["test_service:test_fail"]["errors"] >= 1
