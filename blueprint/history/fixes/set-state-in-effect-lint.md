# Fix: react-hooks/set-state-in-effect lint errors

**Type:** Fix

## The problem

`npm run lint` fails with 5 `react-hooks/set-state-in-effect` errors (eslint-plugin-react-hooks
7.1.1), all in code from earlier merged features, untouched by anything built this session:

- `components/app/tenant-provider.tsx:103` - `useEffect(() => { load(); }, [load]);`
- `app/(app)/organizations/[organizationId]/page.tsx:73` - same shape
- `app/(app)/organizations/[organizationId]/workspaces/[workspaceId]/page.tsx:64` - same shape
- `app/(app)/settings/page.tsx:55` and `:72` - two effects directly calling `setState` to copy
  `activeOrganization`/`activeWorkspace` settings into local editable form state

`npm run build` still succeeds (this rule doesn't fail the build), so this has been silently
red without blocking anything shipped so far - but the frontend lint gate has been broken.

**What actually satisfies the rule, confirmed empirically** (via a throwaway probe file,
`apps/web/_lint_probe.tsx`, lint-checked and deleted before this spec was written - never
committed):

- Calling a component-local function bare from an effect (`load();`, `void load();`) is
  **always** flagged if that function's own body contains a `setState` call anywhere,
  regardless of whether the call happens after an `await` (i.e. asynchronously). Appending a
  no-op `.then()` to the call does not help.
- The **only** clean shape for an async fetch-then-set-state effect is calling a function that
  itself performs **no** state updates (a pure fetch), then applying the result inside an
  inline `.then(callback)` passed to that fetch call, directly inside the effect body. That
  callback **may** delegate to another named function that sets state (confirmed: nesting one
  level through a named "apply" function inside the `.then()` callback is not flagged) - the
  rule only inspects the effect's own top-level call shape, not deeper delegation once inside
  a recognized `.then()` callback.
- This exact shape - a fetch function and a separate state-applying callback, wired together
  with a `cancelled` flag set in the effect's cleanup - is **already the established pattern**
  in this codebase: `components/app/session-provider.tsx`'s mount effect (`fetchUser` /
  `applyUser`, split in feature 8's F-34 repair) already passes lint clean. This fix applies
  that same proven pattern to the four other mount effects instead of inventing a new one.
- A `useEffect` that directly derives local state from a prop/context value with no async
  fetch involved at all (the two `settings/page.tsx` effects) needs a different, unrelated
  pattern: "adjust state during rendering" per React's own docs - compare against a tracked
  previous id and call `setState` directly in the render body (not inside `useEffect`) when it
  changes. Confirmed empirically that this shape is not flagged (it isn't inside an effect at
  all, so the rule has nothing to inspect).

## The fix

Two distinct patterns, applied to the two distinct problem shapes above. Nothing about what
these effects fetch, when they fetch it, or what they set changes - only the code shape around
the existing logic changes, to make cancellation-on-unmount explicit and give the linter a
shape it recognizes as safe.

**Pattern A - async fetch-on-mount** (`tenant-provider.tsx`, both `[organizationId]` page
files): split the existing `load` function's fetching into a pure fetch (no `setState`,
returns the raw data) and keep a separate `apply...` step that sets state from that data.
`load` itself keeps its current signature and is still called unchanged from every existing
handler (`run`, `handleInvite`, `handleAdd`, `refresh`, `setActiveOrganization`) - only its
internal body changes to compose fetch + apply instead of inlining both. The mount effect
stops calling `load()` directly; it calls the pure fetch function and applies the result (via
the same `apply...` step) inside a `.then()` callback, gated by a `cancelled` flag set in the
effect's cleanup - exactly `session-provider.tsx`'s shape.

**Pattern B - derive local state from context on change** (`settings/page.tsx`'s two effects):
replace each `useEffect` with a tracked "previous id" comparison evaluated directly in the
render body (React's documented "adjust state during rendering" escape hatch), calling the
same `setState` calls it already does, just moved out of `useEffect` and gated by an
id-equality check instead of a dependency array.

Nothing about the rendered output, the data model, or the API layer changes. This is a pure
refactor of how five effects are shaped; behavior must be identical to before.

## Build steps

- [x] **Step 1 - `tenant-provider.tsx`** - split `load`'s organization fetch into a pure
  `fetchOrganizations` (module-level, no closure) and an `applyOrganizations` step (sets
  `organizations`, resolves and sets `activeOrganizationId` + storage, then either awaits the
  existing `loadWorkspaces` for the resolved id or clears workspace state); `load` composes
  fetch + apply and keeps being used unchanged by `refresh`. `loadWorkspaces` itself is
  unchanged (it's never called bare from an effect, so it isn't flagged). The mount effect
  calls `fetchOrganizations().then(...)` with a `cancelled` guard, applying results (or the
  error) only if not cancelled.
  *Done when:* `npx eslint components/app/tenant-provider.tsx` reports zero
  `react-hooks/set-state-in-effect` errors; `npm run build` succeeds; a manual browser check
  (sign in, confirm the organization/workspace switcher still loads and lets you switch)
  shows no behavior change.

- [x] **Step 2 - the two `[organizationId]` page components** - same pattern for
  `app/(app)/organizations/[organizationId]/page.tsx` (`load`: org, members, conditional
  invitations) and `app/(app)/organizations/[organizationId]/workspaces/[workspaceId]/page.tsx`
  (`load`: org, workspace, workspace members, conditional org members). Each gets a pure
  `fetch...Detail(organizationId, ...)` returning a small result object and an
  `apply...Detail` step; `load` composes them and stays the unchanged target of every existing
  handler (`run`, `handleInvite`, `handleAdd`); only the mount effect changes to the
  fetch-then-`.then()`-apply-with-cancelled-guard shape.
  *Done when:* `npx eslint` on both files reports zero `react-hooks/set-state-in-effect`
  errors; `npm run build` succeeds; a manual browser check (open an organization detail page,
  open a workspace detail page, confirm members/invitations still load and add/remove/invite
  actions still work) shows no behavior change.

- [x] **Step 3 - `settings/page.tsx`'s two derive-from-context effects** - replace both
  `useEffect(() => { if (activeOrganization) { setCurrency(...) } }, [activeOrganization])`
  and the equivalent for `activeWorkspace` (timezone/locale/business hours) with the
  "adjust state during rendering" pattern: a tracked `prevActiveOrganizationId` /
  `prevActiveWorkspaceId`, compared directly in the render body against the current
  `activeOrganization?.id` / `activeWorkspace?.id`, calling the same `setState` calls
  (unconditionally, only guarded by the id actually differing) when they differ.
  *Done when:* `npx eslint app/\(app\)/settings/page.tsx` reports zero
  `react-hooks/set-state-in-effect` errors; `npm run build` succeeds; a manual browser check
  (open `/settings`, confirm the organization currency and workspace
  timezone/locale/business-hours fields still populate correctly on load and after switching
  workspaces) shows no behavior change.

- [x] **Step 4 - full verification** - once all three steps are in, confirm nothing else
  regressed.
  *Done when:* `npm run lint` is fully clean (zero errors); `npm run test` passes; `npm run
  build` succeeds; `npx playwright test` passes (covers the existing auth/org E2E flow from
  feature 4b, the most likely place a subtle behavior change in these effects would surface).

## Files / areas

**Modified**
- `apps/web/components/app/tenant-provider.tsx`
- `apps/web/app/(app)/organizations/[organizationId]/page.tsx`
- `apps/web/app/(app)/organizations/[organizationId]/workspaces/[workspaceId]/page.tsx`
- `apps/web/app/(app)/settings/page.tsx`

**Unchanged**
- `components/app/session-provider.tsx` - already correct; the pattern source, not a target.
- Every backend file, every other frontend file. No API, schema, or route changes.

## Verify

Automated: `npm run lint`, `npm run test`, `npm run build`, `npx playwright test` (Step 4).

Manual: sign in, switch organizations and workspaces via the shell switcher, open an
organization detail page and a workspace detail page, open `/settings` and confirm all
fields populate correctly - each per its own step above, then once more together at the end.

A temporary, now-deleted Playwright spec (`e2e/temp-set-state-in-effect-walkthrough.spec.ts`,
never committed) additionally walked the full real flow against the live dev stack: register
a new user, create an organization, open its detail page (members list loads), create a
workspace, open its detail page, open `/settings` and confirm currency/timezone populate
correctly. All green on a clean run. Two false-alarm failures during iteration were diagnosed
and ruled out as environmental: a Turbopack dev-server recompile-timing flake (matches this
project's documented "pre-warm every route" gotcha), and the register rate limiter
(5/hour/IP) tripped by repeatedly re-running the test - cleared via the established
dev-Redis-key-deletion technique, then reproduced a clean pass.
