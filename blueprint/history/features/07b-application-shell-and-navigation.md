# Feature: Application shell and navigation

**From build-plan:** feature 7b
**Status:** not started

## Goal

Give every authenticated page a consistent shell: a sidebar, a top bar carrying the
organization and workspace switchers plus a user menu with sign-out, and the existing
organization/workspace pages rendering inside it instead of each page drawing its own
full-screen background. This is the chrome that every later feature route mounts into.

## Design reference

None. Reuses the existing dark theme (`bg-slate-950` / `text-white`) and the `Button`
primitive already in `components/organizations/ui.tsx`. No new visual system.
`prototypes/` does not exist in this repo.

## In scope

- `lib/navigation.ts` — a small static sidebar nav-item list, seeded with one entry
  ("Organizations" → `/organizations`), the only currently-built top-level area.
- `AppShell` / `Sidebar` / `TopBar` components mounted in `app/(app)/layout.tsx`, inside
  `TenantProvider` (they read `useTenant()` and `useSession()`).
- Refactoring the shared `PageShell` primitive (`components/organizations/ui.tsx`) to stop
  drawing its own full-screen background — `AppShell` now owns that — so every existing
  page keeps working without a doubled screen-height wrapper.
- The organization switcher: a select in the top bar listing `organizations` from
  context; changing it calls `setActiveOrganization` and navigates to the selected
  organization's detail page.
- The workspace switcher: a select in the top bar listing `workspaces` for the active
  organization; changing it calls `setActiveWorkspace` and navigates to the selected
  workspace's detail page. Hidden when the active organization has no workspaces.
- The user menu: shows the signed-in user's name/email and a Sign out button, using
  `useSession()`'s existing `signOut()` — the route-group guard (7a) already redirects to
  `/login` once the session goes `unauthenticated`, so no manual redirect is needed here.

## Out of scope

- **`/overview`, shared loading/empty/error primitives, the route error boundary, and
  post-authentication redirect wiring.** That is 7c.
- **The rest of the target core-navigation list** (Calls, Assistants, Knowledge,
  Contacts, Appointments, Campaigns, Numbers, Integrations, Analytics, Settings) — none
  of those features are built yet, and `project-overview.md`'s UI/UX section is explicit
  that "navigation items appear only for features that are actually built." The sidebar
  ships with exactly one entry today and grows as later features land.
- **A collapsible/mobile sidebar drawer.** The shell uses plain flex layout that wraps
  reasonably on narrow viewports; a dedicated mobile nav pattern is deferred until a
  feature actually needs it.
- **Any backend change.** No new endpoint, schema, or permission.
- **Renaming or restyling `Button`, `Card`, `EmptyState`, `ErrorText`, `RoleBadge`,
  `StatusBadge`.** Only `PageShell` changes, and only its outer wrapper.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Shell scaffold: sidebar, minimal top bar, PageShell refactor** - add
  `lib/navigation.ts`, `components/app/sidebar.tsx` (renders `NAV_ITEMS`, highlights the
  active link via `usePathname()`), `components/app/top-bar.tsx` (for now: signed-in
  user's name/email + a Sign out button only, no switchers yet), and
  `components/app/app-shell.tsx` composing them around `children` with the
  `min-h-screen bg-slate-950 text-white` wrapper. Mount `AppShell` inside
  `TenantProvider` in `app/(app)/layout.tsx`. Refactor `PageShell` in
  `components/organizations/ui.tsx` to drop its own `<main className="min-h-screen ...">`
  wrapper and outer max-width container, since `AppShell` now owns the screen-level
  chrome — keep its title/description/action header and `children` untouched otherwise.
  *Done when:* `npm run build` passes; Playwright shows `/organizations` (signed in)
  rendering a sidebar with an "Organizations" link and a top bar with "Sign out", the
  page's own heading ("Organizations") still visible with no doubled background: clicking
  "Sign out" redirects to `/login`, and a subsequent direct visit to `/organizations`
  also redirects to `/login`; a spot check of one nested page (a workspace's members
  page) still renders its existing content correctly inside the shell.

- [x] **Step 2 - Organization switcher** - extend `top-bar.tsx` with a select populated
  from `useTenant().organizations`, value bound to `activeOrganization?.id`. On change,
  call `setActiveOrganization(id)` and `router.push('/organizations/' + id)`. Hidden
  when `organizations.length === 0`.
  *Done when:* Playwright shows a user with two organizations: switching the select
  navigates to the other organization's detail page and updates
  `norma.active_organization` in localStorage to the newly selected id; a user with
  exactly one organization still sees a working (single-option) switcher; a user with
  zero organizations sees no switcher and no crash.

- [x] **Step 3 - Workspace switcher** - extend `top-bar.tsx` with a second select
  populated from `useTenant().workspaces` (scoped to the active organization), value
  bound to `activeWorkspace?.id`. On change, call `setActiveWorkspace(id)` and
  `router.push('/organizations/' + activeOrganization.id + '/workspaces/' + id)`.
  Rendered only when `activeOrganization` exists and `workspaces.length > 0`.
  *Done when:* Playwright shows a user whose active organization has two workspaces:
  switching the select navigates to the other workspace's detail page and updates
  `norma.active_workspace` in localStorage; an organization with zero workspaces shows
  no workspace switcher (organization switcher still works alone) and no crash.

## Files / areas

**New**
- `apps/web/lib/navigation.ts`
- `apps/web/components/app/sidebar.tsx`
- `apps/web/components/app/top-bar.tsx`
- `apps/web/components/app/app-shell.tsx`

**Modified**
- `apps/web/app/(app)/layout.tsx` — mounts `AppShell` inside `TenantProvider`.
- `apps/web/components/organizations/ui.tsx` — `PageShell` loses its own full-screen
  wrapper. This is a shared primitive used by all four existing pages; verify each one
  visually, not by inspection alone.

**Unchanged**
- The four page files themselves (`organizations/page.tsx`,
  `organizations/[organizationId]/page.tsx`, `.../workspaces/page.tsx`,
  `.../workspaces/[workspaceId]/page.tsx`) — they consume `PageShell` and need no edits.
- `lib/auth.ts`, `lib/organizations.ts`, `lib/workspaces.ts`, `lib/tenant-selection.ts`,
  `components/app/session-provider.tsx`, `components/app/tenant-provider.tsx` — this
  feature is a consumer of 7a's contracts, not a change to them.
- Everything under `apps/api/`.

## Data / contracts

No new contracts. This feature is purely a consumer of the `useSession()` and
`useTenant()` shapes 7a already locked — no field, hook, or storage key changes here.

Navigation stays URL-driven, not context-driven: selecting a different organization or
workspace in a switcher both updates context *and* navigates, so the URL and the active
context never drift apart. This matches 7a's existing rule (each org/workspace page
syncs context to its own URL param on mount) and keeps the same "the URL wins, context
follows" direction consistently in both directions.

## Testing

The frontend gate is live (`npm run test`), but this feature has no in-scope pure logic
— `lib/navigation.ts` is a static list with no branches or edge cases worth a unit test.
Every done-when here is a shell/integration behavior (chrome rendering, switcher
navigation, sign-out), which per the Testing gate rides on screenshot/build evidence
plus Playwright, not unit tests.

**Browser verification:** Playwright is already installed, so per `coding-standards.md`'s
Browser Verification section it is the tool for all three steps. Use temporary spec
files under `apps/web/e2e/_temp_*.spec.ts`, deleted along with `test-results/` before
finishing each step.

**Manual path:** sign in, confirm the sidebar and top bar appear around `/organizations`,
switch organizations and workspaces via the top bar selects and confirm the page
navigates and stays correct, then sign out and confirm the shell disappears and
`/organizations` redirects to `/login`.

## Notes for the AI

- **`PageShell`'s refactor is load-bearing.** All four existing authenticated pages
  render through it. After the change, visually verify each one (not just the build
  succeeding) — a missing background or double scrollbar would be easy to introduce and
  easy to miss by inspection alone.
- **Reuse `AuthGuard`'s existing redirect, don't duplicate it.** `useSession().signOut()`
  already flips `status` to `"unauthenticated"`, and `app/(app)/layout.tsx`'s `AuthGuard`
  (from 7a) already redirects to `/login` on that transition. The top bar's sign-out
  handler should just call `signOut()` and stop.
- **Switching must navigate, not just update context.** Per the Data/contracts note
  above, a switcher that updates `activeOrganization`/`activeWorkspace` without
  navigating would leave the URL showing a different organization/workspace than the one
  now "active" in context — exactly the mismatch 7a's Step 4 fix was about, in the other
  direction.
- **The sidebar's one entry is deliberate, not a placeholder to "fill in."** Do not add
  speculative entries for Calls, Assistants, Knowledge, etc. — those features don't
  exist. `project-overview.md` is explicit that nav items appear only for built features.
- **Rate limits during verification:** registration is rate-limited
  (`REGISTER_RATE_LIMIT`, 5/hour per IP) in dev. Reuse one registered session across
  multiple assertions within a test where possible, and check
  `docker compose exec redis redis-cli GET "ratelimit:register:<ip>"` before a run that
  needs several fresh accounts (two organizations, two workspaces).
- **Docker/Windows gotcha:** run `docker compose restart web` if a change isn't picked
  up through the bind mount.
- Preserve the existing dark theme tokens (`slate-950`/`slate-900`/`slate-800`/`slate-700`
  borders) exactly as used elsewhere — don't introduce new colors.
