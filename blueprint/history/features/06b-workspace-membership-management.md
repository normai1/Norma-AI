# Feature: Workspace membership management

**From build-plan:** feature 6b (under 6. Workspaces)

## Goal

Let an org-level workspace manager grant or revoke an existing organization member's access to a
specific workspace, and see who currently has it. This is what makes the `WorkspaceMember` path in
`require_workspace_access` (6a) reachable through the API for the first time - until now only the
`MANAGE_WORKSPACES` bypass was usable.

## In scope

- `POST /organizations/{organization_id}/workspaces/{workspace_id}/members` - grant an existing
  organization member access to a workspace, identified by their `OrganizationMember` id (matching
  how `PATCH`/`DELETE .../members/{member_id}` already address memberships, not raw user ids).
- `GET /organizations/{organization_id}/workspaces/{workspace_id}/members` - list who has access,
  open to anyone who can already read the workspace (`CurrentWorkspace`), matching how organization
  membership lists are open to any org member, not just managers.
- `DELETE /organizations/{organization_id}/workspaces/{workspace_id}/members/{workspace_member_id}` -
  revoke access.
- Authorization and cross-tenant tests for all three.

## Out of scope

- **Any UI.** 6c owns the workspace list/switcher and the member management screen.
- **A role on `WorkspaceMember`.** Unchanged from 6a - authority stays entirely at the organization
  level; this feature only controls *which* workspaces a member can reach.
- **Removing yourself, or last-manager protection.** Organizations have a last-owner guard because a
  organization with no owner is unrecoverable. A workspace with no explicit members is not a
  problem - any `MANAGE_WORKSPACES` holder still reaches it via the bypass 6a already built. No
  equivalent guard is needed here.
- **Notifying the added user.** No email/SMS delivery abstraction is wired to workspaces; out of
  scope until a feature actually needs it.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Add member and list members** - add `WorkspaceMemberNotFound` and
  `WorkspaceMemberAlreadyExists` to `app/core/exceptions.py` (both `WorkspaceError`). Add
  `create`, `get_by_id` (scoped to `workspace_id`, matching
  `organization_member_repo.get_by_id`'s organization-scoping pattern), and `list_for_workspace`
  (joins `User`, matching `organization_member_repo.list_members`) to `workspace_member_repo`. Add
  a private `_resolve_workspace(db, *, organization_id, workspace_id)` helper to
  `app/services/workspace.py` (the same "get or raise `WorkspaceNotFound`" check `update_workspace`
  already does inline - factor it out here since this feature adds two more call sites) and use it
  from `update_workspace` too. Add `add_member` (resolves the target via
  `organization_member_repo.get_by_id`, raising the existing `MemberNotFound` if it doesn't resolve
  to a member of this organization; raises `WorkspaceMemberAlreadyExists` if a `WorkspaceMember` row
  already exists for that user, checked before insert and backstopped by catching `IntegrityError`
  on the actual insert) and `list_workspace_members`. Add `WorkspaceMemberCreate` (`member_id:
  uuid.UUID`) and `WorkspaceMemberResponse` (`id`, `workspace_id`, `created_at`, `user:
  MemberUserResponse` - reusing the existing schema from `app/schemas/organization.py`, not
  duplicating it) to `app/schemas/workspace.py`. Add the `POST` and `GET` routes to
  `workspaces.py`: `POST` uses `CanManageWorkspaces` and catches `WorkspaceNotFound` (404),
  `MemberNotFound` (404, distinct detail message), and `WorkspaceMemberAlreadyExists` (409); `GET`
  uses `CurrentWorkspace`. Ship `tests/test_workspaces.py` coverage: add succeeds and the member
  then appears in the list; add is denied for member/viewer; add 404s for a `member_id` that
  belongs to a different organization; add 409s when the target already has access; list is
  reachable by an explicit member (not just a manager); list 404s for a workspace in another
  organization. *Done when:* the tests pass.

- [x] **Step 2 - Remove member** - add `delete` to `workspace_member_repo` (matching
  `organization_member_repo.delete`). Add `remove_member` to the service (resolves the workspace
  via `_resolve_workspace`, the target row via `workspace_member_repo.get_by_id`, raising
  `WorkspaceMemberNotFound` if it doesn't belong to this workspace). Add the `DELETE` route using
  `CanManageWorkspaces`, catching `WorkspaceNotFound` and `WorkspaceMemberNotFound` (both 404).
  Extend `tests/test_workspaces.py`: remove succeeds and the member disappears from the list and
  loses access (a follow-up `GET` for that workspace as the removed member now 404s); remove is
  denied for member/viewer; remove 404s for a `workspace_member_id` that belongs to a different
  workspace (including one in a different organization). *Done when:* the tests pass.

- [x] **Repair F-30 and F-33 - stop duplicating "not found" constants** - in `workspaces.py`,
  drop the locally-defined `_WORKSPACE_NOT_FOUND` and import `workspace_deps.py`'s `_NOT_FOUND`
  in its place (aliased so every existing `raise _WORKSPACE_NOT_FOUND from exc` site stays
  unchanged); drop the locally-defined `_MEMBER_NOT_FOUND` and import `organizations.py`'s
  instead. *Done when:* `pytest apps/api/tests` and `ruff check apps/api` both stay green with no
  behavior change - this is a pure de-duplication, no new test needed.

## Files / areas

| Path | Change |
| --- | --- |
| `apps/api/app/core/exceptions.py` | edit - `WorkspaceMemberNotFound`, `WorkspaceMemberAlreadyExists` |
| `apps/api/app/repositories/workspace_member.py` | edit - `create`, `get_by_id`, `list_for_workspace`, `delete` |
| `apps/api/app/services/workspace.py` | edit - `_resolve_workspace`, `add_member`, `list_workspace_members`, `remove_member` |
| `apps/api/app/schemas/workspace.py` | edit - `WorkspaceMemberCreate`, `WorkspaceMemberResponse` |
| `apps/api/app/api/v1/workspaces.py` | edit - three new routes |
| `apps/api/tests/test_workspaces.py` | edit - new coverage |

## Data / contracts

- **`member_id` addresses an `OrganizationMember`, not a `User`.** Matches the existing
  `PATCH`/`DELETE .../members/{member_id}` convention exactly, and structurally guarantees the
  target actually belongs to this organization before any workspace grant happens - a raw
  `user_id` would need a separate existence-and-membership check to get the same guarantee for
  free.
- **`WorkspaceMemberResponse` reuses `MemberUserResponse`** from `app/schemas/organization.py`
  rather than redefining an identical user-shape schema. If 6c's UI needs a different shape later,
  that is a deliberate new decision, not a missed reuse opportunity now.

## Testing

Same gate as every backend feature so far: `pytest` is already the declared test command, and
`coding-standards.md` requires cross-tenant and authorization tests for every scoped resource.

| Step | Coverage |
| --- | --- |
| 1 | Add success + appears in list, permission denial, cross-org `member_id` 404, duplicate-add 409, list reachable by explicit member, list 404 across organizations |
| 2 | Remove success + disappears from list + access actually revoked, permission denial, cross-workspace/cross-org `workspace_member_id` 404 |

Run with `pytest` from either the repository root or `apps/api` - `pytest.ini` at the root covers
both.

## Notes for the AI

- **This is what makes 6a's `WorkspaceMember` bypass path reachable for the first time.** Every
  test in `test_workspaces.py` that so far had to insert a `WorkspaceMember` row directly through
  the `db` fixture (`test_get_succeeds_for_an_explicit_member`,
  `test_member_access_to_one_workspace_does_not_reach_a_sibling`) could be rewritten to go through
  the real `POST` endpoint instead - doing so is optional cleanup, not required by this feature, and
  should not be bundled into these steps if attempted.
- **Match `organizations.py`'s exact layering and exception-per-route-catch pattern** - this feature
  extends `workspace.py`'s service/repository, it does not introduce a new pattern.
- **No em dashes**, two blank lines between top-level Python definitions, short docstrings on public
  functions and multi-line helpers, explicit return types - same conventions as every prior backend
  feature.
