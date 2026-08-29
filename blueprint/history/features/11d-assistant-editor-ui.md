# Feature: Assistant editor UI

**From build-plan:** feature 11d
**Status:** not started

## Goal

The frontend for everything 11a-11c built: a top-level `/assistants` list and an
`/assistants/[id]` editor - identity (rename, archive), configuration (simple fields visible by
default, advanced fields behind a disclosure - CLAUDE.md's own "progressive disclosure"
principle), saving a new version, and version history with publish/rollback and diff. This is
the first real UI for the assistant domain and completes build-plan item 11 entirely.

## Design reference

None. No mockups exist for this project; follows the established `PageShell`/`Card`/
`LoadingState`/`EmptyState`/`ErrorText`/`Button` primitives and list/detail patterns already used
by `/organizations` and `/settings`.

## In scope

- **Routes are top-level, context-driven** (`/assistants`, `/assistants/[assistantId]`), matching
  `project-overview.md`'s own Routes table and `/settings`'s established pattern - `useTenant()`
  supplies `activeOrganization`/`activeWorkspace`, not URL path params. This is different from
  the `/organizations/[organizationId]/workspaces/...` pages, which are specifically
  organization/workspace *management* screens and nest deliberately; `/assistants` is a
  day-to-day operator page, like `/settings`.
- **`Assistants` added to the shell's left nav** (`lib/navigation.ts`) - `project-overview.md`'s
  Core navigation list already names it, and this is exactly the point where it becomes a real,
  built feature. (Unlike item 10's `/voices`, which deliberately stayed nav-less because it's a
  future editor *section*, not a destination of its own.)
- **`/assistants`** - list of assistants in the active workspace (name, status badge), a create
  form. Empty/loading/error states matching every other list page. If there is no active
  workspace yet, a clear "create a workspace first" empty state, not a broken fetch.
- **`/assistants/[assistantId]`** - the editor:
  - Identity: rename (11a's `PATCH`), archive (11a's `POST .../archive`).
  - Configuration form, all 11b fields: **simple** (always visible) - voice (a real `<select>`
    populated from item 10's `GET /api/v1/voices`, not a free-text field - "the more natural
    place to prevent an invalid voice_id at the source," per 11b's own spec), language, greeting,
    persona. **Advanced** (behind a disclosure, collapsed by default) - speech rate, turn
    sensitivity, creativity, ambient sound.
  - "Save as new version" - `POST .../versions` (11b), a full snapshot every time, never a
    partial update (versions are immutable).
  - Version history: list versions (11b's `GET .../versions`), a "Publish" action per version
    (11c's `POST .../publish` - publishing an older version *is* the rollback UI, no separate
    control), and a diff view between any two selected versions (11c's `GET .../diff`).
- Loading/empty/error states throughout, matching the established primitives.

## Out of scope

- **The live test call panel.** `project-overview.md`'s own Assistant Editor description calls
  for "configuration on the left, a live test call on the right" - the right side needs item 21
  (in-browser test call), which needs item 20 (the voice engine), neither of which exists yet.
  This feature builds the left side only. Flagging this explicitly rather than silently building
  half the described editor and calling it done.
- **`greeting_interruptible`, `business_hours_behavior`, `fallback_behavior`, `enabled_skills`,
  `prompt_template_id`/`prompt_version`.** Still not built anywhere in the backend (11b's own
  deferral) - nothing for this UI to show.
- **Un-archiving / restoring, un-publishing.** Neither exists in the backend (11a/11c's own
  deferrals); the UI won't offer what the API can't do.
- **Bulk actions, search, or filtering the assistants list.** Not asked for; a workspace's
  assistant count is small for the foreseeable future, matching every other list page's current
  scope.
- **Real-time/optimistic UI beyond a simple refetch-after-mutation.** Every action (rename,
  archive, save version, publish) reloads via the existing fetch pattern afterward - no
  client-side cache invalidation library, matching every other page in this codebase.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `lib/assistants.ts`** - `Assistant`, `AssistantVersion`, `AssistantVersionDiff`
  types (snake_case fields, matching every other frontend type) and `listAssistants`,
  `getAssistant`, `createAssistant`, `renameAssistant`, `archiveAssistant`, `publishAssistant`,
  `listAssistantVersions`, `createAssistantVersion`, `diffAssistantVersions` - all taking
  `organizationId`/`workspaceId` (and `assistantId`/version numbers where relevant) as explicit
  parameters, matching `lib/workspaces.ts`'s exact shape, via `authorizedJson`/`authorizedEmpty`.
  *Done when:* `npx tsc --noEmit` passes; `npm run lint` clean.

- [x] **Step 2 - `/assistants` list page + nav entry** - `lib/navigation.ts` gets the
  `Assistants` entry; `app/(app)/assistants/page.tsx` uses `useTenant()` for
  `activeOrganization`/`activeWorkspace`, fetches on mount using the fetch-then-`.then()`-apply-
  with-`cancelled`-guard shape this codebase's own lint fix established, re-fetching when
  `activeWorkspace?.id` changes; renders create form + list with status badges; an empty-state
  when there is no active workspace yet, distinct from the empty-state for "workspace has no
  assistants."
  *Done when:* `npm run build` succeeds; a manual/Playwright browser check confirms: the nav
  shows "Assistants"; creating an assistant makes it appear in the list; switching workspaces via
  the shell switcher shows that workspace's own assistants, not the previous one's.

- [x] **Step 3 - `/assistants/[assistantId]` editor: identity** - fetch the one assistant
  (context-driven org/workspace, `assistantId` from the route), the fetch/apply/`cancelled`
  shape again; rename control (11a's `PATCH`); archive control (11a's `POST .../archive`) with a
  clear "archived" state once done. No configuration form yet - this step only proves the page
  itself, the fetch, and the two identity actions work before the bigger form goes on top of it.
  *Done when:* `npm run build` succeeds; a manual/Playwright browser check confirms: opening the
  page shows the assistant's name and status; renaming updates the displayed name; archiving
  updates the displayed status.

- [x] **Step 4 - Configuration form: save a version** - the simple/advanced config form (voice
  `<select>` populated from `listVoices()` (item 10), language, greeting, persona always visible;
  speech rate, turn sensitivity, creativity, ambient sound behind a collapsed "Advanced"
  disclosure); "Save as new version" posts the full form as a new `AssistantVersion` and shows a
  success indicator. No version history/publish/diff yet - that's Step 5, once saving itself is
  proven.
  *Done when:* `npm run build` succeeds; a manual/Playwright browser check confirms: the voice
  select is populated (or shows a clear "no voices available" state when the catalogue is
  empty - the real state for the default mock provider); saving a version succeeds.

- [x] **Step 5 - Version history: list, publish/rollback, diff** - a version list on the editor
  page (version number, created date), a "Publish" button per version, and a two-version-picker
  diff view rendering only the changed fields (11c's response shape).
  *Done when:* `npm run build` succeeds; a manual/Playwright browser check confirms: saving two
  versions and publishing the first, then the second, then rolling back to the first (publishing
  it again) all correctly update which version shows as current; selecting two versions for diff
  shows exactly the fields that differ between them.

- [x] **Step 6 - Full verification** - confirm nothing regressed.
  *Done when:* full backend `pytest` passes (nothing here should have touched the backend, but
  confirm); `npm run lint`, `npm run test`, `npm run build`, and `npx playwright test` all pass;
  a manual walkthrough of the full flow (create assistant -> configure -> save version ->
  publish -> save a second version -> roll back -> diff) confirms end to end.

## Files / areas

**New**
- `apps/web/lib/assistants.ts`
- `apps/web/app/(app)/assistants/page.tsx`
- `apps/web/app/(app)/assistants/[assistantId]/page.tsx`

**Modified**
- `apps/web/lib/navigation.ts` - adds the `Assistants` nav entry.

**Unchanged**
- No backend file. No new API contract - 11a/11b/11c already locked every shape this UI
  consumes. `lib/voices.ts` is reused as-is, not modified.

## Data / contracts

Nothing new is locked here - this feature is purely a consumer of 11a/11b/11c's already-locked
API contracts and item 10's `listVoices()`. If a step seems to need a new backend field or route,
the backend spec was incomplete, not this one - stop and say so rather than improvising an API
change inside a frontend feature.

## Testing

No backend test command applies here (frontend-only feature). Per `coding-standards.md`'s
testing scope rule, this is UI/integration surface, not pure logic - verified by `npm run build`
plus manual/Playwright checks per step, not unit tests. `lib/assistants.ts` (Step 1) is a thin
fetch wrapper with no branching logic, matching `lib/organizations.ts`/`lib/voices.ts`'s own
precedent of no dedicated unit test for that class of file.

## Notes for the AI

- **Routes are top-level and context-driven, not nested under
  `/organizations/[id]/workspaces/[id]`.** Get the organization/workspace ids from `useTenant()`,
  not the URL. This matches `/settings`, not the workspace-management pages.
- **Reuse the fetch/apply/`cancelled`-guard mount-effect shape** established by this session's
  own lint fix (`blueprint/history/fixes/set-state-in-effect-lint.md`) for every list/detail
  fetch in this feature. Don't write `useEffect(() => { load(); }, [load])` where `load` itself
  sets state.
- **The voice picker is a real `<select>` from item 10's catalogue, not a text input.** This is
  exactly the validation-at-the-source item 10's own spec flagged as 11d's job.
- **A version save is always a full snapshot.** The form should submit every field every time,
  matching 11b's `AssistantVersionCreate` - there is no partial-update version endpoint, and
  there never will be (11c's immutability guarantee depends on that staying true).
- **Publish is rollback.** One "Publish" action per version in the list; publishing an older
  version than current is how rollback works in this UI - don't build a second "rollback" button
  that does the same thing.
- **No live test call panel.** See Out of scope - don't attempt to stub one in; leave that
  region of the editor absent until items 20/21 exist, rather than building a fake placeholder.
- **Don't add nav entries, routes, or fields beyond what's listed here.** If a step seems to
  need one, it has drifted past 11d's scope - this feature completes item 11, not item 12+.
