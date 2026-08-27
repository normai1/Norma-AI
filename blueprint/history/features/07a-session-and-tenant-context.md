# Feature: Session and tenant context

**From build-plan:** feature 7a
**Status:** not started

## Goal

Replace the per-page `fetchCurrentUser()` guard that is now copy-pasted across five
pages with one authenticated route group whose layout resolves the session once, and
introduce the active-organization / active-workspace context that every later feature
route (`/assistants`, `/calls`, `/knowledge`, ...) will read.

This is the plumbing feature of the application shell: no visible chrome ships here.
It exists so 7b can render switchers against a real context instead of inventing one,
and so features 11 onward have a defined answer to "which workspace am I looking at?"

## Design reference

None. This feature ships no new visual surface — existing pages must look and behave
exactly as they do today. `prototypes/` does not exist in this repo.

## In scope

- A pure selection module: resolve which organization/workspace is active given a
  persisted id and the list the API actually returned, plus SSR-safe localStorage
  read/write helpers.
- `SessionProvider` — resolves the signed-in user once, exposes `useSession()`.
- An authenticated route group `app/(app)/` whose `layout.tsx` mounts the providers
  and redirects to `/login` when there is no usable session.
- Moving the four existing authenticated pages into that group and deleting their
  now-duplicated per-page guards. **URLs do not change.**
- `TenantProvider` — active organization and active workspace derived from the real
  `listOrganizations()` / `listWorkspaces()` results, persisted, re-validated on load,
  exposed via `useTenant()`.
- Unit tests for the selection logic.

## Out of scope

- **All visual chrome** — sidebar, top bar, switcher UI, user menu. That is 7b.
- **`/overview`, shared loading/empty/error primitives, the route error boundary, and
  post-authentication redirect wiring.** That is 7c. Until then, a provider-level
  failure shows a plain inline message on the consuming page.
- **Any backend change.** No new endpoint, schema, migration, or permission.
- **`/invitations/accept`** stays outside the route group. It is an email entry point
  reached by users who may not be signed in, and it keeps its own guard. After
  accepting it already navigates to `/organizations`, which is inside the group and
  loads fresh context on arrival.
- **The root `/` page.** Its signed-in/signed-out split and the health-check panel are
  untouched here; 7c owns the redirect wiring.
- **The duplicated `run(action)` mutation helper** on the organization-detail and
  workspace-detail pages. It is real duplication but it is a mutation concern, not a
  session concern — it belongs with 7b/7c's shared primitives.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Selection logic and storage helpers** - add `lib/tenant-selection.ts`:
  a pure `resolveActiveId(persistedId, availableIds)` plus SSR-safe
  read/write/clear helpers for the two localStorage keys, guarded the same way
  `lib/auth.ts` guards `typeof window`. Ship `lib/tenant-selection.test.ts`.
  *Done when:* `npm run test` passes with cases covering an empty list, a stale
  persisted id, a valid persisted id, a null persisted id, and a single-item list;
  no UI changes in the diff.

- [x] **Step 2 - SessionProvider and the authenticated route group** - add
  `components/app/session-provider.tsx` exposing
  `useSession() -> { user, status, signOut }`, add `app/(app)/layout.tsx` that mounts
  it and redirects to `/login` on `unauthenticated`, move the four existing
  authenticated pages into `app/(app)/`, and delete their per-page
  `fetchCurrentUser()` guards.
  *Done when:* Playwright shows `/organizations` still resolving at the same URL when
  signed in and redirecting to `/login` when signed out; every moved page renders
  as before; clearing the stored tokens mid-session and navigating lands on `/login`
  rather than a blank page; `npm run build` and `npm run test` pass.

- [x] **Step 3 - TenantProvider: organizations** - add
  `components/app/tenant-provider.tsx`, mounted inside the `(app)` layout. It loads
  `listOrganizations()`, resolves the active organization through Step 1's function,
  persists the choice, and exposes `useTenant()`. The `/organizations` page consumes
  the context list instead of fetching its own, and `createOrganization` calls
  `refresh()`.
  *Done when:* Playwright shows `/organizations` rendering the same list as before,
  a newly created organization appearing without a page reload, a user with zero
  organizations rendering `activeOrganization === null` with no crash and no infinite
  spinner, and a failed organization fetch surfacing an inline error rather than
  hanging; `npm run build` and `npm run test` pass.

- [x] **Step 4 - TenantProvider: workspaces and persistence** - extend the provider so
  the active organization drives `listWorkspaces(orgId)` and an active workspace
  resolves through the same function. Changing the active organization re-validates
  the persisted workspace against the **new** organization's list rather than keeping
  a foreign id. The workspaces list page consumes the context.
  *Done when:* Playwright shows the workspaces page rendering from context; a full
  page reload preserves the active organization and workspace (verified by reading
  both localStorage keys in the browser); pointing the persisted workspace key at a
  workspace from a different organization falls back to that organization's first
  workspace instead of a 404 or an empty screen; `npm run build` and `npm run test`
  pass.

## Files / areas

**New**
- `apps/web/lib/tenant-selection.ts` — pure resolution + storage helpers
- `apps/web/lib/tenant-selection.test.ts` — Vitest unit tests
- `apps/web/components/app/session-provider.tsx`
- `apps/web/components/app/tenant-provider.tsx`
- `apps/web/app/(app)/layout.tsx`

**Moved (URLs unchanged, guards deleted)**
- `apps/web/app/organizations/page.tsx` → `apps/web/app/(app)/organizations/page.tsx`
- `apps/web/app/organizations/[organizationId]/page.tsx` → under `(app)`
- `apps/web/app/organizations/[organizationId]/workspaces/page.tsx` → under `(app)`
- `apps/web/app/organizations/[organizationId]/workspaces/[workspaceId]/page.tsx` → under `(app)`

**Unchanged**
- `apps/web/lib/auth.ts`, `lib/api.ts`, `lib/organizations.ts`, `lib/workspaces.ts` —
  the providers consume the existing clients; no client signature changes.
- Everything under `apps/api/`.

## Data / contracts

No backend change. Three client-side contracts are locked here and are load-bearing
for 7b, 7c, and every feature from 11 onward.

**1. Tenant identity lives in context, not the URL.**
Feature routes stay flat (`/assistants`, `/calls`), matching the routes table in
`project-overview.md`. The active organization and workspace come from context.
API calls keep embedding the ids in the path
(`/api/v1/organizations/{orgId}/workspaces/{wsId}/...`), so `require_org_member` and
`require_workspace_access` remain the authorization boundary exactly as today — the
client-held id is a *selection*, never a grant. Do not add header-based tenant
context; it would need a backend change this feature does not make.

**2. Hook shapes.**

```ts
type SessionStatus = "loading" | "authenticated" | "unauthenticated";

useSession(): {
  user: AuthUser | null;
  status: SessionStatus;
  signOut: () => Promise<void>;
};

type TenantStatus = "loading" | "ready" | "error";

useTenant(): {
  status: TenantStatus;
  error: string | null;
  organizations: Organization[];
  activeOrganization: Organization | null;
  setActiveOrganization: (id: string) => void;
  workspaces: Workspace[];
  activeWorkspace: Workspace | null;
  setActiveWorkspace: (id: string) => void;
  refresh: () => Promise<void>;
};
```

`AuthUser`, `Organization`, and `Workspace` are the existing interfaces from
`lib/auth.ts`, `lib/organizations.ts`, and `lib/workspaces.ts`. Do not redeclare them.

**3. Storage keys** — matching the existing `norma.` prefix in `lib/auth.ts`:

```
norma.active_organization
norma.active_workspace
```

Both hold a bare id string. Both are advisory: a value that is not in the list the
API returned is discarded and replaced, never trusted.

## Testing

The frontend gate is live — `npm run test` (Vitest) is declared in `AGENTS.md`, so
logic-bearing steps ship a passing test in the same diff.

**In-scope logic needing tests (Step 1):** `resolveActiveId` and the storage helpers.
Cases: empty available list, stale persisted id, valid persisted id, null persisted
id, single-item list, and the SSR path where `window` is undefined.

**Not unit-tested:** the providers and the route-group layout. They are
integration surfaces per the Testing gate — verify them with Playwright and the build,
not brittle render tests.

**Browser verification:** Playwright is already installed (feature 4b), so per
`coding-standards.md`'s Browser Verification section it is the tool for Steps 2-4.
Use temporary spec files under `apps/web/e2e/_temp_*.spec.ts` and delete them, and
their `test-results/` output, before finishing each step. Nothing temporary gets
committed.

**Manual path:** sign in, land on `/organizations`, create an organization, open it,
open its workspaces, reload the page, confirm the same organization and workspace are
still active. Sign out and confirm `/organizations` redirects to `/login`.

## Notes for the AI

- **Client-side by necessity.** `coding-standards.md` says "server components by
  default", but this app holds bearer tokens in `localStorage` against a separate-origin
  FastAPI backend, so the session is unreadable on the server. Every page here is
  already `"use client"`. That is a pre-existing, accepted constraint — do not try to
  convert pages to server components in this feature.
- **Read `localStorage` in `useEffect`, never during render.** Reading it in a render
  body causes a hydration mismatch in the App Router. `lib/auth.ts` already guards on
  `typeof window === "undefined"`; match that style.
- **Never trust a persisted id.** Both stored ids are re-validated against the list the
  API returned on every load. An id the user has lost access to is discarded silently
  and replaced with the first available, not surfaced as an error.
- **Do not re-derive authorization on the client.** The provider only *selects*; the
  backend still authorizes every request. Per CLAUDE.md §36, a client-held
  organization id is never an authorization claim.
- **Preserve the existing patterns.** Keep the `lib/*.ts` API-client style with
  `authorizedJson`/`authorizedEmpty`, the shared primitives in
  `components/organizations/ui.tsx` (generic despite the folder name), and the existing
  error-message style. This feature adds a layer; it does not restyle anything.
- **Moving a page must not change its URL.** `app/(app)/organizations/page.tsx` serves
  `/organizations`, because a parenthesised segment is a route group. Verify each moved
  route in the browser, not by inspection.
- **Docker/Windows gotcha:** a new route directory is not always picked up through the
  bind mount. Run `docker compose restart web` if a moved or new route 404s.
- Deleting each page's `fetchCurrentUser()` guard is the point of Step 2 — the layout
  owns it now. Leaving both in place would double the `/api/v1/auth/me` calls.
