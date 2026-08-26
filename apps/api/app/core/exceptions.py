class AuthError(Exception):
    """
    Base class for authentication and session failures.
    """


class EmailAlreadyRegistered(AuthError):
    """
    Registration attempted with an address that already exists.
    """


class InvalidCredentials(AuthError):
    """
    Email/password pair did not match an active account.

    Raised for both an unknown email and a wrong password, so the API never
    reveals which addresses are registered.
    """


class InactiveAccount(AuthError):
    """
    Credentials were correct but the account is deactivated.
    """


class InvalidRefreshToken(AuthError):
    """
    Refresh token is unknown, expired, revoked, or already used.
    """
