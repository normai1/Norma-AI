# Feature: Workspace foundation

**From build-plan:** feature 6a (under 6. Workspaces)

## Goal

Give organizations a second scoping level below themselves. `Workspace`/`WorkspaceMember` tables,
CRUD for workspaces, and `require_workspace_access` - the dependency every later workspace-scoped
feature (assistants, numbers, calls, contacts) will depend on to prove a caller may touch a specific
workspace, the same way `require_org_member` already proves organization access.

## In scope

- `Workspace` and `WorkspaceMember` models and their migration.
- A `MANAGE_WORKSPACES` permission (owner/admin, matching the existing elevated-role split).
- Workspace CRUD: create, list, get one, update name/settings.
- `require_workspace_access`: read access to one workspace, granted to either an org-level
  `MANAGE_WORKSPACES` holder or an explicit `WorkspaceMember`.
- Authorization and cross-tenant tests for all of the above.

## Out of scope

- **Adding or removing members from a workspace at all.** That's 6b's job entirely - this feature
  makes zero `WorkspaceMember` writes. Until 6b ships, only `MANAGE_WORKSPACES` holders (org
  owner/admin) can reach any workspace, via the permission bypass; a plain member or viewer can
  create nothing and sees an empty workspace list, because no mechanism exists yet to grant them
  one. That is expected, not a bug - see Notes for the AI.
- **Auto-adding the workspace creator as a `WorkspaceMember`.** Considered and dropped: only
  `MANAGE_WORKSPACES` holders can create a workspace, and they already bypass the `WorkspaceMember`
  check unconditionally, so an auto-grant would be redundant for its only plausible purpose (the
  creator's own access) and would introduce an untested, unrequested side effect (permanent access
  surviving a later role demotion).
- **Any UI.** 6b owns the workspace list/switcher screen.
- **Validated workspace settings** (timezone, locale, business hours, currency). `settings` ships as
  an unvalidated JSONB blob matching `Organization.settings`'s existing pattern - item 8 gives it a
  real shape, for both organizations and workspaces together. See Notes for the AI.
- **Workspace deletion or archival.** Not named in either plan; nothing currently needs it.
- **Retrofitting `Company`/`Contact`/etc. with `workspace_id`.** Those tables don't exist yet in this
  direction. Every future workspace-scoped feature adds its own `workspace_id` column and calls
  `require_workspace_access` - this feature only builds the dependency itself.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Models and migration** - add `app/models/workspace.py` (`Workspace`:
  `organization_id` FK -> organizations, `name`, `settings` JSONB default `'{}'::jsonb` - same shape
  as `Organization`) and `app/models/workspace_member.py` (`WorkspaceMember`: `workspace_id` FK ->
  workspaces, `user_id` FK -> users, unique on `(workspace_id, user_id)` - no `role` column; a
  workspace member's authority still comes entirely from their organization role). Register both in
  `app/db/base.py`. Generate and hand-verify the Alembic migration (indexes, cascades, no duplicate
  constraint names against existing tables). Add persistence tests to `tests/test_models.py`
  matching the existing `User`/`Session` coverage. *Done when:* `alembic upgrade head` /
  `downgrade -1` / `upgrade head` round-trips cleanly, and the new model tests pass.

- [x] **Step 2 - Create and list** - add `MANAGE_WORKSPACES` to `app/core/permissions.py`'s
  `_ELEVATED` set. Add `app/repositories/workspace.py` (`get_by_id`, `list_for_organization` - every
  workspace in an org, `list_for_user` - only workspaces the caller is a `WorkspaceMember` of,
  mirroring `organization_repo.list_for_user`'s join shape, `create`). Add `app/services/workspace.py`:
  `create_workspace` and `list_workspaces` (returns every org workspace when the caller holds
  `MANAGE_WORKSPACES`, otherwise only the ones they belong to - empty for everyone until 6b ships
  the add-member endpoint, see Out of scope). Add `CanManageWorkspaces` to `app/api/org_deps.py`
  (`require_permission(MANAGE_WORKSPACES)`, same pattern as `CanManageMembers`). Add
  `app/schemas/workspace.py` (`WorkspaceCreate`: `name` only, matching `OrganizationCreate`'s
  `min_length=1`; `WorkspaceResponse`). Add `POST /organizations/{organization_id}/workspaces` and
  `GET /organizations/{organization_id}/workspaces` to a new `app/api/v1/workspaces.py`, wired into
  the router alongside `organizations.py`. Ship `tests/test_workspaces.py`: create succeeds for an
  admin; create is denied for member/viewer roles; create rejects an empty name with 422; list
  returns every workspace for an org admin; list returns an empty list for a plain member (nothing
  can grant them one yet); list is rejected for a non-member of the organization entirely; every
  workspace route rejects an unauthenticated request with 401. *Done when:* the tests pass.

- [x] **Step 3 - Get, update, and `require_workspace_access`** - add `WorkspaceNotFound` to
  `app/core/exceptions.py` (new `WorkspaceError` base, matching the `AuthError`/`OrganizationError`
  pattern). Add `app/repositories/workspace_member.py` (`get` only - `create`/`delete` are 6b's job).
  Add `update` to `workspace_repo` (partial update, `None` means untouched, matching
  `organization_repo.update`) and `update_workspace` to the service (resolves the workspace by id,
  raising `WorkspaceNotFound` if missing or in a different organization, matching
  `organization_service`'s exception-per-route-catch pattern). Add `app/api/workspace_deps.py`:
  `require_workspace_access` (resolves `workspace_id` from the path plus `CurrentOrgMembership`;
  404s if the workspace doesn't exist or belongs to a different organization; returns it directly if
  the caller holds `MANAGE_WORKSPACES`; otherwise requires a `WorkspaceMember` row, 404 if absent)
  and `CurrentWorkspace = Annotated[Workspace, Depends(require_workspace_access)]`. Add
  `WorkspaceUpdate` to the schemas. Add `GET /organizations/{organization_id}/workspaces/{workspace_id}`
  (using `CurrentWorkspace`) and `PATCH .../{workspace_id}` (using `CanManageWorkspaces`, catching
  `WorkspaceNotFound` -> 404) to `workspaces.py`. Extend `tests/test_workspaces.py`: get succeeds for
  an explicit member - insert the `WorkspaceMember` row directly through the `db` fixture, matching
  `test_organization_concurrency.py`'s precedent for testing a mechanism before its public endpoint
  exists; get succeeds for an org admin who is *not* an explicit member (the bypass this step exists
  to prove); get 404s for an org member who is neither; update succeeds for an admin and is denied
  for a member/viewer; update 404s for a workspace id from a different organization; a malformed
  workspace id returns 422, not a 500. *Done when:* the tests pass.

- [x] **Repair F-29 - workspace `settings` update test coverage** - add tests to
  `tests/test_workspaces.py`: a PATCH sending `settings` persists it; a name-only PATCH leaves
  `settings` untouched; a settings-only PATCH leaves `name` untouched - mirroring
  `test_organization_members.py::test_update_settings_without_touching_name`'s coverage of the
  identical pattern on `Organization`. *Done when:* the tests pass.

## Files / areas

| Path | Change |
| --- | --- |
| `apps/api/app/models/workspace.py` | new |
| `apps/api/app/models/workspace_member.py` | new |
| `apps/api/app/db/base.py` | edit - register new models |
| `apps/api/alembic/versions/` | new migration |
| `apps/api/app/core/permissions.py` | edit - `MANAGE_WORKSPACES` |
| `apps/api/app/core/exceptions.py` | edit - `WorkspaceError`, `WorkspaceNotFound` |
| `apps/api/app/repositories/workspace.py` | new |
| `apps/api/app/repositories/workspace_member.py` | new |
| `apps/api/app/services/workspace.py` | new |
| `apps/api/app/api/org_deps.py` | edit - `CanManageWorkspaces` |
| `apps/api/app/api/workspace_deps.py` | new |
| `apps/api/app/schemas/workspace.py` | new |
| `apps/api/app/api/v1/workspaces.py` | new |
| `apps/api/tests/test_models.py` | edit - `Workspace`/`WorkspaceMember` persistence |
| `apps/api/tests/test_workspaces.py` | new |

## Data / contracts

- **`require_workspace_access` is the load-bearing contract this feature exists to deliver.** Every
  future workspace-scoped route depends on it: `MANAGE_WORKSPACES` holders (org owner/admin) reach
  any workspace in their organization unconditionally; anyone else needs an explicit `WorkspaceMember`
  row. A 404 covers both "no such workspace" and "not granted access," the same information-hiding
  reasoning `require_org_member` already uses.
- **`WorkspaceMember` carries no role.** Authority comes entirely from the caller's organization
  role plus whether they hold a `WorkspaceMember` row - there is no separate per-workspace
  permission system. If a later feature needs one, that's a deliberate new decision, not an
  oversight here.
- **`settings` is unvalidated JSONB**, matching `Organization.settings` exactly. `project-overview.md`
  has been corrected to reflect this (it previously listed dedicated `timezone`/`locale`/
  `business_hours`/`currency` columns, which belong to item 8 instead).
- No workspace-scoped resource tables (`Assistant`, `PhoneNumber`, `Call`, `Contact`, ...) are
  touched - those get their own `workspace_id` column and their own migration when each is built.

## Testing

Same gate as every backend feature so far: `pytest` is already the declared test command, and
`coding-standards.md` requires cross-tenant and authorization tests for every scoped resource.

| Step | Coverage |
| --- | --- |
| 1 | `test_models.py` - `Workspace`/`WorkspaceMember` persist with generated ids and timestamps |
| 2 | `test_workspaces.py` - create success/denial, empty-name 422, list scoping (admin sees all, member sees an empty list, non-member denied), unauthenticated 401 across every route |
| 3 | `test_workspaces.py` - get via a directly-inserted `WorkspaceMember` row, get via admin bypass without membership, get denied for a true outsider, update success/denial, cross-organization 404, malformed-id 422 |

Run with `pytest` from either the repository root or `apps/api` - `pytest.ini` at the root covers
both.

## Notes for the AI

- **Match `organizations.py`'s exact layering**: route -> service -> repository -> database,
  `Annotated` dependency aliases, service functions raise domain exceptions that routes catch and
  translate to HTTP errors - don't invent a different pattern for workspaces.
- **The `settings` scope cut is deliberate, not an oversight.** Build-plan item 8 explicitly frames
  organization *and* workspace settings as "an unvalidated JSON blob" today, so giving `Workspace`
  dedicated typed columns now would fight the plan's own sequencing. `project-overview.md` has
  already been corrected to match - don't re-introduce the typed-column shape from memory.
- **Nobody but an org admin can reach any workspace until 6b ships.** That is the correct state for
  this feature, not a gap to work around - don't add an add-member endpoint or auto-membership here
  to "make it more useful," that scope belongs to 6b.
- **`CanManageWorkspaces` bypasses `WorkspaceMember` entirely by design.** An org owner/admin
  managing workspace settings should never need to be manually added to every workspace first -
  that's exactly how `MANAGE_MEMBERS` already lets an admin manage any member without extra grants.
- **No em dashes**, two blank lines between top-level Python definitions, short docstrings on public
  functions and multi-line helpers, explicit return types - same conventions as every prior backend
  feature.
