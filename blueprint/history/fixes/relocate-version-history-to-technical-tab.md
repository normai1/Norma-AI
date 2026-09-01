# Fix: move assistant Version history into the Technical tab

**Type:** Fix

## The problem

The assistant's own "Version history" section (version list, "Publish" button, "Compare
versions"/diff) currently renders **unconditionally** below all four tabs in
`apps/web/app/(app)/assistants/[assistantId]/page.tsx` - right before the closing
`</PageShell>`, outside every `{activeTab === "..." && (...)}` block. It shows on General,
Knowledge, Custom Prompt, and Technical alike. The user wants General decluttered down to just
voice/language/greeting/persona (which it already is otherwise), and Version history moved
somewhere - specifically confirmed: into the Technical tab, so it only appears when Technical is
active.

**This section is the only place with a "Publish" button** - the only mechanism that makes a
saved draft `AssistantVersion` become the assistant's live `current_version_id`, the version a
real call would use. This is a pure relocation, not a removal - confirmed with the user.

## The fix

Move the JSX block (starting at the unconditional `<div className="mt-8"><Card><h2>Version
history</h2>...` through its closing `</Card></div>`, just before `</PageShell>`) into the
Technical tab's `{activeTab === "technical" && (...)}` block. No state, handler, or effect
changes - `versions`, `versionsError`, `publishingVersion`, `publishError`, `diffFrom`, `diffTo`,
`diffResult`, `diffError`, `diffLoading`, `handlePublish`, `handleDiff` all stay exactly as they
are; only where the JSX renders changes.

**Do not confuse this with the Custom Prompt tab's own "Version history" section** (added in 23f,
inside the per-template expanded row) - that one renders `templateVersions`/`PromptTemplateVersion`
rows and calls `handlePublishTemplateVersion`/`handleDiffTemplateVersions`, a completely different
resource (`PromptTemplate`, not `Assistant`). Confirmed by reading both blocks in full before
editing: they share only the literal heading text, nothing else. Left the Custom Prompt one
untouched.

Must not break: the version-fetch `useEffect` that currently runs unconditionally (on mount /
`activeWorkspace` change) - it stays exactly as-is; only the rendered JSX moves under the Technical
tab's condition, so re-fetching still happens regardless of which tab is active, matching the
existing behavior of every other unconditionally-fetched piece of state on this page.

## Build steps

- [x] **Step 1 - move the JSX block**
  - Cut the "Version history" block from its current unconditional position (just before
    `</PageShell>`).
  - Paste it as a new `<Card>` directly inside `{activeTab === "technical" && (...)}`'s existing
    `<div className="mt-6 space-y-8">` wrapper, after Technical's other sections (Speech behavior,
    Call settings, Technical Terms/Glossary) - discovered while building that Technical's tab uses
    one single `space-y-8` wrapper around sibling `<Card>`s directly, unlike the
    per-section-`<div className="mt-6">` pattern General/Knowledge/Custom Prompt use, so the moved
    block's own extra wrapper div had to be stripped rather than kept, to avoid a duplicate div
    breaking the JSX structure.
  *Done when:* `npm run build` passes; a temporary Playwright check confirms "Version history" is
  visible when Technical is the active tab and NOT visible when General, Knowledge, or Custom
  Prompt is active; Publish and diff still work from their new location.

## Verify

Open an assistant with at least two saved versions. Confirm "Version history" no longer appears on
General/Knowledge/Custom Prompt. Click the Technical tab - confirm it appears there, and that
Publish and Compare versions still work exactly as before.

## Result

`npm run build` and `npm run lint`: both pass. A temporary Playwright check confirmed: "Version
history" has zero matches on General, Knowledge, and Custom Prompt tabs, and is visible (with its
correct empty state, "No versions saved yet...") on Technical. The check also incidentally
confirmed 23f's nav-link removal is intact (no "Prompt Templates" link in the nav bar).
