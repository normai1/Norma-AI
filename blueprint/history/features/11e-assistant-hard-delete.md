# Feature: Assistant hard delete

**From build-plan:** feature 11e

**Status:** complete

## Goal

Add a real, irreversible delete for an `Assistant` - today only `POST .../archive` exists (a
reversible status flip). Confirmed with the user: this is genuine hard deletion, not a relabel of
archive, cascading to everything the assistant owns, with a destructive-action confirmation in the
UI. Archive stays exactly as it is today, as the separate, safer, reversible option.

## Design reference

None. Backend route + one new UI button/confirmation, matching existing patterns throughout this
page (Archive button, and every other hard-delete route already in this codebase, e.g. FAQ entry
delete, glossary entry delete - 204 No Content, `db.delete(...)` + `db.flush()`).

## Architecture decisions (read before building)

- **The circular FK between `assistants.current_version_id` and
  `assistant_versions.assistant_id` was verified directly against the real dev database, not
  assumed.** `AssistantVersion.assistant_id` has `ondelete="CASCADE"`; `Assistant.current_version_id`
  has no cascade action (defaults to `NO ACTION`). A live test (`BEGIN; ... DELETE FROM assistants
  WHERE id = :id; ... ROLLBACK;`) with `current_version_id` pointing at a real, live
  `assistant_versions` row confirmed the delete succeeds cleanly with zero rows left in either
  table and no FK violation - deleting the `assistants` row removes its own `current_version_id`
  value as part of the same operation, so there is nothing left afterward for the non-cascading FK
  to violate. No special delete ordering is needed at the application level.
- **Cascade coverage confirmed by reading each model directly**: `KnowledgeSource.assistant_id`,
  `Chunk.assistant_id`, and `GlossaryEntry.assistant_id` all already have `ondelete="CASCADE"`
  (added in 23d and item 13 respectively). A plain `DELETE FROM assistants WHERE id = ...` (via
  SQLAlchemy `await db.delete(assistant)`) removes the assistant's knowledge sources, their chunks,
  every `AssistantVersion` snapshot, and every glossary entry - no manual cleanup loop needed.
- **Known, accepted gap, not a regression this feature introduces**: cascading a `KnowledgeSource`
  row does not clean up its S3-backed `Document` object. This gap already exists platform-wide -
  no knowledge-source deletion capability of any kind exists yet (23e's own spec explicitly
  deferred it: "No backend route exists; not this feature's job to add one"). Assistant deletion
  inherits that same gap rather than being a new one; flagged here, not silently worked around.
- **Same permission level as `archive`** - `CanManageAssistants` (owners and admins), matching the
  existing archive route's own access rule exactly. No stricter gate invented.
- **Not idempotent, unlike archive.** Archiving an already-archived assistant is a documented
  no-op; deleting an already-deleted (i.e. nonexistent) assistant simply 404s via the existing
  `AssistantNotFound` → `_ASSISTANT_NOT_FOUND` mapping, matching every other resource's delete
  semantics in this codebase (e.g. FAQ entry delete, glossary entry delete).
- **UI confirmation is a native `window.confirm()`, not a new modal component.** No dialog/modal
  primitive exists anywhere in `components/organizations/ui.tsx` today; introducing one for a
  single destructive button would be over-engineering for what this needs. Matches this codebase's
  "boring, reliable engineering" preference.
- **On successful delete, redirect to `/assistants`** - the assistant no longer exists, so staying
  on its own now-404-ing detail page would be broken.
- **Two build steps**: backend (route/service/repo/tests) first and independently completable and
  testable via the API directly; frontend (button/confirm/redirect) second, consuming the now-real
  endpoint.

## In scope

- **`apps/api/app/repositories/assistant.py`** - new `delete(db, assistant) -> None`:
  `await db.delete(assistant); await db.flush()`.
- **`apps/api/app/services/assistant.py`** - new `delete_assistant(db, *, organization_id,
  workspace_id, assistant_id) -> None`: `resolve_assistant(...)` (existing tenant-scoped lookup,
  raises `AssistantNotFound`), then `assistant_repo.delete(db, assistant)`.
- **`apps/api/app/api/v1/assistants.py`** - new route:
  ```
  DELETE /organizations/{organization_id}/workspaces/{workspace_id}/assistants/{assistant_id}
  ```
  `CanManageAssistants`, catches `WorkspaceNotFound`/`AssistantNotFound`, `db.commit()`, returns
  `Response(status_code=204)` - mirrors `delete_glossary_entry`'s/`delete_faq_entry`'s exact shape.
- **`apps/api/tests/test_assistants.py`** - new tests: delete succeeds and the assistant is
  genuinely gone (a subsequent GET 404s); delete cascades (create a knowledge source + version +
  glossary entry first, delete the assistant, confirm all three are gone too - a real integration
  proof, not just trusting the FK); delete requires authentication; delete is forbidden for a
  member/viewer (matching the existing archive test's role-gating pattern); delete 404s for a
  nonexistent assistant; delete in one workspace is not reachable through a sibling workspace;
  delete in one organization is not reachable through another organization.
- **`apps/web/lib/assistants.ts`** - new `deleteAssistant(organizationId, workspaceId,
  assistantId): Promise<void>`, mirroring `archiveAssistant`'s shape but `DELETE` + no response
  body (`authorizedSend`, not `authorizedJson`, since 204 has no body).
- **`apps/web/app/(app)/assistants/[assistantId]/page.tsx`** - Identity card gains a "Delete
  assistant" button (`variant="danger"`, the existing unused danger variant) next to "Archive
  assistant". On click: `window.confirm(...)` → on confirm, call `deleteAssistant` → on success,
  `router.push("/assistants")`; on failure, surface via the existing `actionError` state (already
  shared with rename/archive errors on this page).

## Out of scope

- **Cleaning up S3-backed documents for cascade-deleted knowledge sources.** A pre-existing,
  platform-wide gap (no knowledge-source deletion exists at all yet); not this feature's job to
  close.
- **Any change to the existing `archive` action.** It stays exactly as it is - the separate,
  reversible option.
- **A generic confirmation-dialog component.** One native `window.confirm()` call, not a reusable
  primitive.
- **Deleting a workspace or organization.** Out of scope; this is assistant-level only.

## Build steps

- [x] **Step 1 - backend: route, service, repo, tests**
  - `apps/api/app/repositories/assistant.py`: `delete(db, assistant) -> None`.
  - `apps/api/app/services/assistant.py`: `delete_assistant(...)`.
  - `apps/api/app/api/v1/assistants.py`: `DELETE .../assistants/{assistant_id}` route, 204,
    `CanManageAssistants`.
  - New tests: 8 new tests, including the real cascade-verification test (seeded a knowledge
    source, version, and glossary entry, deleted the assistant, confirmed all three gone via
    direct queries, not just an assumption from the FK definition alone).
  *Done when:* `pytest apps/api/tests` passes (including the new tests); `ruff check apps/api`
  clean; a direct `curl -X DELETE` against the real dev API against a real throwaway assistant
  succeeds with 204 and a follow-up GET 404s.

- [x] **Step 2 - frontend: delete button, confirmation, redirect**
  - `apps/web/lib/assistants.ts`: `deleteAssistant(...)`.
  - Assistant editor page: "Delete assistant" button (danger variant) in the Identity card,
    `window.confirm()` guard, redirect to `/assistants` on success, error surfaced via the existing
    `actionError` state on failure.
  *Done when:* `npm run build` passes; `npm run lint` clean; a temporary Playwright check clicks
  Delete, confirms the browser dialog, and verifies the app lands on `/assistants` with the deleted
  assistant no longer listed.

## Files / areas

**Modified**
- `apps/api/app/repositories/assistant.py`, `app/services/assistant.py`,
  `app/api/v1/assistants.py`
- `apps/api/tests/test_assistants.py`
- `apps/web/lib/assistants.ts`
- `apps/web/app/(app)/assistants/[assistantId]/page.tsx`

**Unchanged**
- No migration - no schema change, only a new route/service/repo function using FK behavior that
  already exists.
- `apps/voice` - unaffected; no telephony/call data references assistants yet (items 24+ unbuilt).

## Data / contracts

No new types. `DELETE .../assistants/{assistant_id}` returns `204 No Content` on success, matching
every other hard-delete route in this codebase (FAQ entries, glossary entries).

## Testing

Real backend logic (a genuinely new capability, cascading data deletion) - got full pytest
coverage per `coding-standards.md`'s Testing gate: happy path, the real cascade proven via direct
DB queries (not trusted from the FK definition alone), auth/role gating matching the existing
archive test's pattern, not-found, and both standard cross-tenant negative tests. Frontend is a
button + confirm + redirect - verified via `npm run build` plus a temporary Playwright check
against the real running stack (written to `apps/web/e2e/`, deleted after use, never committed).

Final gates: full `apps/api` suite (619/619 passed, including all 8 new tests), `ruff check
apps/api` (clean), a real `curl -X DELETE` against the live dev API (204, confirmed gone via a
direct DB query afterward), `npm run build` (pass), `npm run lint` (clean), `npm run test` (6
files, 47 tests, all pass), and a temporary Playwright check confirming the full UI flow
(confirmation dialog → delete → redirect to `/assistants` → gone from the list → gone server-side).

## Notes for the AI

- **The circular-FK delete behavior was verified directly against the real dev database in a
  rolled-back transaction before this spec was written**, and re-confirmed by the real cascade
  test in Step 1 and the live curl check - this is not a theoretical concern, it's a proven-safe
  path.
- **Reused `resolve_assistant` and `CanManageAssistants`** - both already existed and already did
  exactly the tenant-scoping and permission-check work this feature needed; no reimplementation.
