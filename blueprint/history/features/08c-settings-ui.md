# Feature: Settings UI

**From build-plan:** feature 8c
**Status:** not started

## Goal

Give operators a `/settings` screen to actually use everything 8a and 8b built:
update their own name/avatar and password, and edit the currency, timezone, locale,
and business hours that 8b just made real, validated fields instead of an unvalidated
blob. This closes out build-plan item 8 (User and organization settings) entirely.

## Design reference

None. Reuses the existing dark theme and the primitives in
`components/organizations/ui.tsx` / `components/app/`. `prototypes/` does not exist in
this repo.

## In scope

- `lib/auth.ts` — `updateProfile()`, `changePassword()` (the latter calls
  `storeTokens()` internally, matching `login`/`register` - see Data/contracts for why
  this cannot be left to the caller).
- `components/app/session-provider.tsx` — `useSession()` gains `refreshUser()` so the
  account form can reflect a saved name/avatar without a full reload.
- `lib/organizations.ts` — `OrganizationSettings` type (retiring the current
  `Record<string, unknown>` placeholder), `updateOrganization()`.
- `lib/workspaces.ts` — `WorkspaceSettings`/`BusinessHoursWindow` types (same
  retirement), `updateWorkspace()`.
- `app/(app)/settings/page.tsx` — one page, three stacked sections: Account (profile +
  password), Organization (currency), Workspace (timezone, locale, business hours).
  Organization and Workspace sections are read-only for a non-manager, matching the
  existing `canManage(role)` pattern used elsewhere.
- `lib/navigation.ts` — add the "Settings" entry now that the area is actually built.

## Out of scope

- **Avatar file upload, email change, password reset, account deletion, 2FA.** All
  explicitly out of scope in 8a's own spec; this feature only builds the UI for what
  8a actually shipped.
- **Members and invitations UI.** Already live on the organization detail page and the
  workspace members page (features 2d, 6c) and stay there - this feature's own
  build-plan line scopes it to "the account screen... and the organization/workspace
  settings forms," not a relocation of existing member management.
- **A searchable timezone/locale picker.** `zoneinfo.available_timezones()` accepts
  hundreds of IANA names; the UI offers a curated `<select>` of common zones/locales
  (see Data/contracts) rather than building or importing a combobox component. The
  backend still accepts the full IANA set - typing is UI-side curation, not a
  validation gap.
- **Any backend change.** Items 8a/8b already shipped everything this feature calls.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Settings scaffold and Account section** - add `updateProfile`/
  `changePassword` to `lib/auth.ts`, `refreshUser` to `session-provider.tsx`, the
  "Settings" nav entry, and `app/(app)/settings/page.tsx` with just the Account
  section: a profile form (name, avatar URL) pre-filled from `useSession().user`, and
  a password form (current, new, confirm-new with a client-side match check).
  *Done when:* Playwright shows a signed-in user landing on `/settings` via the new
  sidebar link, editing their name and saving it, seeing it reflected on the page
  without a reload, changing their password successfully, and - the load-bearing
  check - confirms the OLD access token's paired refresh token is now rejected by
  `/api/v1/auth/refresh` while a subsequent authenticated action still works (proving
  the new tokens were actually stored); a mismatched confirm-password shows an inline
  error and does not submit; a wrong current password surfaces the backend's 401
  message inline.

- [x] **Step 2 - Organization settings section** - add `OrganizationSettings`/
  `updateOrganization` to `lib/organizations.ts`; add the Organization section to
  `/settings`: a currency `<select>` (the same curated list as the backend's
  allow-list), enabled only when `canManage(activeOrganization.role)`, otherwise a
  read-only display.
  *Done when:* Playwright shows an owner changing currency and seeing it persist
  (reload confirms it), and a `member`-role user seeing the value read-only with no
  editable control.

- [x] **Step 3 - Workspace settings section** - add `WorkspaceSettings`/
  `BusinessHoursWindow`/`updateWorkspace` to `lib/workspaces.ts`; add the Workspace
  section to `/settings`: timezone and locale `<select>`s (curated lists), and a
  seven-row business-hours editor (one row per day, a closed/open toggle, and two
  `<input type="time">` fields shown only when open). Gated the same way as Step 2;
  additionally shows an empty state when the organization has no active workspace.
  *Done when:* Playwright shows an owner setting timezone/locale and saving them,
  marking Monday open 09:00-17:00 and Sunday closed, reloading, and seeing all three
  persisted; toggling a day back to closed clears its window; a user with no
  workspace in the active organization sees the empty state, not a crash.

## Files / areas

**New**
- `apps/web/app/(app)/settings/page.tsx`

**Modified**
- `apps/web/lib/auth.ts` — `updateProfile`, `changePassword`.
- `apps/web/components/app/session-provider.tsx` — `refreshUser`.
- `apps/web/lib/organizations.ts` — `Organization.settings` retyped,
  `OrganizationSettings`, `updateOrganization`.
- `apps/web/lib/workspaces.ts` — `Workspace.settings` retyped, `WorkspaceSettings`,
  `BusinessHoursWindow`, `updateWorkspace`.
- `apps/web/lib/navigation.ts` — adds the Settings entry.

**Unchanged**
- Every existing page - `/organizations`, `/organizations/[id]`, `.../workspaces`,
  `.../workspaces/[id]`, `/overview`. None of them read or write settings; this
  feature is additive.
- Everything under `apps/api/`.

## Data / contracts

**1. `changePassword` stores tokens itself, not the caller.** 8a's spec flagged this
explicitly: the endpoint revokes every session and returns a fresh pair, so a caller
that forgets to persist the response silently signs the user out on their next
request. Baking `storeTokens()` into the client function (matching `login`/`register`)
makes the correct behavior the only behavior - no settings-page code has the chance to
get this wrong.

**2. Client types mirror the backend shapes exactly:**

```ts
// lib/organizations.ts
export interface OrganizationSettings {
  currency: string;
}
// Organization.settings: Record<string, unknown> -> OrganizationSettings

// lib/workspaces.ts
export interface BusinessHoursWindow {
  open: string;   // "HH:MM", 24h
  close: string;  // "HH:MM", 24h
}
export type BusinessHoursDay =
  | "monday" | "tuesday" | "wednesday" | "thursday"
  | "friday" | "saturday" | "sunday";
export interface WorkspaceSettings {
  timezone: string;
  locale: string;
  business_hours: Partial<Record<BusinessHoursDay, BusinessHoursWindow | null>> | null;
}
// Workspace.settings: Record<string, unknown> -> WorkspaceSettings
```

**3. `updateOrganization`/`updateWorkspace` always PATCH the whole settings object the
form currently holds**, not a hand-picked subset - the form always has concrete values
for every field (pre-filled from the current record), so there is no "omitted field"
at the UI layer. The backend's partial-merge (8b) exists for API flexibility, not
because this UI needs to exploit it; do not add client-side logic to send a subset.

**4. Curated `<select>` lists, matching the backend allow-list/validation, not
duplicating its full range:**

- Currency: exactly `SUPPORTED_CURRENCIES` from `apps/api/app/schemas/settings.py`
  (`USD`, `EUR`, `GBP`, `CAD`, `AUD`) - keep this list in sync if the backend's grows.
- Timezone: a curated ~15-20 common IANA names (e.g. `America/New_York`,
  `America/Chicago`, `America/Los_Angeles`, `America/Toronto`, `Europe/London`,
  `Europe/Paris`, `Europe/Berlin`, `Asia/Kolkata`, `Asia/Dubai`, `Asia/Singapore`,
  `Asia/Tokyo`, `Australia/Sydney`, `Africa/Johannesburg`, `America/Sao_Paulo`,
  `UTC`) - the backend still validates against the full IANA set, so this list can
  grow freely without a backend change.
- Locale: a curated handful (`en-US`, `en-GB`, `fr-CA`, `fr-FR`, `es-ES`, `es-MX`,
  `de-DE`, `pt-BR`, `hi-IN`).

**5. `full_name`/`avatar_url` clearing convention.** An emptied input sends `null`
(clears the field), matching the exact existing convention in
`register/page.tsx` (`input.fullName?.trim() || null`) - not an empty string, which
the backend would happily store as a technically-valid but visually-empty name.

## Testing

The frontend gate is live (`npm run test`), but this feature has no in-scope pure
logic - the new `lib/*.ts` functions are thin request wrappers with no branching (the
existing convention: only `canManage`, which has real logic, gets a unit test; plain
wrappers like `createOrganization`/`inviteMember` do not). Every done-when here is
integration/UI behavior, which per the Testing gate rides on Playwright and the build.

**Browser verification:** Playwright is already installed, so per
`coding-standards.md`'s Browser Verification section it is the tool for all three
steps. Use temporary spec files under `apps/web/e2e/_temp_*.spec.ts`, deleted along
with `test-results/` before finishing each step.

**Rate limits:** registration is rate-limited (5/hour per IP) in dev; check
`docker compose exec redis redis-cli GET "ratelimit:register:<ip>"` before a run that
needs a fresh account and clear it if needed, same as every prior frontend feature
this session. Password-change is also rate-limited (10/15min per user) - unlikely to
matter for a handful of verification runs, but worth knowing if Step 1's verification
needs several attempts.

**Manual path:** sign in, click Settings in the sidebar, edit your name and save,
change your password, edit the organization's currency, edit the active workspace's
timezone/locale/hours, reload after each and confirm persistence.

## Notes for the AI

- **Reuse `canManage(role)` exactly as already used** on the organization detail page
  and workspaces list page - do not invent a second permission-gating pattern.
- **Read the active organization/workspace from `useTenant()`**, not from a route
  param - `/settings` is a flat route with no `[organizationId]` in its path, matching
  the "tenant identity lives in context" contract 7a locked.
- **`<input type="time">`** renders a native picker in every evergreen browser and
  needs no library - use it directly for business-hours fields.
- **Docker/Windows gotcha, and a real one hit last session:** a `docker compose
  restart web` can leave the *next* first hit to any route - including ones that
  already existed - mid-Turbopack-compile, which looks exactly like a stuck
  navigation in a Playwright run. Pre-warm every route you're about to exercise with a
  direct `curl` after any restart before trusting a failure as a real bug.
- **Verifying Step 1's token-rotation done-when concretely:** after changing the
  password in the browser, read the *old* refresh token out of `localStorage` before
  the change (or capture it via a network listener), then `POST
  /api/v1/auth/refresh` with it directly and confirm 401 - proving `changePassword`'s
  `storeTokens()` call actually replaced what was stored, not just that the API call
  succeeded.
- No backend change in this feature. If a step seems to need one, 8a or 8b already
  should have shipped it - stop and check before adding one here.
