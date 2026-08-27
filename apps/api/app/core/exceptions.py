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


class OrganizationError(Exception):
    """
    Base class for organization and membership failures.
    """


class SlugGenerationFailed(OrganizationError):
    """
    A free slug could not be found after repeated attempts.
    """


class NotAnOrganizationMember(OrganizationError):
    """
    The user has no membership of the requested organization.

    Routes surface this as 404 rather than 403 so an authenticated user cannot
    discover which organization ids exist.
    """


class MemberNotFound(OrganizationError):
    """
    No such membership in this organization.
    """


class RoleEscalation(OrganizationError):
    """
    The caller tried to grant or act on a role at or above their own.

    Surfaced as 403: the caller has already proven the organization exists to
    them, so refusing plainly leaks nothing.
    """


class LastOwnerRemoval(OrganizationError):
    """
    The change would leave an organization with no owner.
    """


class AlreadyAMember(OrganizationError):
    """
    The invited address already belongs to a member of the organization.
    """


class InvalidInvitation(OrganizationError):
    """
    Invitation token is unknown, expired, revoked, or already accepted.
    """


class InvitationConflict(OrganizationError):
    """
    Repeated concurrent invitations to the same address kept colliding.
    """


class InvitationEmailMismatch(OrganizationError):
    """
    The accepting user's address does not match the invited address.
    """


class WorkspaceError(Exception):
    """
    Base class for workspace failures.
    """


class WorkspaceNotFound(WorkspaceError):
    """
    No such workspace in this organization.
    """
