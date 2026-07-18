import pytest

from app.services.generation_failure import classify_generation_failure


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        ("HappyHorse task timed out after 600s", "provider_timeout", True),
        ("submit failed (HTTP 429): quota exceeded", "provider_busy", True),
        ("HTTP 503 service unavailable", "provider_unavailable", True),
        ("generated clip is too short (3.0s for 4.0s edit)", "invalid_provider_duration", True),
        ("ffprobe could not decode output", "invalid_provider_media", True),
        ("source video unavailable", "source_unavailable", False),
        ("unexpected provider response", "generation_failed", True),
    ],
)
def test_generation_failures_have_stable_product_states(error, code, retryable):
    failure = classify_generation_failure(error)

    assert failure.code == code
    assert failure.retryable is retryable
    assert any(
        phrase in failure.user_message
        for phrase in ("unchanged", "not added", "Nothing was changed")
    )
    assert error not in failure.user_message
