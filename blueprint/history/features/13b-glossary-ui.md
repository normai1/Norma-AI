# Feature: Glossary UI

**From build-plan:** feature 13b
**Status:** not started

## Goal

The frontend for 13a's glossary backend: a "Glossary" section added to the existing
`/assistants/[assistantId]` editor page - list, add, inline edit, delete. Completes
build-plan item 13 entirely. No new route: the build-plan line itself calls for "a glossary
section on the assistant editor page," not a standalone list/detail pair like the assistant
and prompt-template editors got.

## Design reference

None. Follows the established `Card`/`Button`/`EmptyState`/`ErrorText`/`LoadingState`
primitives and the assistant editor's own existing Identity/Configuration/Version-history
Card layout - Glossary becomes a fourth Card on that same page.

## In scope

- **`lib/glossary.ts`** - `GlossaryEntry` type and `listGlossaryEntries`,
  `createGlossaryEntry`, `updateGlossaryEntry`, `deleteGlossaryEntry`, all taking
  `organizationId`/`workspaceId`/`assistantId` (and `glossaryEntryId` where relevant),
  matching `lib/assistants.ts`'s exact shape via `authorizedJson`/`authorizedEmpty` (`DELETE`
  returns `204`, so it uses `authorizedEmpty` the same way an existing precedent in this
  codebase's `lib/` handles a no-body response).
- **A "Glossary" `Card`** added to the assistant editor page, below the existing Version
  history card:
  - A list of entries: term, meaning, phonetic spelling, boost weight, each with an inline
    "Edit" and "Delete" control.
  - An "Add entry" form: term (required), meaning (optional), phonetic spelling (optional),
    boost weight (optional number input, 0.0-1.0, defaults to the backend's own default of
    0.5 when left blank).
  - Editing a row swaps that one row into the same field set, pre-filled, with "Save"/
    "Cancel" - no navigation, no modal, no separate route.
  - Deleting asks for no separate confirmation dialog (matching this codebase's existing
    "Archive" buttons, which also act immediately) but does show the resulting empty state
    when the list becomes empty.
  - Loading/empty/error states matching every other list section in this codebase.

## Out of scope

- **A standalone `/glossary` route or list/detail pair.** The build-plan line asks for a
  *section on the assistant editor*, not a new top-level destination - no nav entry either.
- **Any STT/TTS wiring, or a live preview of pronunciation.** 13a's own scope note: the
  provider interfaces don't support per-term weighting or pronunciation overrides yet: this
  is a plain CRUD UI over already-stored data.
- **Bulk import/export.** Not asked for.
- **A duplicate-term inline error beyond surfacing the backend's 409.** The "Add entry" form
  shows whatever error the API returns (including the 409 for a duplicate term) via the
  existing `ErrorText` pattern; it does not pre-validate uniqueness client-side.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `lib/glossary.ts`** - the type and four client functions.
  *Done when:* `npx tsc --noEmit` passes; `npm run lint` clean.

- [x] **Step 2 - Glossary section on the assistant editor** - the full list/add/edit/delete
  Card added to `app/(app)/assistants/[assistantId]/page.tsx`, fetched with the same
  fetch/apply/`cancelled`-guard mount-effect shape every other section on this page already
  uses, refetching after every mutation.
  *Done when:* `npm run build` succeeds; a manual/Playwright browser check confirms: adding an
  entry makes it appear in the list; editing a field and saving updates the displayed row;
  deleting removes it and an empty assistant shows the empty state.

- [x] **Step 3 - Full verification** - confirm nothing regressed.
  *Done when:* full backend `pytest` passes (nothing here should touch the backend, but
  confirm); `npm run lint`, `npm run test`, `npm run build`, and `npx playwright test` all
  pass; a manual walkthrough (add an entry -> edit it -> delete it) confirms end to end.

## Files / areas

**New**
- `apps/web/lib/glossary.ts`

**Modified**
- `apps/web/app/(app)/assistants/[assistantId]/page.tsx` - adds the Glossary Card.

**Unchanged**
- No backend file. No new API contract - 13a already locked every shape this UI consumes.
- No nav entry, no new route.

## Data / contracts

Nothing new is locked here - purely a consumer of 13a's already-locked API contracts.

## Testing

No backend test command applies here (frontend-only feature). Verified by `npm run build`
plus manual/Playwright checks per step, matching every other frontend sub-feature's precedent
this session. `lib/glossary.ts` is a thin fetch wrapper with no branching logic - no dedicated
unit test, matching `lib/assistants.ts`/`lib/prompt-templates.ts`'s own precedent.

## Notes for the AI

- **This is the last piece of build-plan item 12/13's combined scope.** Completing this
  sub-feature checks off 13b, and with 13a already done, item 13 (and with item 12 already
  complete, the whole "step 12 and step 13" instruction) is finished.
- **No new route, no nav entry.** The section lives on the existing assistant editor page.
- **Reuse the fetch/apply/`cancelled`-guard mount-effect shape** already used three times on
  this exact page (identity, configuration, version history) - do not introduce a different
  pattern for a fourth section.
- **Editing is inline, per row - no modal, no separate page.** A single `editingId` piece of
  state (or equivalent) toggles one row into its editable form at a time.
