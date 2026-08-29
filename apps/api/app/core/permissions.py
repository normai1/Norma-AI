"""
The organization permission model.

Every later feature that adds a protected mutation (companies, contacts,
opportunities, tasks, notes, documents, conversations, prompts, audit logs)
should add its own permission constants here and extend ROLE_PERMISSIONS,
rather than writing a new role-list check or hardcoding roles at the route.
"""

MANAGE_ORGANIZATION = "organization:manage"
MANAGE_MEMBERS = "members:manage"
CREATE_INVITATIONS = "invitations:create"
REVOKE_INVITATIONS = "invitations:revoke"
MANAGE_WORKSPACES = "workspaces:manage"
MANAGE_ASSISTANTS = "assistants:manage"

# Owner and admin get identical grants today - this is byte-for-byte what the
# OrgAdmin role list it replaces already allowed. Member and viewer get none.
_ELEVATED = frozenset(
    {
        MANAGE_ORGANIZATION,
        MANAGE_MEMBERS,
        CREATE_INVITATIONS,
        REVOKE_INVITATIONS,
        MANAGE_WORKSPACES,
        MANAGE_ASSISTANTS,
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": _ELEVATED,
    "admin": _ELEVATED,
    "member": frozenset(),
    "viewer": frozenset(),
}


def has_permission(role: str, permission: str) -> bool:
    """
    Whether a role carries a permission. Unrecognized roles are denied.
    """

    return permission in ROLE_PERMISSIONS.get(role, frozenset())
