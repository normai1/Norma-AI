# Feature: Tab shell and field relocation

**From build-plan:** feature 23a

**Status:** complete

## Goal

Reorganize the assistant editor from one long scrolling page into four tabs - General, Knowledge,
Custom Prompt, Technical - matching the structure the user asked for, using Norma's own existing
dark-theme design system throughout (CLAUDE.md section 35: this is an independent product: no
third-party visual assets, styling, or copy are pulled in - only the tab-naming structure is
referenced). This sub-feature builds the tab shell itself and relocates every field that already
exists today into its correct new home. It does not add any new functionality: Knowledge and
Custom Prompt start as placeholder panels ("coming soon"), filled in by 23d/23e and 23c
respectively. Technical's new call/recording/ambient-sound fields are 23b's job, not this one's.

## Design reference

None. No mockup exists; this reuses `components/organizations/ui.tsx`'s existing `PageShell`/
`Card`/`Button` primitives and dark palette throughout - the only new visual element is the tab
bar itself, styled to match.

## Architecture decisions (read before building)

- **Tabs are local component state, not URL-addressable routes.** `activeTab` lives in
  `useState` on the editor page, defaulting to `"general"`.
- **One version-save action, shared across General and Technical.** `AssistantVersion` is an
  immutable configuration snapshot - voice/language/greeting/persona (General) and
  speech_rate/turn_sensitivity/creativity/ambient_sound (Technical) are all columns on the *same*
  version row, saved together in one `POST .../versions` call. Splitting them into two tabs is a
  **display-only** change: every field's React state stays exactly where it already lives in the
  parent component, so switching tabs never loses unsaved input in the other tab. The "Save
  version" button appears on both General and Technical.
- **Identity (rename) and Version history/publish stay outside the tabs, unchanged.**
- **Glossary moves into the Technical tab with a label change only.** The card heading becomes
  "Technical Terms," but the `GlossaryEntry` model, API routes, and every internal identifier stay
  named `glossary`/`GlossaryEntry` unchanged.
- **Two build steps to keep each diff reviewable.** Step 1 wraps the entire existing Configuration
  content into a "General" tab unchanged, alongside three placeholder tabs. Step 2 moves
  speech_rate/turn_sensitivity/creativity/ambient_sound and the Glossary section into Technical.

## In scope

- **`apps/web/components/organizations/ui.tsx`** - a new `Tabs` primitive (`items: readonly
  {key, label}[]`, `activeKey`, `onChange`), styled to match the existing dark palette.
- **`apps/web/app/(app)/assistants/[assistantId]/page.tsx`** - `activeTab` state; a four-tab
  layout (General, Knowledge, Custom Prompt, Technical); General tab (name/voice/language/
  greeting/persona); Technical tab (speech_rate/turn_sensitivity/creativity/ambient_sound +
  Glossary section, renamed "Technical Terms"); Knowledge and Custom Prompt placeholder panels.

## Out of scope

- Any new field, backend column, or migration.
- Knowledge tab content (23d/23e), Custom Prompt tab content (23c), Technical's new
  call-duration/recording/ambient-sound-preset fields (23b).
- Renaming the Glossary backend concept.
- URL-addressable or deep-linkable tabs.

## Build steps

- [x] **Step 1 - tab shell: General (unchanged content) + three placeholders** - built as specced.
  `npm run build`/`npm run lint` green.

- [x] **Step 2 - relocate Technical's real content out of General** - built as specced.
  `npm run build`/`npm run lint` green.

## Files / areas

**Modified**
- `apps/web/components/organizations/ui.tsx` (`Tabs`)
- `apps/web/app/(app)/assistants/[assistantId]/page.tsx` (tab shell, field relocation)

**Unchanged**
- No backend file. `GlossaryEntry` model, API routes, `lib/glossary.ts` - reused exactly as-is.

## Data / contracts

None new. No API shape changes.

## Testing

Rode on `npm run build`/`npm run lint` (both green, including a full TypeScript pass across every
route) plus a real interactive manual verification: a temporary Playwright script (deleted after
use, per this spec's Testing section - not a permanent addition to the suite) registered a user,
created an org/workspace/assistant via the real API, injected tokens, and drove the actual editor:
confirmed all four tabs render, General shows only its fields (no `#speech_rate` present) and
saves a version successfully, Knowledge/Custom Prompt show their placeholders, Technical shows the
relocated fields plus "Technical Terms" with working glossary CRUD (added an entry, it appeared),
saving from Technical also persists correctly, and General's saved values survive a tab switch.

## Notes for the AI

- Every field's `useState` stays exactly where it is in the parent component - only the JSX
  *rendering* is conditional on `activeTab`.
- The Glossary section's internal logic is unchanged - only its containing card's visible heading
  text changed from "Glossary" to "Technical Terms."
- **A real bug was found and fixed during Step 1**: the `Tabs` primitive's `items` prop was typed
  as a mutable array, but `EDITOR_TABS` (built with `as const`) is a readonly tuple - TypeScript
  rejected the assignment at build time. Fixed by typing `items` as `readonly {key, label}[]`.
- **The `norma-web` docker container needed a manual `--force-recreate` restart after Step 2's
  edits** - its dev-server HMR did not reliably pick up the changes on its own (the same class of
  stale-container issue seen repeatedly earlier in this session for `norma-api`/`norma-voice`).
  Confirmed via a failing manual check (`#speech_rate` still found on the General tab) before the
  restart, and a passing one after - always force-recreate `norma-web` before trusting a manual
  `/check` result if it was not already freshly restarted after the edit.
- **Two selector-ambiguity failures during manual verification were bugs in the throwaway check
  script, not the product** (`getByText("Configuration")` and `getByText("Technical Terms")` both
  matched unintended substrings elsewhere on the page). Fixed by switching to
  `getByRole("heading", {name: ...})`, which is the safer default for asserting a section header
  exists when the same words may appear elsewhere in body copy.

## Findings

None recorded against this feature; the ledger's outstanding entries all predate it and are
unrelated.
