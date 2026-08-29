"""
The organization permission model.

Every later feature that adds a protected mutation (assistants, prompt
templates, glossary entries, phone numbers, calls, knowledge sources,
contacts, appointments, campaigns) should add its own permission constants
here and extend ROLE_PERMISSIONS, rather than writing a new role-list check
or hardcoding roles at the route.
"""

MANAGE_ORGANIZATION = "organization:manage"
MANAGE_MEMBERS = "members:manage"
CREATE_INVITATIONS = "invitations:create"
REVOKE_INVITATIONS = "invitations:revoke"
MANAGE_WORKSPACES = "workspaces:manage"
MANAGE_ASSISTANTS = "assistants:manage"
MANAGE_PROMPT_TEMPLATES = "prompt_templates:manage"
MANAGE_KNOWLEDGE = "knowledge:manage"

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
        MANAGE_PROMPT_TEMPLATES,
        MANAGE_KNOWLEDGE,
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
