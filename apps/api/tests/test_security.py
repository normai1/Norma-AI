import pytest

from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_token,
    normalize_email,
    verify_dummy_password,
    verify_password,
)


def test_hash_password_does_not_return_plaintext() -> None:
    hashed = hash_password("correct-horse-battery")

    assert hashed != "correct-horse-battery"
    assert hashed.startswith("$argon2")


def test_verify_password_accepts_correct_password() -> None:
    hashed = hash_password("correct-horse-battery")

    assert verify_password("correct-horse-battery", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("correct-horse-battery")

    assert verify_password("wrong-password", hashed) is False


def test_hashes_are_salted() -> None:
    first = hash_password("same-password")
    second = hash_password("same-password")

    assert first != second


def test_verify_dummy_password_always_false() -> None:
    assert verify_dummy_password("anything") is False


def test_hash_token_is_stable_and_hex() -> None:
    token = "a-refresh-token"

    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64
    assert int(hash_token(token), 16) >= 0


def test_hash_token_differs_per_token() -> None:
    assert hash_token("token-a") != hash_token("token-b")


def test_generate_refresh_token_is_unique_and_long() -> None:
    first = generate_refresh_token()
    second = generate_refresh_token()

    assert first != second
    assert len(first) >= 43


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("User@Example.com", "user@example.com"),
        ("  spaced@example.com  ", "spaced@example.com"),
        ("ALLCAPS@EXAMPLE.COM", "allcaps@example.com"),
        ("already@example.com", "already@example.com"),
    ],
)
def test_normalize_email(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected
