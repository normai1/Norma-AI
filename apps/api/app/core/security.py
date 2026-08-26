import hashlib
import secrets

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()

# Dummy argon2 hash used to spend the same work when an email is unknown, so
# login timing does not reveal which addresses are registered.
_DUMMY_HASH = _password_hash.hash("norma-dummy-password")


def hash_password(password: str) -> str:
    """
    Hash a plaintext password with argon2.
    """

    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Check a plaintext password against a stored argon2 hash.
    """

    return _password_hash.verify(password, password_hash)


def verify_dummy_password(password: str) -> bool:
    """
    Burn equivalent hashing time for an unknown email. Always returns False.
    """

    _password_hash.verify(password, _DUMMY_HASH)

    return False


def generate_refresh_token() -> str:
    """
    Create a high-entropy opaque refresh token.
    """

    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """
    Hash a refresh token for storage and lookup.

    SHA-256 rather than argon2: refresh tokens are high-entropy random values,
    so a slow KDF adds no protection and would make lookup by token expensive.
    """

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    """
    Fold an email address to the form stored in the unique index.
    """

    return email.strip().lower()
