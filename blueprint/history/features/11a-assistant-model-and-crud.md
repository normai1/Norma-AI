# Feature: Assistant model and CRUD

**From build-plan:** feature 11a
**Status:** not started

## Goal

The `Assistant` table and workspace-scoped CRUD: create, list, get, rename, and archive an
assistant. This is the foundation every later sub-item of item 11 builds on - 11b adds validated
configuration fields, 11c adds `AssistantVersion` and pins `current_version_id`, 11d builds the
editor UI over all of it. Backend only, matching 6a's own precedent (workspace foundation shipped
backend-first, UI followed separately).

## Design reference

None. No UI in this sub-feature.

## In scope

- `Assistant` model: `organization_id`, `workspace_id`, `name`, `status`
  (`draft`/`published`/`archived`, CHECK-constrained, defaulting to `draft`) - matching
  `Organization`/`Workspace`'s exact conventions (UUID PK, timestamp mixin, indexed FKs).
- **No `current_version_id` column yet.** It's a nullable FK to `AssistantVersion`, which doesn't
  exist until 11c. Adding it now would mean either creating an unused table early or a dangling FK
  - deferred to 11c as a clean additive migration instead, per the two-plane additive-migration
  rule.
- `MANAGE_ASSISTANTS` permission constant, granted to owner/admin only (matching
  `MANAGE_WORKSPACES`'s exact grant), and a `CanManageAssistants` dependency reusing the existing
  `require_permission()` factory in `org_deps.py`.
- Repository, service, and route layers for: create, list (workspace-scoped), get one, rename
  (`PATCH`, name only - there is no other field yet), and archive (`POST .../archive`, an explicit
  action endpoint per CLAUDE.md's own documented `/assistants/{id}/publish` pattern - archiving is
  a real lifecycle transition, not an arbitrary field edit).
- Read access (list/get) via `CurrentWorkspace` - any workspace member, matching
  `list_workspace_members`'s exact precedent. Mutations (create/rename/archive) via
  `CanManageAssistants` (org-level) plus the target workspace resolved and validated in the
  service layer - matching `add_workspace_member`/`remove_workspace_member`'s exact precedent
  (not `CurrentWorkspace`, since those routes already prove this shape works for workspace-scoped
  mutations gated by an org-level permission).
- Cross-tenant and cross-workspace negative tests, matching every other scoped model added so far.

## Out of scope

- **Any configuration field** (voice, language, greeting, persona, speech rate, sensitivity,
  creativity, ambience) - 11b's.
- **`AssistantVersion`, versioning, diffing, rollback, `current_version_id`** - 11c's.
- **The editor UI** - 11d's. This sub-feature ships zero frontend code.
- **Un-archiving / restoring an assistant.** The build-plan line says "archive," not
  "archive and restore." Adding a restore endpoint nobody asked for yet is scope creep; note it
  as a natural follow-up if a real need shows up.
- **Deleting an assistant.** Not in the build-plan line, and every other domain model in this
  project treats "remove access to something" as a status/membership change, never a hard delete
  of the resource itself once real data (calls, versions) could point at it - `archived` is the
  correct verb here, matching `Organization.status`'s `suspended` precedent.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Model, migration, exceptions, permission** - `app/models/assistant.py`
  (`Assistant`, matching `Workspace`'s exact structure); Alembic migration creating `assistants`
  (indexed `organization_id`/`workspace_id`, the status CHECK constraint, `ondelete="CASCADE"` on
  both FKs matching `Workspace`'s own cascade behavior); `AssistantError`/`AssistantNotFound` in
  `app/core/exceptions.py` (matching `WorkspaceError`/`WorkspaceNotFound`); `MANAGE_ASSISTANTS` in
  `app/core/permissions.py`, added to the `_ELEVATED` frozenset; `CanManageAssistants` in
  `app/api/org_deps.py` alongside `CanManageWorkspaces`.
  *Done when:* `alembic upgrade head` applies cleanly from the current head (`93adf6abe167`) and
  `alembic downgrade -1` reverses cleanly; `ruff check apps/api` clean; no route or test yet, so
  no behavioral done-when - this step only needs to import cleanly and migrate cleanly.

- [x] **Step 2 - Repository and service** - `app/repositories/assistant.py` (`create`,
  `get_by_id`, `list_for_workspace`, `update_name`, `archive`); `app/services/assistant.py`
  enforcing workspace scope (every operation takes `organization_id` + `workspace_id` and raises
  `WorkspaceNotFound` if the workspace doesn't belong to that organization, `AssistantNotFound` if
  the assistant doesn't belong to that workspace - matching `workspace_service`'s exact
  double-check pattern for nested resources). `archive` is idempotent: archiving an
  already-archived assistant succeeds without error.
  *Done when:* `ruff check apps/api` clean. No test yet - this logic is only exercised through the
  route layer in this codebase's established convention (no direct-repository test files exist
  for `organization`/`workspace`), so Step 3 is where it gets proven.

- [x] **Step 3 - Schemas, routes, and tests** - `app/schemas/assistant.py`
  (`AssistantCreate{name}`, `AssistantUpdate{name}`, `AssistantResponse{id, organization_id,
  workspace_id, name, status, created_at}`); `app/api/v1/assistants.py` nested under
  `/organizations/{organization_id}/workspaces/{workspace_id}/assistants`, matching
  `workspaces.py`'s exact URL-nesting and dependency style; registered in `main.py`.
  *Done when:* `pytest apps/api/tests/test_assistants.py` (new) passes, proving: create/list/get/
  rename/archive all succeed for an owner; a member can list and get but gets 403 on
  create/rename/archive; an unauthenticated request gets 401; renaming or archiving a
  non-existent assistant gets 404; **an assistant in workspace A is invisible and inaccessible
  (404, not 403) through workspace B's URL, even within the same organization**; an assistant in
  organization A is invisible through organization B entirely; archiving twice succeeds both
  times; a workspace with no assistants yet returns an empty list, not an error. Full backend
  suite still green. `ruff check apps/api` clean.

## Files / areas

**New**
- `apps/api/app/models/assistant.py`
- `apps/api/alembic/versions/e196da966c0e_assistants.py`
- `apps/api/app/repositories/assistant.py`
- `apps/api/app/services/assistant.py`
- `apps/api/app/schemas/assistant.py`
- `apps/api/app/api/v1/assistants.py`
- `apps/api/tests/test_assistants.py`

**Modified**
- `apps/api/app/core/exceptions.py` - adds `AssistantError`/`AssistantNotFound`.
- `apps/api/app/core/permissions.py` - adds `MANAGE_ASSISTANTS`.
- `apps/api/app/api/org_deps.py` - adds `CanManageAssistants`.
- `apps/api/app/main.py` - registers the assistants router.
- `apps/api/app/db/base.py` - registers `Assistant` for Alembic autogenerate.

**Unchanged**
- No frontend file. No `AssistantVersion`. No `Assistant.current_version_id` - that's 11c's
  additive column.

## Data / contracts

**`Assistant`** - `id` (UUID PK), `organization_id` (UUID FK, indexed, CASCADE), `workspace_id`
(UUID FK, indexed, CASCADE), `name` (text, NOT NULL), `status` (text, CHECK `draft`/`published`/
`archived`, default `draft`), `created_at`/`updated_at`. **Locked for 11b/11c**: 11b adds
configuration columns directly to this table or a new one (11b's own spec decides which, once
this table exists to react to); 11c adds `current_version_id` (UUID, FK -> `assistant_versions`,
nullable) as an additive column. Neither sub-feature should need to alter anything defined here.

**`POST/GET/PATCH /organizations/{organization_id}/workspaces/{workspace_id}/assistants[/...]`**
- request/response bodies as in Step 3 above. No pagination on list - matching every other list
endpoint in this codebase so far (organizations, workspaces); revisit only if a workspace's
assistant count ever makes that wrong.

## Testing

The backend gate is live - every step ships its tests in the same diff where the logic becomes
observable (Step 3, per this codebase's established convention of testing repository/service
logic through the route layer rather than direct repository test files).

**In-scope logic needing tests:** all of Step 3's done-when list above. The workspace-scoping and
cross-tenant checks are the highest-value tests here - `workspace_service`'s established
double-check pattern (workspace belongs to org, resource belongs to workspace) is exactly the kind
of logic that silently breaks if a future edit swaps an `==` or drops a filter, per
CLAUDE.md's tenant-isolation testing mandate for every new scoped model.

**No frontend tests** - no frontend code in this sub-feature.

## Notes for the AI

- **Match `workspaces.py`/`workspace_deps.py`/`org_deps.py` exactly**, not a theoretically cleaner
  variant. This project's layering (route -> schema -> service -> repository -> database) and its
  read-vs-mutate dependency split (`CurrentWorkspace` for reads, `CanManageX` + service-layer
  scope validation for mutations) are already proven; deviating here would be the "risky missing
  abstraction" CLAUDE.md warns against, not an improvement.
- **`404`, not `403`, for cross-workspace/cross-org access.** Matches `_NOT_FOUND`'s documented
  reasoning in `workspace_deps.py`: telling "doesn't exist" apart from "exists but you can't see
  it" lets a caller probe which ids are real. Don't special-case assistants to leak that.
- **Every scoped model gets cross-tenant negative tests.** This is CLAUDE.md section 6.3's
  standing requirement, not something specific to this feature - don't treat it as optional
  because the feature itself feels small.
- **Don't add `current_version_id`, configuration fields, or anything UI-related.** If a step
  seems to need one of those, the step has drifted past 11a's scope into 11b/11c/11d.
- Continuing straight through 11b, 11c, and 11d after this one completes, per the "entire step 11
  in a single go" instruction - each still gets its own spec, branch, review, and merge.
