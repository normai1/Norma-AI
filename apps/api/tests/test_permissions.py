import pytest

from app.core.permissions import (
    CREATE_INVITATIONS,
    MANAGE_MEMBERS,
    MANAGE_ORGANIZATION,
    REVOKE_INVITATIONS,
    has_permission,
)

ALL_PERMISSIONS = (
    MANAGE_ORGANIZATION,
    MANAGE_MEMBERS,
    CREATE_INVITATIONS,
    REVOKE_INVITATIONS,
)

ELEVATED_ROLES = ("owner", "admin")
NON_ELEVATED_ROLES = ("member", "viewer")


@pytest.mark.parametrize("role", ELEVATED_ROLES)
@pytest.mark.parametrize("permission", ALL_PERMISSIONS)
def test_elevated_roles_hold_every_permission(role: str, permission: str) -> None:
    assert has_permission(role, permission) is True


@pytest.mark.parametrize("role", NON_ELEVATED_ROLES)
@pytest.mark.parametrize("permission", ALL_PERMISSIONS)
def test_non_elevated_roles_hold_no_permission(role: str, permission: str) -> None:
    assert has_permission(role, permission) is False


def test_unrecognized_role_is_denied_rather_than_raising() -> None:
    assert has_permission("superuser", MANAGE_ORGANIZATION) is False


def test_unrecognized_permission_is_denied() -> None:
    assert has_permission("owner", "companies:delete") is False
