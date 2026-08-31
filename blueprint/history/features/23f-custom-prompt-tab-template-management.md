# Feature: Custom Prompt tab - full template management

**From build-plan:** feature 23f

**Status:** complete

## Goal

Relocate prompt-template management (create, rename, archive, edit content + save new version,
version history, publish, compare/diff) from the standalone `/prompt-templates` pages into the
assistant editor's Custom Prompt tab, and remove the standalone pages and their nav link. Prompt
templates stay exactly what they are today - a shared, workspace-scoped, reusable data model
(`lib/prompt-templates.ts`'s API surface is unchanged; this is a UI relocation only).

## Design reference

None new. Reuses this same page's own established patterns: the manual-FAQ "expand a row to manage
it" interaction from 23e, the fetch/apply/error/reload triple already used by Glossary and the
existing Custom Prompt picker, and the existing local-badge-component precedent
(`AssistantStatusBadge`, `KnowledgeSourceStatusBadge`).

## Architecture decisions (read before building)

- **The existing "which template + version does this assistant version use" picker is untouched
  and stays load-bearing.** It lives inside `handleSaveVersion`'s form (`selectedPromptTemplateId`/
  `selectedPromptVersion` posted as part of `AssistantVersionInput`) - this relocation adds a
  separate "Manage templates" section alongside it, and must not nest new CRUD forms inside that
  same `<form>` or otherwise disturb its submit wiring.
- **New CRUD forms are independent, matching the Glossary/Knowledge-tab precedent** - their own
  `<form>` elements with their own submit handlers and their own loading/error state, sitting next
  to (not inside) the assistant's own save-version form.
- **Per-template management uses the same expand/collapse row pattern as 23e's FAQ entries** - a
  "Manage" toggle per template row reveals identity (rename/archive), content + save-new-version,
  version history + publish, and compare/diff, all scoped to that one expanded template.
- **The management list and the existing picker's dropdown share the same `promptTemplates`
  state** - one fetch, and every CRUD action (create/rename/archive/publish) refreshes it so the
  picker's own dropdown options stay in sync automatically, matching how 23e's
  `refreshKnowledgeSources()` served both the list display and the picker-equivalent affordances.
- **Reuse the existing `formatDiffValue` function already defined in this file** (from the
  assistant's own version-diff feature) rather than defining a duplicate - confirmed identical in
  purpose (render `null`/`undefined` as `"(none)"`, else `String(value)`) by reading both call
  sites. Add a new `PromptTemplateStatusBadge` local component, matching the
  `AssistantStatusBadge`/`KnowledgeSourceStatusBadge` precedent (do not stretch a shared badge).
- **Do not port the old list page's synchronous reset-on-workspace-change pattern** (setState
  during render rather than inside a fetch's `.then()`/`.catch()`) - this codebase's own established
  convention (confirmed by every existing effect in this file) only sets state inside `.then()`/
  `.catch()`, never synchronously in an effect body; the new fetch effect must follow that.
- **No backend or `lib/prompt-templates.ts` changes.** Every function this feature needs
  (`listPromptTemplates`, `getPromptTemplate`, `createPromptTemplate`, `renamePromptTemplate`,
  `archivePromptTemplate`, `publishPromptTemplate`, `listPromptTemplateVersions`,
  `createPromptTemplateVersion`, `diffPromptTemplateVersions`) already exists with the exact shape
  needed - only new call sites in the assistant editor.
- **Four build steps**, ordered so each leaves the app working; the old pages stay functional
  (just unlinked from navigation after Step 1) until Step 4 deletes them once nothing in the new UI
  still needs them as a reference:
  1. Nav link removed, standalone list page deleted; "Manage templates" section added to Custom
     Prompt tab with create/list/rename/archive (the identity-level actions).
  2. Content editing (save-new-version) + version history + publish, inside the same expanded row.
  3. Compare/diff between two versions, inside the same expanded row.
  4. Delete the standalone detail page (`/prompt-templates/[promptTemplateId]/page.tsx`) - by this
     point nothing links to it and every capability it had now lives in the tab.

## In scope

- **`apps/web/lib/navigation.ts`** - remove the `{ href: "/prompt-templates", label: "Prompt
  Templates" }` entry.
- **`apps/web/app/(app)/prompt-templates/page.tsx`** - deleted (Step 1).
- **`apps/web/app/(app)/prompt-templates/[promptTemplateId]/page.tsx`** - deleted (Step 4).
- **`apps/web/app/(app)/assistants/[assistantId]/page.tsx`** - Custom Prompt tab gains a "Manage
  templates" section:
  - Create-template form (name + use_case).
  - List of all templates (name, use_case, `PromptTemplateStatusBadge`), each with a "Manage"/
    "Hide" toggle.
  - Expanded row: rename form, archive button (disabled once archived); content textarea + "Save
    as new version"; version history list with a "Current" badge or "Publish" button per version;
    compare-versions (From/To selects, "Show diff", a diff table reusing `formatDiffValue`).

## Out of scope

- **Any change to `lib/prompt-templates.ts` or the backend API.** Pure UI relocation.
- **Any change to the existing picker's own behavior or its `handleSaveVersion` wiring.**
- **Deleting a prompt template.** The old pages never had this (only archive); not adding it here
  either - matches the existing backend's own capability exactly.
- **Changing prompt templates from shared to assistant-specific.** Explicitly confirmed with the
  user to stay a shared, workspace-scoped, reusable model.

## Build steps

- [x] **Step 1 - nav removal, list page relocation, identity actions**
  - `lib/navigation.ts`: remove the Prompt Templates entry.
  - Delete `app/(app)/prompt-templates/page.tsx`.
  - Custom Prompt tab: new "Manage templates" section - create-template form, template list
    (reusing/extending the existing lazy-fetched `promptTemplates` state), expand-to-manage toggle
    per row, rename form, archive button. New `PromptTemplateStatusBadge` local component.
  *Done when:* `npm run build` passes; a temporary Playwright check creates a template through the
  new UI, confirms it also appears in the existing picker's dropdown, renames it, and archives it.

- [x] **Step 2 - content editing, version history, publish**
  - Expanded row gains: content textarea + "Save as new version" (`createPromptTemplateVersion`);
    version history list (`listPromptTemplateVersions`); "Publish" per non-current version
    (`publishPromptTemplate`) with a "Current" badge on the published one. A freshly created
    template has zero versions - the version-history list needs its own `EmptyState` ("This
    template has no saved versions yet."), matching the existing picker's own precedent for the
    identical state.
  *Done when:* `npm run build` passes; a temporary Playwright check saves a new version, publishes
  it, and confirms the picker's version dropdown (Section A) reflects the new version.

- [x] **Step 3 - compare/diff**
  - Expanded row gains: From/To version selects, "Show diff" button (`diffPromptTemplateVersions`),
    a diff table reusing the existing `formatDiffValue` function.
  *Done when:* `npm run build` passes; a temporary Playwright check creates two versions, diffs
  them, and confirms the table shows the changed field.

- [x] **Step 4 - delete the standalone detail page**
  - Delete `app/(app)/prompt-templates/[promptTemplateId]/page.tsx`.
  *Done when:* `npm run build` passes; `npm run lint` clean; visiting the old
  `/prompt-templates/[id]` URL directly now 404s (route no longer exists); a final temporary
  Playwright check confirms the full flow - create, edit, publish, diff, rename, archive - all
  work from inside the Custom Prompt tab alone.

## Files / areas

**Deleted**
- `apps/web/app/(app)/prompt-templates/page.tsx` (Step 1)
- `apps/web/app/(app)/prompt-templates/[promptTemplateId]/page.tsx` (Step 4)

**Modified**
- `apps/web/lib/navigation.ts`
- `apps/web/app/(app)/assistants/[assistantId]/page.tsx`

**Unchanged**
- `apps/web/lib/prompt-templates.ts` - no changes, only new call sites.
- `apps/api` - no backend changes; every route this feature needs already exists.

## Data / contracts

No new types or API shapes. Reuses `PromptTemplate`, `PromptTemplateVersion`,
`PromptTemplateVersionDiff` exactly as already defined in `lib/prompt-templates.ts`.

## Testing

Pure UI/integration relocation of already-working, already-tested-at-the-API-layer functionality;
no new pure logic worth a unit test (checked - `formatDiffValue` and the status badge are trivial
and already covered by precedent, not new branchy logic like 23e's retry/recrawl eligibility
helpers). Verified per step via `npm run build` plus a temporary Playwright check against the real
running stack (written to `apps/web/e2e/`, deleted after use, never committed). Final gate for
Step 4 additionally includes `npm run lint`.

Final gates: `npm run build` (pass), `npm run lint` (clean), `npm run test` (6 files, 47 tests, all
pass). Each of the four steps was additionally proven against the real running stack with a
temporary Playwright check, culminating in a full end-to-end flow (create, edit content, save two
versions, publish, diff, rename, archive) run entirely from inside the Custom Prompt tab, plus
confirmation that the old `/prompt-templates` and `/prompt-templates/[id]` URLs now 404 and the
nav link is gone.

## Notes for the AI

- **Read the existing Custom Prompt tab code and the two old page files in full before writing
  anything** - the fetch/apply/error/reload shape, the exact API function signatures, and the
  existing `formatDiffValue` function's exact location all need to be reused precisely, not
  reconstructed from memory.
- **The picker (Section A) must still work identically after every step** - re-verified in each
  step's manual check via a real Playwright check confirming the picker's dropdown/version list
  reflected new templates and versions correctly throughout.
- **Multiple label collisions surfaced during manual verification, all in the test code, not the
  app** - "Save name", "Save as new version", and "Publish" each appear in both the existing
  assistant-level UI (or the picker) and the new template-management UI. Every temporary check had
  to scope its locators to the specific expanded `<li>` row rather than using a bare
  `getByRole("button", { name: ... })`, since the labels are legitimately reused deliberately for
  UI consistency.
