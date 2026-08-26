import pytest

from app.core.config import MIN_SECRET_KEY_LENGTH, Settings

STRONG_KEY = "x" * MIN_SECRET_KEY_LENGTH


def _settings(**overrides: str) -> Settings:
    # _env_file=None keeps a developer's local .env from deciding the result.
    return Settings(_env_file=None, **overrides)


def test_development_tolerates_the_placeholder_key() -> None:
    settings = _settings(
        environment="development",
        secret_key="change-me-in-production",
    )

    assert settings.secret_key == "change-me-in-production"


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_placeholder_key_is_rejected_outside_development(environment: str) -> None:
    with pytest.raises(ValueError, match="placeholder"):
        _settings(environment=environment, secret_key="replace-this-in-production")


def test_short_key_is_rejected_outside_development() -> None:
    with pytest.raises(ValueError, match=str(MIN_SECRET_KEY_LENGTH)):
        _settings(environment="production", secret_key="x" * 31)


def test_strong_key_is_accepted_in_production() -> None:
    settings = _settings(environment="production", secret_key=STRONG_KEY)

    assert settings.secret_key == STRONG_KEY


def test_unset_environment_is_not_treated_as_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    with pytest.raises(ValueError, match="placeholder"):
        Settings(_env_file=None, secret_key="change-me-in-production")


def test_unset_environment_still_accepts_a_strong_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    settings = Settings(_env_file=None, secret_key=STRONG_KEY)

    assert settings.secret_key == STRONG_KEY
