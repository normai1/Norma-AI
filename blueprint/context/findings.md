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

### F-17 [P3] open - Case-differing Authorization header still slips through

**File:** apps/web/lib/auth.ts:133
**Found:** 2026-08-26 by /audit (scope: current; lens: security)
**Why it matters:** The F-09 repair moved `Authorization` after the caller's
headers, which defeats a caller passing that exact spelling. A lowercase
`authorization` is a different object key, so it is not overwritten - both survive
the spread and `Headers` joins them. Reproduced in node: the request goes out as
`Bearer ATTACKER, Bearer SESSION`, which is malformed and puts the caller's value
first. No caller passes headers today, so this is defensive rather than live.
**Suggested fix:** Build a `Headers` instance and call `.set("Authorization", ...)`
after merging. `Headers` normalizes names, so the override holds whatever spelling
a caller used.
**Resolution:**

### F-18 [P3] open - `undefined as T` hides a missing body from the type system

**File:** apps/web/lib/api.ts:63
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** The 204 branch added while repairing F-05 returns
`undefined as T`, so `apiPost<Something>(...)` against a no-content endpoint hands
back `undefined` typed as `Something`. A caller reading a field off that result
crashes at runtime with nothing flagged at compile time. The current callers are
safe - only logout hits 204 and it discards the result - but the cast is exactly
the kind of unchecked assertion `coding-standards.md` rules out alongside `any`.
**Suggested fix:** Type the return as `Promise<T | undefined>` and let the two
callers that always receive a body assert locally, or give no-content requests
their own helper that returns `Promise<void>`.
**Resolution:**
