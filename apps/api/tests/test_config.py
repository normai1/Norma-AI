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
    settings = _settings(
        environment="production",
        secret_key=STRONG_KEY,
        internal_api_secret=STRONG_KEY,
    )

    assert settings.secret_key == STRONG_KEY


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_placeholder_internal_secret_is_rejected_outside_development(
    environment: str,
) -> None:
    with pytest.raises(ValueError, match="INTERNAL_API_SECRET.*placeholder"):
        _settings(
            environment=environment,
            secret_key=STRONG_KEY,
            internal_api_secret="replace-this-in-production",
        )


def test_short_internal_secret_is_rejected_outside_development() -> None:
    with pytest.raises(
        ValueError, match=f"INTERNAL_API_SECRET.*{MIN_SECRET_KEY_LENGTH}"
    ):
        _settings(
            environment="production",
            secret_key=STRONG_KEY,
            internal_api_secret="x" * 31,
        )


def test_strong_internal_secret_is_accepted_in_production() -> None:
    settings = _settings(
        environment="production",
        secret_key=STRONG_KEY,
        internal_api_secret=STRONG_KEY,
    )

    assert settings.internal_api_secret == STRONG_KEY


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

    settings = Settings(
        _env_file=None, secret_key=STRONG_KEY, internal_api_secret=STRONG_KEY
    )

    assert settings.secret_key == STRONG_KEY


# ----------------------------------------------------------------------
# Database URL driver
#
# Every managed provider hands out a plain postgres:// or postgresql:// URL,
# because the driver is the application's business rather than the
# database's. SQLAlchemy reads the scheme AS the driver, so pasting the
# provider's own connection string in resolves to psycopg2 - not installed,
# and the wrong shape regardless, since this application's engine is async.
#
# The failure only appears on a deployed environment, which is the worst
# place to discover it, so it is pinned here instead.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        "postgresql://norma:pw@db.example.com:5432/norma",
        "postgres://norma:pw@db.example.com:5432/norma",
    ],
)
def test_a_providers_plain_url_is_pointed_at_the_async_driver(given: str) -> None:
    settings = _settings(secret_key=STRONG_KEY, database_url=given)

    assert settings.database_url.startswith("postgresql+asyncpg://")
    # Everything after the scheme is untouched - credentials, host, port and
    # database name are the provider's and must survive verbatim.
    assert settings.database_url.endswith("norma:pw@db.example.com:5432/norma")


def test_an_explicit_driver_is_left_alone() -> None:
    """
    Someone naming a driver is being deliberate - including alembic/env.py,
    which swaps asyncpg for psycopg to run migrations synchronously against
    this same value.
    """

    given = "postgresql+psycopg://norma:pw@localhost:5432/norma"

    assert _settings(secret_key=STRONG_KEY, database_url=given).database_url == given


def test_the_test_database_url_is_normalised_the_same_way() -> None:
    settings = _settings(
        secret_key=STRONG_KEY,
        test_database_url="postgresql://norma:pw@localhost:5432/norma_test",
    )

    assert settings.test_database_url.startswith("postgresql+asyncpg://")
