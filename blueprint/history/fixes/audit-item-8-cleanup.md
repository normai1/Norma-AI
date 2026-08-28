# Fix: Repair audit findings F-34, F-35, F-36 (item 8 quality/security cleanup)

**Type:** Fix
**Fixes:** F-34, F-35, F-36

## The problem

Three P2 findings from `/audit`'s scoped pass over build-plan item 8 (8a profile/password,
8b validated settings, 8c settings UI):

- **F-34** — `apps/web/components/app/session-provider.tsx`: `refreshUser` and the mount
  `useEffect`'s inner `load()` function duplicate identical fetch-and-set-session logic.
  They've already drifted — only the effect guards the unmount race with a `cancelled` flag.
- **F-35** — `apps/web/app/(app)/settings/page.tsx`: `businessHoursFromApi`/
  `businessHoursToApi`/`emptyBusinessHoursForm` are real conversion logic (parser/formatter
  class per `coding-standards.md`'s Testing gate) with no unit test, unlike the analogous
  `lib/tenant-selection.ts` pattern already established in this codebase. They also aren't
  exported from the page module, so they can't be unit-tested without extraction.
- **F-36** — `apps/api/app/repositories/user.py`: `update(db, user, **fields: Any)` has no
  field allowlist of its own; it will `setattr` any key it's given. Safe today only because
  its one caller (`PATCH /me`) is constrained by the `ProfileUpdate` Pydantic schema — the
  safety lives entirely in the caller, not the function, unlike the closed-contract
  `organization_repo.update`/`workspace_repo.update` (explicit named params).

## The fix

**F-34:** Have the mount effect call `refreshUser()` instead of reimplementing the fetch
inline; keep the `cancelled` guard around that call so the unmount-race protection isn't lost.

**F-35:** Extract `businessHoursFromApi`, `businessHoursToApi`, `emptyBusinessHoursForm`, and
the `DayRowState`/`BusinessHoursForm` types to a new `apps/web/lib/business-hours.ts`, with a
colocated `business-hours.test.ts` covering: round-tripping open/closed days, an API value of
`null` producing an all-closed form, `businessHoursToApi` always emitting all seven day keys.
Import the extracted functions back into `settings/page.tsx`; no behavior change.

**F-36:** Change `user_repo.update`'s signature from `**fields: Any` to explicit named
optional parameters (`full_name: str | None = _UNSET`, `avatar_url: str | None = _UNSET`,
using a sentinel to keep the existing "omitted vs. explicit None" distinction the caller
already relies on — a plain `= None` default would collapse "leave untouched" and "clear
this field" back together, which is exactly what this function exists to avoid). Update the
one call site in `apps/api/app/api/v1/auth.py` to pass the named arguments instead of
`**fields`.

Must not break: 8a/8b/8c's existing behavior or tests (`avatar_url`/`full_name` clearing
semantics, the settings page's save/reload flows) — this is a pure refactor plus new test
coverage, no product-facing change.

## Build steps

- [x] **Step 1 — F-34: dedupe SessionProvider's session-loading logic** — mount effect calls
  `refreshUser()`.
  *Done when:* `npm run build` passes; a temporary Playwright check confirms sign-in still
  resolves the session on load and `/settings`'s profile save still reflects without a reload
  (both existing behaviors, re-proven, not new ones).

- [x] **Step 2 — F-35: extract and test business-hours conversion logic** — new
  `lib/business-hours.ts` + `lib/business-hours.test.ts`; `settings/page.tsx` imports from it.
  *Done when:* `npm run test` passes with the new unit tests; `npm run build` passes; a
  temporary Playwright check re-proves the existing business-hours save/reload/clear round-trip
  still works through the extracted functions.

- [x] **Step 3 — F-36: constrain `user_repo.update` to named parameters** — sentinel-based
  optional params replacing `**fields: Any`; the one call site updated.
  *Done when:* `pytest tests/test_auth_profile.py` passes unchanged (14/14); `ruff check
  apps/api` clean; a quick grep confirms no other caller of `user_repo.update` exists that
  would need updating.

## Verify

Full backend suite (`pytest`) and full frontend suite (`npm run test` + `npm run build`) both
green after all three steps. Manually: sign in, edit your profile name on `/settings`, confirm
it reflects immediately; set a business-hours day open then closed then reload, confirm it
persists correctly at each step.
