import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def test_development_defaults_remain_local_friendly():
    settings = Settings(_env_file=None)
    assert settings.app_env == "development"
    assert settings.s3_enabled is False


def test_object_storage_only_requires_a_bucket():
    settings = Settings(s3_bucket="media-bucket", _env_file=None)
    assert settings.s3_enabled is True
    assert settings.aws_region == "us-east-1"


def test_production_rejects_unsafe_defaults():
    with pytest.raises(ValidationError, match="Invalid production configuration"):
        Settings(app_env="production", _env_file=None)


def test_production_accepts_managed_configuration():
    settings = Settings(
        app_env="production",
        use_ai_stubs=False,
        auth_jwt_secret="a-production-secret-with-at-least-32-characters",
        auth_cookie_secure=True,
        gemini_api_key="platform-key",
        _env_file=None,
    )
    assert settings.app_env == "production"
    assert settings.real_ai_ready is True
