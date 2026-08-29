# Feature: Prompt template editor UI

**From build-plan:** feature 12c
**Status:** not started

## Goal

The frontend for 12a/12b's prompt template backend: a top-level `/prompt-templates` list and
a `/prompt-templates/[id]` editor - identity (rename, archive), content, saving a new version,
and version history with publish/rollback and diff. Mirrors 11d's assistant editor UI closely;
completes build-plan item 12 entirely.

## Design reference

None. Follows the established `PageShell`/`Card`/`LoadingState`/`EmptyState`/`ErrorText`/
`Button` primitives and the exact list/detail patterns 11d already built for `/assistants`.

## In scope

- **Routes are top-level, context-driven** (`/prompt-templates`, `/prompt-templates/
  [promptTemplateId]`), matching `/assistants`'s own precedent - `useTenant()` supplies
  `activeOrganization`/`activeWorkspace`, not URL path params.
- **`Prompt Templates` added to the shell's left nav** (`lib/navigation.ts`), matching the
  moment 11d added `Assistants` - this is the point where it becomes a real, built feature.
- **`/prompt-templates`** - list of templates in the active workspace (name, use case, status
  badge), a create form (name + use case). Empty/loading/error states matching every other
  list page.
- **`/prompt-templates/[promptTemplateId]`** - the editor:
  - Identity: rename (12a's `PATCH`), archive (12a's `POST .../archive`). `use_case` is
    displayed but not editable - there is no backend endpoint for it (12a's
    `PromptTemplateUpdate` only has `name`), so the UI does not offer what the API can't do.
  - Content form: a single required textarea for `content` (12a's one config field - no
    simple/advanced split needed, unlike the assistant editor's eight fields). "Save as new
    version" posts the full text as a new `PromptVersion` - always a full snapshot, never a
    partial update.
  - Version history: list versions (12a's `GET .../versions`), a "Publish" action per version
    (12a's `POST .../publish` - publishing an older version *is* the rollback UI), and a diff
    view between any two selected versions (12a's `GET .../diff` - the one diffable field is
    `content`).
- Loading/empty/error states throughout, matching the established primitives.

## Out of scope

- **Any cross-link from the assistant editor to pick a prompt template.** 12b locked
  `AssistantVersion.prompt_template_id`/`prompt_version` as backend fields only; wiring a
  picker into the assistant editor is a separate UI decision for a later feature, not implied
  by this build-plan line.
- **A use-case picker with a fixed dropdown of the six named examples.** 12a deliberately kept
  `use_case` a free-form string; the create form is a plain text input, not a closed select.
- **Un-archiving, un-publishing.** Neither exists in the backend (12a's own deferrals).
- **Bulk actions, search, or filtering the list.** Same scope precedent as `/assistants`.
- **Real-time/optimistic UI beyond a simple refetch-after-mutation.** Same precedent as 11d.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `lib/prompt-templates.ts`** - `PromptTemplate`, `PromptVersion`,
  `PromptVersionDiff` types and `listPromptTemplates`, `getPromptTemplate`,
  `createPromptTemplate`, `renamePromptTemplate`, `archivePromptTemplate`,
  `publishPromptTemplate`, `listPromptTemplateVersions`, `createPromptTemplateVersion`,
  `diffPromptTemplateVersions` - matching `lib/assistants.ts`'s exact shape via
  `authorizedJson`.
  *Done when:* `npx tsc --noEmit` passes; `npm run lint` clean.

- [x] **Step 2 - `/prompt-templates` list page + nav entry** - `lib/navigation.ts` gets the
  `Prompt Templates` entry; `app/(app)/prompt-templates/page.tsx` uses `useTenant()`, fetches
  on mount with the cancelled-guard shape, renders a create form (name + use case) + list with
  status badges; an empty-state when there is no active workspace yet.
  *Done when:* `npm run build` succeeds; a manual/Playwright browser check confirms: the nav
  shows "Prompt Templates"; creating a template makes it appear in the list; switching
  workspaces shows that workspace's own templates.

- [x] **Step 3 - `/prompt-templates/[promptTemplateId]` editor: identity** - fetch the one
  template, rename control, archive control with a clear "archived" state, `use_case` shown
  read-only.
  *Done when:* `npm run build` succeeds; a manual/Playwright check confirms: opening the page
  shows the template's name, use case, and status; renaming updates the name; archiving
  updates the status.

- [x] **Step 4 - Content form: save a version** - a single required textarea for `content`;
  "Save as new version" posts it via `createPromptTemplateVersion` and shows a success
  indicator.
  *Done when:* `npm run build` succeeds; a manual/Playwright check confirms saving a version
  succeeds.

- [x] **Step 5 - Version history: list, publish/rollback, diff** - a version list (version
  number, created date), a "Publish" button per version, a two-version-picker diff view.
  *Done when:* `npm run build` succeeds; a manual/Playwright check confirms: saving two
  versions and publishing each, then rolling back, correctly updates which version shows as
  current; diffing two versions shows the changed `content` field.

- [x] **Step 6 - Full verification** - confirm nothing regressed.
  *Done when:* full backend `pytest` passes (nothing here touches the backend); `npm run lint`,
  `npm run test`, `npm run build`, and `npx playwright test` all pass; a manual walkthrough of
  the full flow (create template -> set content -> save version -> publish -> save a second
  version -> roll back -> diff) confirms end to end.

## Files / areas

**New**
- `apps/web/lib/prompt-templates.ts`
- `apps/web/app/(app)/prompt-templates/page.tsx`
- `apps/web/app/(app)/prompt-templates/[promptTemplateId]/page.tsx`

**Modified**
- `apps/web/lib/navigation.ts` - adds the `Prompt Templates` nav entry.

**Unchanged**
- No backend file. No new API contract - 12a already locked every shape this UI consumes.

## Data / contracts

Nothing new is locked here - purely a consumer of 12a's already-locked API contracts.

## Testing

No backend test command applies here (frontend-only feature). Verified by `npm run build` plus
manual/Playwright checks per step, matching 11d's precedent. `lib/prompt-templates.ts` is a thin
fetch wrapper with no branching logic - no dedicated unit test, matching `lib/assistants.ts`'s
own precedent.

## Notes for the AI

- **Copy 11d's exact patterns.** The list page, identity step, and version-history step are
  structurally identical to `/assistants`'s equivalents - reuse the same fetch/apply/
  `cancelled`-guard mount-effect shape, the same render-body "reset state on prop change"
  pattern (not a bare `setState` inside the effect body), and the same local status-badge
  component per page.
- **Only one config field.** Unlike the assistant editor's eight-field simple/advanced split,
  the content form here is a single textarea - do not invent additional fields or a disclosure
  that has nothing to hide behind it.
- **`use_case` is read-only in this UI.** There is no backend endpoint to change it after
  creation (12a's own scope); do not add one here either.
- **Publish is rollback.** Same precedent as 11d - one "Publish" action per version.
- **Don't add nav entries, routes, or fields beyond what's listed here.**
