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


class InvalidCurrentPassword(AuthError):
    """
    A password-change request's current_password did not match the account.
    """


class PasswordUnchanged(AuthError):
    """
    A password-change request's new_password is identical to the current one.
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


class WorkspaceMemberNotFound(WorkspaceError):
    """
    No such membership of this workspace.
    """


class WorkspaceMemberAlreadyExists(WorkspaceError):
    """
    The target user already has access to the workspace.
    """


class AssistantError(Exception):
    """
    Base class for assistant failures.
    """


class AssistantNotFound(AssistantError):
    """
    No such assistant in this workspace.
    """


class AssistantVersionNotFound(AssistantError):
    """
    No such version of this assistant.
    """


class AssistantVersionImmutable(AssistantError):
    """
    An AssistantVersion row was about to be updated in place. Versions are
    immutable configuration snapshots - item 20's call engine reads whatever
    version is current mid-call, so a version silently changing under it
    would be a live-call correctness bug, not just a style violation.
    """


class AssistantArchived(AssistantError):
    """
    The assistant is archived - a terminal lifecycle state in this codebase
    (no restore/un-archive path exists yet), so it cannot be published.
    """


class PromptTemplateError(Exception):
    """
    Base class for prompt template failures.
    """


class PromptTemplateNotFound(PromptTemplateError):
    """
    No such prompt template in this workspace.
    """


class PromptVersionNotFound(PromptTemplateError):
    """
    No such version of this prompt template.
    """


class PromptVersionImmutable(PromptTemplateError):
    """
    A PromptVersion row was about to be updated in place. Versions are
    immutable content snapshots - 12b's variable-interpolation renderer and
    whichever assistant references a version by id must never see it change
    under them.
    """


class PromptTemplateArchived(PromptTemplateError):
    """
    The prompt template is archived - a terminal lifecycle state in this
    codebase (no restore/un-archive path exists yet), so it cannot be
    published.
    """


class PromptRenderError(Exception):
    """
    A `{{namespace.field}}` placeholder in a prompt named a namespace or
    field the rendering context did not provide - a typo'd variable name in
    the template, not a value to silently blank out.
    """


class GlossaryEntryError(Exception):
    """
    Base class for glossary entry failures.
    """


class GlossaryEntryNotFound(GlossaryEntryError):
    """
    No such glossary entry on this assistant.
    """


class GlossaryEntryAlreadyExists(GlossaryEntryError):
    """
    This assistant already has a glossary entry for this term.
    """


class KnowledgeSourceError(Exception):
    """
    Base class for knowledge source failures.
    """


class KnowledgeSourceNotFound(KnowledgeSourceError):
    """
    No such knowledge source in this workspace.
    """


class UnsupportedFileType(KnowledgeSourceError):
    """
    The uploaded file's extension is not one this codebase parses.
    """


class FileTooLarge(KnowledgeSourceError):
    """
    The uploaded file exceeds the size cap.
    """


class FaqEntryNotFound(KnowledgeSourceError):
    """
    No such FAQ entry on this knowledge source - including when the source
    exists but is not type='manual_faq'.
    """


class InvalidKnowledgeSourceType(KnowledgeSourceError):
    """
    The requested operation does not apply to this knowledge source's type
    (e.g. manual reprocessing, which only a 'file'-type source supports).
    """
