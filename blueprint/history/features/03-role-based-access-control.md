# Feature: Role-based access control

**From build-plan:** feature 3
**Status:** built and verified; awaiting review

## Goal

Replace the ad-hoc `require_org_role("owner", "admin")` allow-list feature 2 built out of
necessity with a real permission model: named permissions, a role-to-permission map, and a
`require_permission(permission)` dependency that enforces it. CLAUDE.md section 7 states this
directly - "Do not assume a role implies every permission. Use the existing permission model when
present" - and today there is no such model, only a role list repeated at each route.

This feature formalizes what already exists (organizations, members, invitations) and establishes
the pattern - one shared `ROLE_PERMISSIONS` table - that features 6 through 23 (companies, contacts,
opportunities, tasks, notes, documents, conversations, prompts, audit logs) will extend with their
own permissions as those resources are built. It does not invent permissions for resources that
don't exist yet.

## In scope

- `Permission` constants and a `ROLE_PERMISSIONS` map covering the four actions currently gated by
  `OrgAdmin`: managing organization details, managing members, creating invitations, revoking
  invitations.
- `has_permission(role, permission)`, a pure function, fail-closed for an unrecognized role.
- `require_permission(permission)`, a dependency factory replacing `require_org_role`, plus one
  named `Annotated` alias per permission.
- Swapping the five route signatures currently typed `OrgAdmin` to the specific permission each one
  actually requires.
- Removing `require_org_role`, `OrgAdmin`, and `OrgOwner` - confirmed unused elsewhere by grep, so
  nothing is left half-migrated.
- Closing a real test gap found while scoping this: no existing test proves a plain `member` or
  `viewer` is denied at any of the five now-permission-gated routes.

## Out of scope

- **Permissions for any CRM or knowledge resource** - companies, contacts, opportunities, tasks,
  notes, documents, conversations, prompts, audit logs. None of those exist yet (features 6-23).
  Inventing their permissions now would be a guess; each feature adds its own constants to
  `ROLE_PERMISSIONS` when it's actually built.
- **The role-hierarchy and escalation-prevention logic** (`ROLE_RANK`, `outranks`,
  `RoleEscalation`, `_guard_last_owner` in `organization_service.py` and `invitation_service.py`).
  That logic answers a different question - "can you assign this role at all" - not "does your role
  have this permission." It has already been through three audit rounds closing a real takeover path;
  this feature does not touch it, to avoid risking a regression in already-hardened code for no
  product benefit.
- **Organization deletion permissions** - no delete endpoint exists.
- **Splitting `members:manage` into separate change-role and remove permissions** - both actions are
  granted identically today. Split later only if a real requirement diverges them.
- **Any frontend change** - `canManage()` in the web client already collapses every management
  action into one boolean matching current behavior. No route's actual access set changes here, so
  there is nothing for the UI to react to.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Permission model** - add `app/core/permissions.py` with four permission constants
  (`MANAGE_ORGANIZATION`, `MANAGE_MEMBERS`, `CREATE_INVITATIONS`, `REVOKE_INVITATIONS`), a
  `ROLE_PERMISSIONS: dict[str, frozenset[str]]` mapping (owner and admin get all four - identical to
  what `OrgAdmin` grants today; member and viewer get none), and `has_permission(role, permission) ->
  bool` using `.get(role, frozenset())` so an unrecognized role denies rather than raises. Ship
  `tests/test_permissions.py` in the same diff. *Done when:* `pytest tests/test_permissions.py`
  passes, covering every (role, permission) pair in the 4x4 matrix plus an unrecognized-role case;
  `ruff check apps/api` is clean.

- [x] **Step 2 - Enforce it and retire the role list** - add `require_permission(permission)` to
  `app/api/org_deps.py` (same shape as the `require_org_role` it replaces: depends on
  `CurrentOrgMembership`, checks `has_permission`, raises 403 - never 404, since membership is
  already proven at this point) plus four named aliases (`CanManageOrganization`,
  `CanManageMembers`, `CanCreateInvitations`, `CanRevokeInvitations`). Remove `require_org_role`,
  `OrgAdmin`, and `OrgOwner`. Update the five `OrgAdmin`-typed parameters in
  `app/api/v1/organizations.py` (`update_organization`, `change_member_role`, `remove_member`) and
  `app/api/v1/invitations.py` (`create_invitation`, `revoke_invitation`) to the matching alias - only
  the type annotation changes, route bodies are untouched. Ship `tests/test_permission_enforcement.py`
  in the same diff, proving a plain `member` and a `viewer` each get 403 from all five routes - the
  gap identified while scoping this feature. *Done when:* the new tests pass; the full suite stays
  green with no regressions; `ruff check apps/api` is clean; a live request from a `viewer` token to
  `PATCH /organizations/{id}` returns 403.

## Files / areas

| Path | Change |
| --- | --- |
| `apps/api/app/core/permissions.py` | new - permission constants, `ROLE_PERMISSIONS`, `has_permission` |
| `apps/api/app/api/org_deps.py` | edit - add `require_permission` + 4 aliases; remove `require_org_role`, `OrgAdmin`, `OrgOwner` |
| `apps/api/app/api/v1/organizations.py` | edit - 3 route signatures |
| `apps/api/app/api/v1/invitations.py` | edit - 2 route signatures |
| `apps/api/tests/test_permissions.py` | new |
| `apps/api/tests/test_permission_enforcement.py` | new |

## Data / contracts

**Load-bearing.** `ROLE_PERMISSIONS` in `app/core/permissions.py` becomes the single source of truth
for what each organization role can do. Every later feature that adds a protected mutation
(companies, contacts, opportunities, tasks, notes, documents, conversations, prompts, audit logs)
should add its permission constants here and extend the map, rather than writing a new
`require_org_role`-style check or hardcoding a role list at the route.

### The four permissions this feature defines

| Permission | Replaces (route) | Owner | Admin | Member | Viewer |
| --- | --- | --- | --- | --- | --- |
| `organization:manage` | `PATCH /organizations/{id}` | yes | yes | no | no |
| `members:manage` | `PATCH`/`DELETE .../members/{id}` | yes | yes | no | no |
| `invitations:create` | `POST .../invitations` | yes | yes | no | no |
| `invitations:revoke` | `DELETE .../invitations/{id}` | yes | yes | no | no |

This is byte-for-byte what `OrgAdmin` already grants - the point of this feature is the mechanism
(a data table plus one dependency factory), not a behavior change. `CurrentOrgMembership`-gated
routes (viewing an organization, listing members, listing invitations) are unaffected; every member
regardless of role can still read those.

### Naming convention

Permission strings follow `resource:action` (e.g. `"organization:manage"`), plain string constants -
not an `enum.Enum` class, matching the project's existing convention of plain-string constants for
closed sets (`ORGANIZATION_ROLES`, `ORGANIZATION_STATUSES`). Permissions are never stored in the
database, so the enum-avoidance rationale from those two doesn't directly apply here, but consistency
with the rest of the codebase does.

### Decisions locked here

- **Fail closed on an unrecognized role.** `has_permission` returns `False` rather than raising for
  any role not in `ROLE_PERMISSIONS`. In practice `membership.role` is always one of the four values
  the database `CHECK` constraint allows, so this path is defensive, not reachable in normal
  operation - but authorization defaults to deny, not permit, on the unexpected case.
- **`require_org_role`, `OrgAdmin`, and `OrgOwner` are removed, not deprecated in place.** Keeping a
  role-list mechanism running alongside a permission-table mechanism would let the two drift and
  contradicts the goal of one source of truth. Confirmed by grep that nothing outside `org_deps.py`
  and the five routes above references them.
- **The role-hierarchy/escalation logic is a different concern and stays untouched.** See Out of
  scope. Do not fold `ROLE_RANK`/`outranks` into `ROLE_PERMISSIONS` - one governs which role a member
  can assign to someone else, the other governs which actions a role may take. Conflating them was
  considered and rejected: they answer different questions and mixing them would make both harder to
  reason about.

## Testing

Same gate as features 1 and 2: pytest and the test-database fixtures are already wired
(`tests/conftest.py`, `pytest.ini` at the repo root), and CLAUDE.md section 23 requires tests for
this kind of logic. Both steps ship their own tests in the same diff.

| Step | Coverage |
| --- | --- |
| 1 | `tests/test_permissions.py` - the full 4-role x 4-permission matrix (16 cases) plus an unrecognized-role-denies case |
| 2 | `tests/test_permission_enforcement.py` - member and viewer get 403 on all 5 gated routes (10 cases); existing owner/admin-succeeds coverage in `test_organization_members.py` and `test_invitations.py` must keep passing unchanged, proving no behavior regression |

Run with `pytest` from either the repository root or `apps/api` - `pytest.ini` at the root covers
both.

## Notes for the AI

- **`blueprint/context/coding-standards.md` still describes the wrong stack** (TypeScript/Next.js/
  Prisma/Clerk). Follow `CLAUDE.md` and the patterns already in `apps/api/app/` instead - flagged in
  features 1 and 2, still not fixed by `/onboard`.
- **Match the existing layering and style:** `Annotated` dependency-alias pattern, two blank lines
  between top-level definitions, a short docstring on each public function, explicit return types,
  no em dashes.
- **`require_permission`'s shape should mirror `require_org_role`'s** almost exactly - same
  `async def dependency(membership: CurrentOrgMembership) -> OrganizationMember` structure, just
  checking `has_permission(membership.role, permission)` instead of `role not in allowed_roles`.
  Reading the current `require_org_role` in `org_deps.py` before writing `require_permission` will
  make the diff smaller and the pattern obviously consistent.
- **A stale comment will surface in review, not a bug.** `test_organization_authorization.py` around
  line 210 has a comment reading "A plain member is not an OrgAdmin, so the role gate stops them
  first." The assertion (403) stays correct after this feature - `require_permission` denies a
  member exactly like `require_org_role` did - only the comment's wording becomes inaccurate. Fix the
  wording if convenient while touching that file; it is not worth a dedicated step.
- **Test with a live `viewer` token, not just `member`.** Both roles have zero permissions today, but
  they're distinct roles, and the done-when's live check specifically asks for `viewer` since that
  role has never been exercised against these routes at all before this feature.
