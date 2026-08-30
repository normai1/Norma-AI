# Findings

> **Generated file.** The findings ledger: review findings raised by `/audit`
> against the work in progress, each with a durable ID, severity (P0-P3), and
> status. `/implement` marks repaired findings `fixed`, a later `/audit` pass
> moves them to `closed`, and `/complete` refuses to merge while any P0 or P1
> finding is `open` or `fixed`, then archives resolved findings with the work
> and resets this file.

### F-14 [unverified] open - Cancelled query returns a connection to the pool

**File:** apps/api/tests/test_auth_concurrency.py:79
**Found:** 2026-08-26 by /audit (scope: current; lens: tests)
**Why it matters:** `asyncio.wait_for` cancels an asyncpg query that is deliberately
blocked on a row lock. asyncpg sends a cancellation request and the session is then
rolled back and returned to the pool. That sequence is a known source of
intermittent failures when a connection is reused before the server has finished
processing the cancel. No failure has been observed here - three consecutive
concurrency runs and three full-suite runs were green, and running the concurrency
tests ahead of the session tests changed nothing - so this is a lead rather than a
defect. It is recorded because a slower or more contended CI runner (build-plan
item 28) is exactly where it would first appear.
**Suggested fix:** If it ever flakes, give these two tests their own engine so a
possibly-tainted connection is disposed with it rather than pooled. Do not change
anything now; there is no failure to chase.
**Resolution:**

### F-27 [P3] closed - `_signed_in` is duplicated across five test files, byte-identical

**File:** apps/api/tests/test_permission_enforcement.py:20
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** The exact same `_signed_in(client, email)` helper is defined
identically in `test_organizations.py`, `test_organization_authorization.py`,
`test_organization_members.py`, `test_invitations.py`, and now
`test_permission_enforcement.py` (diffed all five, byte-identical). Four predate
this feature - pre-existing drift from feature 2 that its own three audit rounds
never caught - and this feature added a fifth rather than breaking the pattern.
Not a defect; every copy works. It is compounding maintainability debt: a change
to the registration payload shape now needs five identical edits, and each new
test file makes the eventual extraction slightly more work.
**Suggested fix:** Move `_signed_in` into `conftest.py` as a shared helper or
fixture, import it from all five files. While in that territory, the three
differently-named "create an org and add a member" helpers
(`_org_with_role`, `_org_with_owner`, `_org_with_second_member`) are a related,
looser instance of the same pattern - worth a look in the same pass, though their
differing return shapes mean the fix isn't as mechanical.
**Resolution:** Fixed in feature 4a, Step 1. `_signed_in` and the two byte-identical
`_org_with_owner` copies (`test_invitations.py`, `test_organization_members.py`)
moved into `tests/conftest.py`; all five `_signed_in` sites and both
`_org_with_owner` sites now import the shared version. `_org_with_role`
(`test_permission_enforcement.py`) and `_org_with_second_member`
(`test_organization_authorization.py`) were deliberately left in place - their
differing return shapes (2-tuple vs. 4-tuple) mean a forced merge risks a subtle
bug in two already-correct files for a P3 finding's marginal benefit. The
unrelated DB-level `_org_with_owner` in `test_organization_concurrency.py` was
also left alone; it builds fixtures directly, not through the API. Re-reviewed
2026-08-27 (scope: full; lens: quality): `conftest.py` and the five originally
affected test files are unchanged since the fix, confirmed zero duplicate
`_signed_in` definitions outside `conftest.py`. Closed.

### F-28 [P3] fixed - `apps/voice`'s new health endpoint has no test, unlike every other health endpoint in the repo

**File:** apps/voice/app/main.py:11
**Found:** 2026-08-27 by /audit (scope: current; lens: tests)
**Why it matters:** `apps/api`'s health endpoints (`test_health.py::test_root`,
`::test_health`) are just as trivial - static status checks with no real logic -
and both still get a basic smoke test. `apps/voice`'s new `GET /health` is the
same kind of endpoint but has no test at all, and `apps/voice` has no test
infrastructure yet (no `pytest`/`httpx` in its `requirements.txt`, no `tests/`
directory). Low risk today since the response is hardcoded, not computed, but
it's a real drift from an established repo-wide pattern.
**Suggested fix:** When `apps/voice` gets its first piece of real logic (most
likely item 20), set up `pytest` + `httpx` for it the same way `apps/api` did
and add a smoke test for `/health` at the same time. Not worth standing up a
whole test suite for one static endpoint in isolation before then.
**Resolution:** Fixed in feature 20a, Step 2 - `apps/voice` gets its own `pytest.ini`
(discovered a real bug in doing so: without one, a test run from `apps/voice` walked up
to the root `pytest.ini` and picked up its `apps/api`-specific `pythonpath`, importing
the wrong `app.main` entirely), `pytest`/`httpx` added to its `requirements.txt`, and
`tests/test_health.py` smoke-tests the existing endpoint. `ruff check apps/voice` and
the new test both pass. Not yet re-reviewed by `/audit`.

### F-29 [P2] closed - Workspace `settings` update has zero test coverage

**File:** apps/api/tests/test_workspaces.py
**Found:** 2026-08-27 by /audit (scope: current; lens: tests)
**Why it matters:** `WorkspaceUpdate.settings` and `workspace_repo.update`'s partial-update
handling of it are live, mutable code paths, but no test in `test_workspaces.py` ever sends
`settings` in a PATCH request. The only reference to `settings` in the whole file is the
create-test's default-`{}` assertion. The sibling resource, organizations, has direct
coverage of this exact pattern (`test_organization_members.py::test_update_settings_without_touching_name`).
The underlying code is a structural copy of `organization_repo.update` (already proven correct),
so this is a coverage gap rather than a suspected bug - hence P2, not P1.
**Suggested fix:** Add a test that PATCHes `settings` on a workspace and asserts it persists,
and (mirroring the organization test) a test proving a name-only update leaves `settings`
untouched, and a settings-only update leaves `name` untouched.
**Resolution:** Fixed, then re-reviewed 2026-08-27 (scope: apps/api item-6 files; lens: tests).
`test_update_settings_without_touching_name` and `test_update_name_without_touching_settings`
in `test_workspaces.py` both pass, correctly assert `settings` persists on a settings-only PATCH
and stays untouched on a name-only PATCH (and vice versa) - the exact partial-update semantic
`workspace_repo.update` implements. No new defect introduced. Closed.

### F-30 [P3] fixed - "Workspace not found" is defined twice, byte-identical in message and status

**File:** apps/api/app/api/workspace_deps.py:15
**Found:** 2026-08-27 by /audit (scope: current; lens: quality)
**Why it matters:** `workspace_deps.py`'s `_NOT_FOUND` and `workspaces.py`'s
`_WORKSPACE_NOT_FOUND` are two separate `HTTPException` objects with the identical
status code and detail message, one raised from the access dependency, the other from
the service-exception handler in the PATCH route. Not a defect today - both currently
say the same thing - but they can silently drift if either is edited without the other,
the same class of duplication F-27 already flagged for test helpers, just in production
code this time.
**Suggested fix:** Have `workspaces.py` reuse `workspace_deps.py`'s `_NOT_FOUND` directly
instead of redefining an identical constant, or hoist a single shared constant both
modules import. Small, low-risk, not urgent.
**Resolution:** Fixed. `workspaces.py` now imports `_NOT_FOUND as _WORKSPACE_NOT_FOUND` from
`workspace_deps.py` instead of redefining it; every existing raise site is unchanged. `pytest`
(207 passed) and `ruff` stayed green with no behavior change. Not yet re-reviewed by `/audit`.

### F-31 [P2] closed - No test proves a WorkspaceMember grant to one workspace doesn't leak access to a sibling workspace

**File:** apps/api/tests/test_workspaces.py
**Found:** 2026-08-27 by /audit (scope: apps/api item-6 files; lens: tests)
**Why it matters:** `require_workspace_access` and `workspace_repo.list_for_user` both scope
the `WorkspaceMember` check to the exact `workspace_id` in play (`WHERE workspace_id = :id AND
user_id = :id`), so a member granted access to workspace A should not be able to `GET` or see
in the list a sibling workspace B in the same organization. The query logic is correct on
inspection, but nothing tests it: the existing coverage only proves "zero memberships -> empty
list / 404" and "the one membership that matches -> access granted," never "a membership that
exists but doesn't match." This is exactly the kind of tenant/resource-boundary case this
project otherwise tests explicitly (see `test_tenant_isolation.py` for the equivalent at the
organization level). Not a proven bug - hence P2, not P1.
**Suggested fix:** Add a test that inserts a `WorkspaceMember` row for workspace A (same
technique F-29's sibling tests and the existing `test_get_succeeds_for_an_explicit_member`
already use, since 6a has no member-add endpoint yet) and asserts that member gets 404 on
`GET` for workspace B, and that workspace B does not appear in their `list` results.
**Resolution:** Fixed. Added `test_member_access_to_one_workspace_does_not_reach_a_sibling` to
`test_workspaces.py`, verified it actually catches the regression by temporarily dropping the
`workspace_id` filter in `workspace_member_repo.get` (the test failed as expected: 200 instead
of 404), then reverted that change cleanly. Re-reviewed 2026-08-27 (scope: current, feature 6b;
lens: tests): `workspace_member_repo.get`'s body is unchanged since the fix (confirmed via diff),
the test still passes in the full suite, and 6b's own new `get_by_id`/`list_for_workspace` follow
the identical workspace-scoping discipline (verified `remove_member`'s cross-workspace 404 test
exercises it too). No new defect introduced. Closed.

### F-32 [P2] fixed - Permission module's extension-point docstring still names the abandoned CRM/RAG entities

**File:** apps/api/app/core/permissions.py:4-5
**Found:** 2026-08-27 by /audit (scope: full; lens: quality)
**Why it matters:** The module docstring instructing future features how to extend
`ROLE_PERMISSIONS` still lists the abandoned direction's entities ("companies, contacts,
opportunities, tasks, notes, documents, conversations, prompts, audit logs") instead of the
current AI-phone-assistant ones. CLAUDE.md section 1 explicitly says to flag this class of
leftover rather than let it stand. Elevated above a cosmetic P3 because this is a high-visibility,
load-bearing extensibility point - every future feature adding a permission reads this docstring
as its usage example, and a wrong entity list could steer a real design decision, not just read
as outdated.
**Suggested fix:** Update the parenthetical to name real upcoming entities (assistants, phone
numbers, calls, knowledge sources, campaigns, workspaces, etc.), or drop the specific list and
just say "every later feature that adds a protected mutation."
**Resolution:** Fixed in feature 12a, Step 1 (while already editing this file to add
`MANAGE_PROMPT_TEMPLATES`). The docstring now names real entities (assistants, prompt
templates, glossary entries, phone numbers, calls, knowledge sources, contacts, appointments,
campaigns) instead of the abandoned CRM/RAG list. `ruff check apps/api` and the full backend
suite (372/372) stayed green. Not yet re-reviewed by `/audit`.

### F-33 [P3] fixed - "Member not found" is defined twice, byte-identical, across organizations.py and workspaces.py

**File:** apps/api/app/api/v1/workspaces.py:30
**Found:** 2026-08-27 by /audit (scope: current, feature 6b; lens: quality)
**Why it matters:** `organizations.py` already has its own `_MEMBER_NOT_FOUND` (status 404,
detail "Member not found") for `change_member_role`/`remove_member`. `workspaces.py`'s new
`add_workspace_member` route needed the identical "the given member_id doesn't resolve" case
and redefined an identical constant rather than reusing one. Same class of risk F-30 already
flagged for "Workspace not found" (workspace_deps.py vs. workspaces.py) - harmless today since
both say the same thing, but two independent copies can drift silently if one is edited without
the other.
**Suggested fix:** Same remedy as F-30: have `workspaces.py` reuse `organizations.py`'s
`_MEMBER_NOT_FOUND`, or hoist a small set of shared "not found" constants both route files
import. Worth doing F-30 and F-33 together in one pass rather than separately. Small, low-risk,
not urgent.
**Resolution:** Fixed alongside F-30. `workspaces.py` now imports `_MEMBER_NOT_FOUND` directly
from `organizations.py` instead of redefining it. `pytest` (207 passed) and `ruff` stayed green
with no behavior change. Not yet re-reviewed by `/audit`.

### F-34 [P2] fixed - `SessionProvider`'s `refreshUser` and its mount effect duplicate the identical fetch-and-set-session logic, with drift

**File:** apps/web/components/app/session-provider.tsx:29-64
**Found:** 2026-08-28 by /audit (scope: build-plan item 8; lens: quality)
**Why it matters:** `refreshUser` (added in 8c so the settings page can reflect a saved
profile) and the mount `useEffect`'s inner `load()` function both do exactly the same
thing - call `fetchCurrentUser()`, catch to `null`, call `setUser`/`setStatus` - written
out twice rather than the effect calling `refreshUser()`. The two copies have already
drifted: the effect guards against the unmount race with a `cancelled` flag,
`refreshUser` does not. A future edit to the fetch/error-handling logic (e.g. adding
retry, adding a log line) made to one copy and not the other would silently reintroduce
a bug the other copy already avoids.
**Suggested fix:** Have the mount effect call `refreshUser()` and just handle the
`cancelled` guard around that call, rather than reimplementing the fetch inline.
**Resolution:** Fixed. Split into `fetchUser()` (fetch + error-to-null) and `applyUser()`
(set state); both `refreshUser` and the mount effect now compose these two shared pieces
instead of duplicating the logic, and the effect's `cancelled` guard wraps `applyUser`
exactly as before - no behavior change. `npm run build` and `npm run test` (13/13) pass;
a temporary Playwright spec re-proved session-load-on-mount and profile-save-reflects
both still work. Not yet re-reviewed by `/audit`.

### F-35 [P2] fixed - `businessHoursFromApi`/`businessHoursToApi` are untested pure conversion logic, unlike the project's own established pattern for this class of function

**File:** apps/web/app/(app)/settings/page.tsx:47-90
**Found:** 2026-08-28 by /audit (scope: build-plan item 8; lens: tests)
**Why it matters:** These two functions convert between the API's `business_hours`
shape and the settings form's local per-day state - real, non-trivial logic with a
reachable wrong-answer surface (a day silently dropped, an open/closed flag inverted,
a key typo'd), which is exactly the "parsers, formatters... assertable inputs and
outputs" class `coding-standards.md`'s Testing gate calls out for a unit test when a
test command is configured, as `npm run test` is here. `lib/tenant-selection.ts`'s
`resolveActiveId` - the closest analogous pure-logic function shipped this session -
got exactly this treatment (13 unit tests). These two did not, and are not even
exported from the page module, so they cannot be unit-tested without extraction.
Coverage today is indirect only, via a temporary (now-deleted) Playwright round-trip.
**Suggested fix:** Extract both functions (and `emptyBusinessHoursForm`) to a small
`lib/` module (e.g. `lib/business-hours.ts`) with a colocated `*.test.ts`, matching the
`tenant-selection.ts` pattern; import them back into the page.
**Resolution:** Fixed. Extracted `emptyBusinessHoursForm`, `businessHoursFromApi`,
`businessHoursToApi`, and their types to `lib/business-hours.ts`, with 8 unit tests in
`lib/business-hours.test.ts` (default shape, null/absent/present/explicit-null day
handling, always-emits-all-7-keys, and a round-trip). `settings/page.tsx` now imports
from the module; no behavior change. `npm run test` (21/21) and `npm run build` pass; a
temporary Playwright spec re-proved the actual save/reload/clear round-trip through the
extracted functions. Not yet re-reviewed by `/audit`.

### F-36 [P2] fixed - `user_repo.update`'s `**fields: Any` has no field allowlist of its own; it is safe today only because its one caller is schema-constrained

**File:** apps/api/app/repositories/user.py:47-60
**Found:** 2026-08-28 by /audit (scope: build-plan item 8; lens: security)
**Why it matters:** `organization_repo.update`/`workspace_repo.update` take explicit
named parameters - a closed contract that cannot be asked to write a column outside
`name`/`settings` no matter what a caller passes. `user_repo.update(db, user, **fields)`
instead does `setattr(user, key, value)` for every key in `fields`, so it will happily
set `password_hash`, `is_active`, `email`, or `id` if ever called with such a key. The
one existing caller (`PATCH /me`) is safe only because `ProfileUpdate` is a Pydantic
schema declaring solely `full_name`/`avatar_url` - the safety lives entirely in the
caller, not in this function. A later feature adding a second call site (e.g. an admin
user-management endpoint) that builds `fields` from anything less strictly typed than a
dedicated Pydantic model would silently reopen mass-assignment risk on the `User` row.
**Suggested fix:** Constrain `update()` to named optional parameters for the columns it
is actually meant to touch (`full_name`, `avatar_url`), matching the org/workspace
repos' convention, rather than an open `**fields: Any`. If the exclude-unset semantics
genuinely need to stay generic, at minimum allowlist the accepted keys inside the
function itself so the safety does not depend entirely on every future caller getting
it right.
**Resolution:** Fixed. `update()` now takes explicit `full_name`/`avatar_url` named
parameters, each defaulting to a module-level `_UNSET` sentinel so "omitted" (leave
untouched) stays distinguishable from an explicit `None` (clear the field) - the
distinction the old `**fields` signature existed to preserve. An unexpected key now
raises `TypeError` at the call boundary instead of being silently `setattr`'d. The one
call site (`PATCH /me`) needed no change - `**fields` unpacking against the new closed
signature already maps correctly since `ProfileUpdate` only ever produces
`full_name`/`avatar_url` keys. `pytest tests/test_auth_profile.py` (14/14 unchanged),
full suite (237/237), and `ruff check apps/api` (clean) all pass. Not yet re-reviewed by
`/audit`.

### F-37 [unverified] open - Business hours cannot represent a window crossing midnight

**File:** apps/api/app/schemas/settings.py:94-100
**Found:** 2026-08-28 by /audit (scope: build-plan item 8; lens: quality)
**Why it matters:** `BusinessHoursWindow._close_after_open` requires `close > open` as a
plain string/lexicographic comparison, so a business open e.g. 18:00-02:00 (a bar,
late-night service - both plausible for this product's target segments) cannot be
stored; the validator rejects it as `close` not being "after" `open`. No feature reads
`business_hours` yet (items 11b and 29 are unbuilt), so there is no confirmed
consequence today - recorded as a lead for whoever builds those, not a defect to fix
now.
**Suggested fix:** When item 11b or 29 consumes `business_hours`, either decide
cross-midnight is out of scope for that feature and document it, or extend the window
validation to allow `close < open` to mean "past midnight" and adjust every consumer's
interpretation accordingly.
**Resolution:**

### F-38 [unverified] open - `business_hours: {}` (explicit empty object) and `business_hours: null` are distinct, valid API states with no clear semantic difference

**File:** apps/api/app/schemas/settings.py:113-136
**Found:** 2026-08-28 by /audit (scope: build-plan item 8; lens: quality)
**Why it matters:** The locked contract says `business_hours: null` means "not
configured yet." `WorkspaceSettingsUpdate.business_hours` also accepts `{}` (a
dict with zero day keys) as a distinct valid value, which the merge-then-validate
service logic would store as `{}`, not `null` - a second, undocumented "no hours
configured" representation. The current settings-UI never produces `{}` (it always
sends all seven day keys or omits the field), so this is unreached by any known
caller today; recorded as a lead in case a future client (or a differently-behaved
future settings UI) sends it and something downstream treats `{}` and `null`
differently.
**Suggested fix:** Either treat an empty-dict `business_hours` as equivalent to `null`
in the validator (normalize `{}` to `None`), or explicitly document that `{}` and
`null` are both valid "nothing configured" spellings if the distinction is genuinely
never meant to matter.
**Resolution:**

### F-40 [unverified] open - `MockSTT.stream()` fully drains the audio iterator before yielding any transcript event, so it cannot express a partial transcript arriving while audio is still being sent

**File:** apps/api/app/providers/mock_speech.py:40-47
**Found:** 2026-08-28 by /audit (scope: build-plan item 9; lens: quality)
**Why it matters:** `ElevenLabsSTT.stream` (9b) is deliberately built around genuine
concurrency - sending audio and receiving transcripts happen at the same time on the wire,
called out explicitly in 9b's own spec notes as required for the latency budget. `MockSTT`
(9a), which item 20's replay harness and later turn-detection/barge-in tests (items 20c/20e)
are expected to build on, does the opposite: it drains the entire `audio` iterator to
completion first, then yields the whole scripted transcript afterward, so it cannot express
"a partial transcript arrives before the caller has finished speaking" - the exact behavior
turn detection depends on. Nothing consumes `MockSTT` for that purpose yet (item 20 is
unbuilt), so this has no reachable consequence today; recorded as a lead for whoever builds
item 20, matching the pattern of F-37/F-38.
**Suggested fix:** When item 20 needs it, give `MockSTT` an interleaving mode (e.g. yield
script event N after consuming audio chunk N) rather than drain-then-yield, or add a second
scripting parameter that pairs each transcript event with how many audio chunks to consume
first.
**Resolution:**
