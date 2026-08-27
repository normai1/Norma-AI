# Feature: Overview home and global states

**From build-plan:** feature 7c
**Status:** not started

## Goal

Give the app a real authenticated landing page instead of dropping users on the
marketing/status root, add a shared loading primitive so every page stops hand-rolling
its own "Loading..." text, and add a shell-wide route error boundary so a render error
inside the authenticated app shows a calm fallback instead of a blank screen or a stack
trace. This closes out build-plan item 7 (Core application shell) entirely.

## Design reference

None. Reuses the existing dark theme and the primitives already in
`components/organizations/ui.tsx`. `prototypes/` does not exist in this repo.

## In scope

- A shared `LoadingState` primitive (message + a simple spinner), added alongside the
  existing `PageShell`/`Card`/`EmptyState`/`ErrorText` in
  `components/organizations/ui.tsx`, replacing every hand-rolled
  `<p className="text-slate-400">Loading...</p>` across the four existing authenticated
  pages and the `(app)` layout's own session-loading branch.
- `app/(app)/error.tsx` — a Next.js route error boundary scoped to the authenticated
  route group: a calm "Something went wrong" fallback with a "Try again" button
  (`reset()`), no stack trace, no raw error message shown to the user.
- `app/(app)/overview/page.tsx` — the authenticated landing page: a greeting, the active
  organization/workspace (from `useTenant()`), and a way into what's actually built
  (a link to Organizations) when the user has none yet.
- Post-authentication redirect wiring: `/login` and `/register` navigate to `/overview`
  on success instead of `/`. The root `/` page's signed-in state repoints its
  "Organizations" link to `/overview` instead.

## Out of scope

- **Renaming or moving `components/organizations/ui.tsx`.** Its name is a pre-existing
  mismatch with what it actually holds (generic shared primitives, not
  organization-specific ones) — 7a and 7b already built on top of it as-is rather than
  renaming mid-feature, and this feature keeps that precedent. A dedicated rename is a
  clean, isolated future change, not bundled here.
- **Auto-redirecting `/` to `/overview` for an already-signed-in visitor.** `/` keeps
  functioning as a manually-dismissable landing/status page (it doubles as a live
  API-health check today) with a clear link into the app, rather than an automatic
  bounce. This is a deliberate scope call, flagged here in case a later feature wants
  the automatic version.
- **`/invitations/accept`'s post-accept redirect.** It stays pointed at `/organizations`
  on purpose — confirming the just-joined organization is a more useful next step than
  a generic landing page for that specific flow, and it sits outside the `(app)` route
  group by 7a's own design.
- **Real dashboard content** (call counts, recent activity, anything data-driven).
  Nothing exists yet to summarize; `/overview` shows what's genuinely true today.
- **A toast/notification system.** Out of scope for this feature; existing inline
  `ErrorText` stays the error-surfacing pattern.
- **Any backend change.**

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Shared LoadingState primitive** - add `LoadingState` to
  `components/organizations/ui.tsx`, then replace every ad-hoc
  `<p className="text-slate-400">Loading...</p>` (the four org/workspace pages plus
  `app/(app)/layout.tsx`'s `AuthGuard` session-loading branch) with it.
  *Done when:* `npm run build` passes; a grep for the literal loading paragraph text
  outside `ui.tsx` returns nothing; Playwright confirms `/organizations` still renders
  correctly end to end (registration → org creation → list) with no visible regression.

- [x] **Step 2 - Route error boundary** - add `app/(app)/error.tsx` implementing
  Next.js's error boundary contract (`{ error, reset }`), rendering a calm fallback
  message and a "Try again" button that calls `reset()`. No error detail or stack trace
  shown.
  *Done when:* a temporary throwing page under `app/(app)/_temp_boom/page.tsx` (deleted
  before the step is finished, alongside its temp spec) proves via Playwright that
  visiting it renders the boundary's fallback instead of a blank screen or the dev
  overlay, and that clicking "Try again" doesn't itself crash.

- [x] **Step 3 - Overview page and redirect wiring** - add
  `app/(app)/overview/page.tsx` (greeting, active organization/workspace summary from
  `useTenant()`, a link to Organizations when the user has none); change `/login` and
  `/register` to `router.push('/overview')` on success; repoint `/`'s signed-in
  "Organizations" link to `/overview`.
  *Done when:* Playwright shows a newly registered user landing on `/overview` (not
  `/`) with the shell visible and the zero-organization state offering a working link to
  `/organizations`; a user who logs in (not registers) also lands on `/overview`; a
  signed-in visit to `/` shows a working link to `/overview`.

## Files / areas

**New**
- `apps/web/app/(app)/error.tsx`
- `apps/web/app/(app)/overview/page.tsx`

**Modified**
- `apps/web/components/organizations/ui.tsx` — adds `LoadingState`.
- `apps/web/app/(app)/layout.tsx` — `AuthGuard`'s loading branch uses `LoadingState`.
- `apps/web/app/(app)/organizations/page.tsx`,
  `apps/web/app/(app)/organizations/[organizationId]/page.tsx`,
  `apps/web/app/(app)/organizations/[organizationId]/workspaces/page.tsx`,
  `apps/web/app/(app)/organizations/[organizationId]/workspaces/[workspaceId]/page.tsx`
  — loading branch uses `LoadingState`.
- `apps/web/app/login/page.tsx`, `apps/web/app/register/page.tsx` — redirect target.
- `apps/web/app/page.tsx` — signed-in CTA link target.

**Unchanged**
- `apps/web/app/invitations/accept/page.tsx` — deliberately, see Out of scope.
- Everything under `apps/api/`.

## Data / contracts

No new contracts. `LoadingState` takes an optional `message?: string` (defaulting to
"Loading...") — a presentational component, not something later features depend on
structurally.

## Testing

The frontend gate is live (`npm run test`), but this feature has no in-scope pure logic
— every done-when here is shell/integration behavior (a shared presentational component,
an error boundary, a landing page, redirect targets), which per the Testing gate rides on
screenshot/build evidence plus Playwright, not unit tests.

**Browser verification:** Playwright is already installed, so per `coding-standards.md`'s
Browser Verification section it is the tool for all three steps. Use temporary spec files
under `apps/web/e2e/_temp_*.spec.ts`, deleted along with `test-results/` before finishing
each step. Step 2's temporary throwing page follows the same disposable-scaffolding
principle, deleted alongside its spec.

**Manual path:** register a new account and confirm you land on `/overview`, not the
marketing page; sign out and log back in, confirm the same; visit `/` while signed in and
follow its link back into the app.

## Notes for the AI

- **This closes out build-plan item 7.** Once Step 3 lands, check off both `7c` and the
  parent `7` in `build-plan.md` at `/complete`.
- **Rate limits during verification:** registration is rate-limited
  (`REGISTER_RATE_LIMIT`, 5/hour per IP) in dev. Check
  `docker compose exec redis redis-cli GET "ratelimit:register:<ip>"` before a run that
  needs a fresh account, and reuse one registered session across assertions within a
  test where possible.
- **Docker/Windows gotcha:** a new route directory (`overview/`, the temporary
  `_temp_boom/`) may not be picked up through the bind mount, and — as seen during 7b's
  verification — a `docker compose restart web` can itself leave the *next* first hit to
  a route mid-compile (Turbopack). If a Playwright run stalls on a navigation, check
  whether the page snapshot shows "Compiling..."/"Rendering..." before assuming an app
  bug — pre-warm the route with a direct `curl` first.
- **The error boundary can only catch render-time errors,** not errors already handled
  inside a page's own try/catch (which show as inline `ErrorText`, unchanged by this
  feature). Step 2's temporary throwing page is deliberately a bare `throw` in the
  component body to exercise exactly that boundary.
- Keep `LoadingState` visually consistent with the existing dark theme tokens
  (`slate-400`/`slate-950`) — don't introduce a new color or a spinner library.
