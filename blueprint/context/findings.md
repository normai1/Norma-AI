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

### F-27 [P3] fixed - `_signed_in` is duplicated across five test files, byte-identical

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
also left alone; it builds fixtures directly, not through the API.

### F-28 [P3] open - `apps/voice`'s new health endpoint has no test, unlike every other health endpoint in the repo

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
**Resolution:**

### F-29 [P2] fixed - Workspace `settings` update has zero test coverage

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
**Resolution:** Fixed. Added `test_update_settings_without_touching_name` and
`test_update_name_without_touching_settings` to `test_workspaces.py`, mirroring
`test_organization_members.py`'s coverage of the identical pattern. Not yet re-reviewed by
`/audit`, so this stays `fixed` rather than `closed` per the ledger's own rule.

### F-30 [P3] open - "Workspace not found" is defined twice, byte-identical in message and status

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
**Resolution:**
