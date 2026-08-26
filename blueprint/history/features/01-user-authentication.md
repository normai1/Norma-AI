# Feature: User identity foundation

**From build-plan:** feature 1a (under 1. User authentication)
**Status:** 1a, 1b, 1c, 1d built and verified; all findings repaired; awaiting review

## Goal

Create the durable identity layer every later feature sits on: a `User` table, a `Session` table for
refresh-token records, the shared model conventions (UUID primary key, timestamps) that every future
Norma AI model will reuse, argon2 password hashing, and the async test-database fixtures that make
database-backed tests possible.

No HTTP routes ship in this sub-feature. It is the schema and the primitives; 1b puts endpoints on
top of them. Getting the shapes right here matters more than usual, because features 2-23 all hang
off `User.id`, and the RBAC model in feature 3 reads from the membership tables that will point at
this one.

## In scope

- Shared `UUIDPrimaryKeyMixin` and `TimestampMixin` used by every model from here on.
- `User` model: email, password hash, profile fields, active flag, last login.
- `Session` model: hashed refresh token, expiry, revocation, client metadata.
- Wiring `app/db/base.py` so Alembic autogenerate actually sees the models.
- One Alembic migration creating both tables and their indexes.
- `app/core/security.py`: argon2 password hash/verify, refresh-token hashing, email normalization.
- Async pytest fixtures bound to a **separate test database**, plus tests proving the models persist.

## Out of scope

- Any API route, request/response schema, or FastAPI dependency (1b and 1c).
- JWT encoding/decoding and access-token issuance (1b).
- Refresh, logout, revocation flows and the `get_current_user` dependency (1c).
- Any frontend work (1d).
- Redis-backed session state - `Session` rows live in PostgreSQL only for now; the plans allow Redis
  to cache live session state later, and 1c decides that.
- Organizations, memberships, roles, and `organization_id` on anything (feature 2 and 3).
- The pgvector extension and any vector column (feature 13).
- Email delivery or verification of any kind.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Model conventions and the User model** - add `app/models/mixins.py` with
  `UUIDPrimaryKeyMixin` (UUID PK, server-side default) and `TimestampMixin` (`created_at`,
  `updated_at`, both timestamptz, server defaults). Add `app/models/user.py` (the file exists but is
  empty) with the `User` model. Import the models into `app/db/base.py` so
  `Base.metadata` is populated. *Done when:* `python -c "from app.db.base import Base; print(sorted(Base.metadata.tables))"` prints `['users']`, and `ruff check .` is clean.

- [x] **Step 2 - Session model** - add `app/models/session.py` with the `Session` model (hashed
  refresh token, expiry, revocation, client metadata) and its FK to `users.id` with
  `ondelete="CASCADE"`. Register it in `app/db/base.py`. *Done when:* the same import prints
  `['sessions', 'users']`, and `ruff check .` is clean.

- [x] **Step 3 - Alembic migration** - autogenerate against the current head (`c8c17028b221`), then
  read the generated file and correct it by hand: confirm both tables, the unique index on
  `users.email`, the index on `sessions.user_id`, the FK cascade, and that `downgrade()` actually
  drops both tables. *Done when:* `alembic upgrade head` succeeds on the dev database, `alembic
  current` shows the new revision, `\d users` and `\d sessions` in psql show the expected columns and
  indexes, and `alembic downgrade -1` followed by `alembic upgrade head` round-trips cleanly.

- [x] **Step 4 - Security primitives** - add `app/core/security.py` with `hash_password`,
  `verify_password` (pwdlib argon2), `hash_token` (SHA-256, for refresh tokens) and
  `normalize_email`. Ship `tests/test_security.py` in the same diff. *Done when:* `pytest
  tests/test_security.py` passes, covering: a hash never equals its plaintext, verify succeeds on
  the right password, verify fails on the wrong one, two hashes of the same password differ (salted),
  `hash_token` is stable and 64 hex chars, and `normalize_email` lowercases and strips surrounding
  whitespace.

- [x] **Step 5 - Test database fixtures and persistence tests** - add `tests/conftest.py` with an
  async engine and session fixture bound to a **separate** test database, creating and dropping
  schema around the session. Add `tests/test_models.py`. *Done when:* `pytest` passes the whole
  suite (including the two existing health tests), and the new tests prove: a `User` persists and
  reads back with a generated UUID and populated timestamps, a duplicate email raises
  `IntegrityError`, a `Session` persists against its user, and deleting the user cascades the session
  away. Confirm the dev database is untouched - its `users` table still holds whatever it held.

### Continued into 1b, 1c, 1d (batched at the user's request)

- [x] **1b - Registration and login API** - `POST /auth/register` (201) and `POST /auth/login`, both
  returning an access token, a rotating refresh token, and the user. Adds schemas, the user and
  session repositories, the auth service, domain exceptions, and JWT issuance. *Done when:* 7
  registration tests and 7 login tests pass, and the live container returns 201/200.
- [x] **1c - Session lifecycle and route protection** - `POST /auth/refresh` (rotation + replay
  detection), `POST /auth/logout` (204), `GET /auth/me`, and the `CurrentUser` / `DbSession`
  dependencies. *Done when:* 13 session tests pass, and a replayed refresh token returns 401 live.
- [x] **1d - Authentication UI** - `/login` and `/register` pages, shared auth shell and form
  controls, token storage with refresh-on-401, and a signed-in/signed-out header with sign-out on
  the home page. *Done when:* `npm run build` compiles all three routes with TypeScript passing and
  the dev server serves them with rendered content.

## Build steps

### Audit repairs (from `/audit`, 2026-08-25)

- [x] **Repair F-02 - reject a default or short JWT signing key outside development** - validate
  `secret_key` at settings load. *Done when:* constructing settings with a production environment and
  a placeholder or sub-32-character key raises, development is unaffected, and tests cover both.
- [x] **Repair F-01 - rate limit the authentication endpoints** - Redis-backed attempt counters on
  login and register. *Done when:* exceeding the limit returns 429 with `Retry-After`, the counter is
  scoped per client and per account, and tests cover allow, block, and isolation.

- [x] **Repair F-04 - resolve the real client address behind a proxy** - hop-counted
  `X-Forwarded-For` parsing so the register limit stops keying on the load balancer. *Done when:*
  the client address is read from the configured hop, a spoofed prefix cannot change it, and an
  untrusted or absent header falls back to the peer address.
- [x] **Repair F-10 - make the rate-limit window atomic** - one round trip that increments and sets
  the expiry. *Done when:* a key left without a TTL self-heals on the next call, and the window is
  not extended by ongoing attempts.
- [x] **Repair F-11 - treat an unset ENVIRONMENT as unsafe** - the secret-key guard must not be
  skipped just because the variable is missing. *Done when:* constructing settings with no
  environment and a placeholder key raises, and an explicit development environment still passes.

- [x] **Repair F-03 - lock the session row during refresh rotation** - load the session with
  `FOR UPDATE` so two concurrent refreshes cannot both pass the revocation check. *Done when:* a
  genuinely concurrent pair of refreshes on one token yields exactly one new pair, proven by a test
  using separate committed transactions.
- [x] **Repair F-06 - cover expired access tokens** - assert an access token past its `exp` is
  rejected. *Done when:* a forged token with a past expiry returns 401 from `/auth/me`.

### Repair: pytest config unreachable from the repo root

**Problem.** `[tool.pytest.ini_options]` lives only in `apps/api/pyproject.toml`, and there is no
pytest config at the repository root. Running `pytest` from the repository root sets `rootdir` there,
never loads those options, and so runs in `asyncio_mode=strict` with default loop scopes. All 46
async tests then fail with "async def functions are not natively supported" while the 32 synchronous
ones pass. The suite is fine - only the harness config is out of reach. This came in with 1a step 5,
and every verification run so far happened to be launched from `apps/api`, which hid it. It would
also break build-plan item 28, where CI runs from the checkout root.

**The fix.** Move the pytest configuration to a root `pyproject.toml` as the single source, adding
`testpaths` and `pythonpath` so `import app` resolves. Because pytest walks upward for a config file,
one root config serves both directories with nothing to drift. Leave ruff's settings where they are.
Must not change any test's behaviour: the same 78 tests must pass, from either directory.

- [x] **Repair: single pytest config at the repo root** - create the root `pyproject.toml` with
  `testpaths`, `pythonpath`, and the three asyncio options; remove the duplicated
  `[tool.pytest.ini_options]` block from `apps/api/pyproject.toml`; document the real test command in
  the Commands section of `AGENTS.md`, which still lists Next.js placeholders. Re-check that
  `TEST_DATABASE_URL` still resolves to a separate database now that the root `.env` is visible to a
  root-launched run. *Done when:* `pytest` passes 78 from the repository root **and** from
  `apps/api`, the guard in `conftest.py` still refuses a test URL matching `DATABASE_URL`, and the
  dev database is untouched.

### Repair: lint gate and environment resolution

**Fixes:** F-15, F-16

**F-15 - the documented lint command fails.** `AGENTS.md` names `ruff check apps/api` as the lint
command and it reports 12 errors, all pre-existing and none from this branch. A documented command
that fails is not a usable gate, and build-plan item 28 wires CI around it.

Approach: fix the hand-written files (`env.py`, `health.py`, `database.py`, `redis.py`,
`test_health.py` - all import ordering), fix `alembic/script.py.mako` so newly generated migrations
no longer carry the `UP035`/`UP007` header, and exclude `alembic/versions/` from lint. The exclusion
is deliberate: CLAUDE.md section 6.2 warns against rewriting historical migrations, and the only
violations there are in the empty `c8c17028b221` baseline that environments may already reference.
The alternative - running `ruff --fix` over the versions directory - is recorded here as the option
not taken.

**F-16 - settings depend on the working directory.** `SettingsConfigDict(env_file=".env")` resolves
relative to the process CWD, so a run from the repository root loads the real `.env` while a run from
`apps/api` silently falls back to field defaults. Both report 78 passed against different signing
keys and different database hosts.

Approach: resolve the env file from the module's own location instead of the CWD, passing both the
`apps/api` and repository-root candidates so either layout works. Note the container's code root is
`/app`, so any fixed `parents[N]` index that walks above it raises `IndexError` - derive the API
directory first and step up from there.

- [x] **Repair F-15: make the documented lint command pass** - fix import ordering in the five
  hand-written files, update `alembic/script.py.mako`, and exclude `alembic/versions/` in the ruff
  config. *Done when:* `ruff check apps/api` from the repository root reports no errors, `pytest`
  still passes 78 from both directories, and a freshly autogenerated migration is lint-clean.
- [x] **Repair F-16: anchor the env file to the source location** - resolve `.env` from the module
  path rather than the CWD. *Done when:* settings resolve to the same signing key and database host
  whether launched from the repository root or `apps/api`, `python -c "from app.main import app"`
  succeeds from both, the API container still starts, and `pytest` passes 78 from both directories.

### Repair: remaining audit findings

**Fixes:** F-05, F-07, F-08, F-09, F-12, F-13

- [x] **Repair F-07, F-12, F-13 (backend cleanups)** - drop the no-op `poolclass=None`; branch the
  secret-key failure message on whether `environment` was declared, so an unset variable is not
  reported as development; replace the `tests/__init__.py` import side effect with a root
  `conftest.py`, pytest's documented early hook. *Done when:* `pytest` passes 78 from both
  directories with `tests/__init__.py` empty, and the failure message names the unset variable.
- [x] **Repair F-05, F-08, F-09 (web client cleanups)** - route `postJson` through `apiFetch`;
  guard the token writers for server-side rendering like their readers; set `Authorization` after
  spreading caller headers so it cannot be clobbered. *Done when:* `npm run build` compiles, and
  logout no longer relies on an exception path.

## Files / areas

| Path | Change |
| --- | --- |
| `apps/api/app/models/mixins.py` | new - UUID PK and timestamp mixins |
| `apps/api/app/models/user.py` | exists but empty - `User` model |
| `apps/api/app/models/session.py` | new - `Session` model |
| `apps/api/app/db/base.py` | edit - import models so `Base.metadata` is populated |
| `apps/api/app/core/security.py` | new - hashing and email normalization |
| `apps/api/alembic/versions/<rev>_user_identity.py` | new - creates `users` and `sessions` |
| `apps/api/tests/conftest.py` | new - async test DB fixtures |
| `apps/api/tests/test_security.py` | new - unit tests for the primitives |
| `apps/api/tests/test_models.py` | new - persistence and constraint tests |

## Data / contracts

**Load-bearing.** `User.id` is the foreign key target for organization members, record ownership,
conversations, documents, audit logs, and telemetry. `Session` is the contract 1c's refresh and
logout flows read. Both shapes should be settled here, not adjusted later.

### `users`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | PK, server-side default |
| `email` | String(320) | NOT NULL, unique index, stored lowercase |
| `password_hash` | String(255) | NOT NULL, argon2 - never plaintext |
| `full_name` | String(255) | nullable |
| `avatar_url` | String(1024) | nullable |
| `is_active` | Boolean | NOT NULL, default true |
| `last_login_at` | timestamptz | nullable, written by 1b |
| `created_at` / `updated_at` | timestamptz | NOT NULL, server defaults |

### `sessions`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | PK |
| `user_id` | UUID | FK -> `users.id`, `ondelete="CASCADE"`, indexed |
| `token_hash` | String(64) | NOT NULL, unique - SHA-256 hex of the refresh token |
| `expires_at` | timestamptz | NOT NULL |
| `revoked_at` | timestamptz | nullable - non-null means invalidated |
| `user_agent` | String(512) | nullable |
| `ip_address` | String(45) | nullable - fits IPv6 |
| `created_at` / `updated_at` | timestamptz | NOT NULL, server defaults |

### Decisions locked here

- **Email is a plain lowercase-normalized `String(320)` with a unique index**, not `citext`. The
  overview left this open ("citext/text"); citext needs its own extension and migration surface for
  no gain when normalization happens on write. Normalize in `normalize_email`, always, before compare
  or insert.
- **Refresh tokens are hashed with SHA-256, not argon2.** They are high-entropy random values, so a
  slow KDF buys nothing and makes lookup by token expensive. Passwords use argon2; tokens use
  SHA-256. Do not merge the two.
- **The plaintext refresh token is never stored** - only `token_hash`.

## Testing

pytest and pytest-asyncio are already installed and configured (`asyncio_mode = "auto"` in
`pyproject.toml`), and `tests/test_health.py` already passes. CLAUDE.md section 23 requires unit
tests for pure logic and integration tests for database operations, so **the test gate is on for this
feature** regardless of what `AGENTS.md` currently declares (see Notes).

| Step | Coverage |
| --- | --- |
| 1, 2 | No test - declarative models, covered by step 5 |
| 3 | Migration verified by `upgrade` / `downgrade` / `upgrade` round-trip and psql inspection, not a unit test |
| 4 | `tests/test_security.py` - hashing, verification, salting, token hashing, email normalization |
| 5 | `tests/test_models.py` - persistence, UUID and timestamp generation, unique-email violation, FK cascade |

Run with `cd apps/api && pytest`. Steps 4 and 5 must be green before either is approved.

**The test database must not be the dev database.** Point the fixtures at a separate database
(a `TEST_DATABASE_URL` setting, defaulting to the dev URL with a `_test` suffix) and have
`conftest.py` refuse to run if the resolved URL matches `settings.database_url`. CLAUDE.md section 28
is explicit about not running destructive operations against the dev database, and these fixtures
create and drop schema.

## Notes for the AI

- **`blueprint/context/coding-standards.md` does not describe this project.** It is still the
  untuned blueprint default: TypeScript, React, Next.js, Prisma, Clerk, Zod, Tailwind. Norma AI's
  backend is Python 3.12 + FastAPI + SQLAlchemy + Alembic. Ignore the Prisma migration rules and the
  "scope every query by the authenticated Clerk user id (`clerkUserId`)" rule - the equivalent here
  is scoping by `organization_id` in the repository layer, and that starts in feature 2. Follow
  `CLAUDE.md` instead, which is written for this stack. The genuinely stack-neutral parts of
  coding-standards still apply: no commented-out code, no unused imports, functions under 50 lines,
  comment the why and not the what, and **no em dashes** in generated content.
- **Match the existing code style**, which is already consistent across `app/core/` and
  `app/api/v1/`: a short triple-quoted docstring on each public function, two blank lines between
  top-level definitions, explicit return type annotations, and `from __future__` not used. Do not
  introduce a different convention.
- **Do not add dependencies.** `PyJWT`, `pwdlib[argon2]`, and `email-validator` are already in
  `apps/api/requirements.txt`, unused so far. This sub-feature needs only `pwdlib` (argon2) and the
  standard library's `hashlib`. JWT and `email-validator` come into play in 1b.
- **Alembic runs sync.** `alembic/env.py` swaps `+asyncpg` for `+psycopg` for migrations while the
  app uses asyncpg. Do not "fix" this; it is deliberate and working.
- **Use `sqlalchemy.orm.Mapped` / `mapped_column`**, matching the 2.0 `DeclarativeBase` style already
  set up in `app/db/base.py`.
- **Autogenerate, then read what it produced.** Alembic will not infer the FK cascade or the index
  choices reliably. CLAUDE.md section 6.2 requires inspecting the head, checking prior migrations,
  and avoiding duplicate objects before writing a migration. The current head is `c8c17028b221`
  ("initial foundation"), which is an empty no-op migration - this will be the first one that creates
  real tables.
- **`app/models/` and `app/api/` have no `__init__.py`** and work as namespace packages. Adding
  `__init__.py` is fine if it helps the model imports read cleanly, but do not restructure packages
  beyond what this feature needs.
- `health.py` still reports `"service": "norma-clone-api"`, a legacy string. Leave it alone; it is
  unrelated to this feature and belongs in a `/fix`.
- Timestamps should be timezone-aware (`DateTime(timezone=True)`) with `server_default=func.now()`,
  and `updated_at` should use `onupdate`.

## Findings

### 01-user-authentication/F-01 [P1] closed - Authentication endpoints have no rate limiting

**File:** apps/api/app/api/v1/auth.py:86
**Found:** 2026-08-25 by /audit (scope: current; lens: security)
**Why it matters:** `/auth/login` accepts unlimited attempts against a known email
with no lockout, delay, or attempt counter anywhere in the request path. A search
for any limiter across `app/` returns nothing. Password guessing is bounded only
by argon2's cost, and `/auth/register` is equally open to automated account
creation. `project-plan.md` section 8 lists rate limiting as a production security
requirement, and Redis is already wired up and available to hold the counters.
**Suggested fix:** Add a Redis-backed attempt counter keyed on email plus client
IP for login, and on IP for register. This is arguably its own build-plan item
rather than part of 1a-1d; if you would rather defer it, mark this `accepted`
with that reason so it stops blocking the merge.
**Resolution:** 2026-08-25 by /implement. Added `app/core/rate_limit.py` with a
Redis fixed-window counter, applied in `auth.py` to login (10 per 15 min, keyed on
client address plus normalized email) and register (5 per hour, keyed on client
address). Exhaustion returns 429 with `Retry-After`. A Redis outage lets the
attempt through and logs a warning rather than locking every user out. Covered by
8 tests in `tests/test_rate_limit.py`, and verified live: attempts 1-10 returned
401, the 11th returned 429 with `Retry-After: 891`. **Closed 2026-08-25 by
/audit:** re-read `rate_limit.py` and the two call sites; unlimited attempts are
genuinely gone and the compound login key correctly prevents one client from
locking out an account it does not control. Two defects in the repair itself are
recorded separately as F-10 and the F-04 escalation.

### 01-user-authentication/F-02 [P1] closed - JWT signing key is a published constant with no guard

**File:** apps/api/app/core/config.py:65
**Found:** 2026-08-25 by /audit (scope: current; lens: security)
**Why it matters:** `secret_key` defaults to a literal committed to the repository,
and the local `.env` carries the equally public placeholder from `.env.example`.
Anyone reading the repo can forge a valid access token for any user id. Nothing
validates the key's length or refuses a default value outside development, so a
deploy that forgets to set it produces a total authentication bypass rather than a
startup failure. PyJWT already warns on every token issued that the key is 23
bytes, under the 32-byte minimum for HS256.
**Suggested fix:** Reject a short or default `secret_key` at startup when
`environment != "development"`. Feature 27 (production configuration) is the
natural owner, but the guard is a few lines in `Settings` and closes the hole now.
**Resolution:** 2026-08-25 by /implement. Added a `model_validator` to `Settings`
that raises when `environment != "development"` and the key is a known placeholder
or under 32 characters, so a misconfigured deploy fails at startup instead of
signing forgeable tokens. Development is unaffected. Covered by 5 tests in
`tests/test_config.py`. **Closed 2026-08-25 by /audit:** re-read the validator;
it fires for every non-development environment and the suggested fix is fully
implemented. A residual gap in the surrounding configuration is recorded as F-11
rather than held against this entry.

### 01-user-authentication/F-03 [P2] closed - Refresh-token rotation has no row lock

**File:** apps/api/app/services/auth.py:122
**Found:** 2026-08-25 by /audit (scope: current; lens: security)
**Why it matters:** `refresh()` reads the session, checks `revoked_at`, then revokes
and reissues as separate statements with no `SELECT ... FOR UPDATE`
(`with_for_update` appears nowhere in `app/repositories/`). Two concurrent requests
carrying the same refresh token can both pass the revocation check before either
writes, so both receive fresh token pairs and the replay detection that
`test_replaying_a_used_refresh_token_fails` proves in the serial case never fires.
That weakens the specific control this feature advertises against stolen tokens.
**Suggested fix:** Lock the row when loading it for rotation
(`select(...).with_for_update()` in `session_repo.get_by_token_hash`, or a
dedicated locking variant used only by `refresh`).
**Resolution:** 2026-08-26 by /implement. Added
`session_repo.get_by_token_hash_for_update`, a `SELECT ... FOR UPDATE` used only by
`auth_service.refresh`; the plain read stays for callers that do not rotate.
`tests/test_auth_concurrency.py` proves the lock with two really-committed
transactions on separate connections: the second locking read times out while the
first holds the row, and a plain read of the same row does not, which is the exact
difference the bug turned on. Verified the test discriminates - deleting
`.with_for_update()` fails it with "DID NOT RAISE TimeoutError". Note a first
attempt at this test, using `asyncio.gather` over two full refresh calls, passed
with and without the lock because the transactions serialized naturally; it was
discarded rather than kept as false assurance. **Closed 2026-08-26 by /audit:**
confirmed `refresh` is the only caller of the locking read and that the plain read
is still reached by `logout`, so nothing became dead. Independently reproduced the
discriminator: with `.with_for_update()` removed the blocking test fails with "DID
NOT RAISE TimeoutError", and with it restored the suite is green across three
consecutive runs. Coverage boundary worth knowing: these tests exercise the
repository lock directly, not `refresh` end to end under contention.

### 01-user-authentication/F-04 [P1] closed - Proxy IP breaks registration once rate limiting keys on it

**File:** apps/api/app/api/v1/auth.py:38
**Found:** 2026-08-25 by /audit (scope: current; lens: security)
**Why it matters:** `_client_ip` reads `request.client.host` directly, and no
`X-Forwarded-For` handling or uvicorn `--proxy-headers` flag exists anywhere in
`app/` or `docker-compose.yml`. In the chosen production topology (Cloudflare in
front of a Render web service) every `sessions.ip_address` row will store the load
balancer's address. That value feeds session listings and the audit trail in
feature 22, so it is wrong in exactly the situation where it matters.
**Raised to P1 on 2026-08-25 by /audit:** the F-01 repair turned this from a
data-quality problem into an availability one. `auth.py:86` keys the register limit
on `_client_ip(request)` alone, so behind Cloudflare and Render every visitor
resolves to the same proxy address and shares a single bucket of
`REGISTER_RATE_LIMIT` (5 per hour) for the entire product. Sign-ups past the fifth
in an hour would get a 429 no matter who they are. Login is unaffected because its
key also carries the email.
**Suggested fix:** Run uvicorn with `--proxy-headers --forwarded-allow-ips`, or
read the leftmost untrusted hop from `X-Forwarded-For`. Only trust the header when
the request came through a known proxy. Until that lands, do not deploy the
register limit behind a proxy.
**Resolution:** 2026-08-26 by /implement. Added `app/api/client_info.py` with
`client_ip`, which counts `trusted_proxy_count` hops from the **right** of
`X-Forwarded-For`; the auth routes now use it. Counting from the right is what
makes it unspoofable, since anything a caller injects lands on the left. An absent
header, a shorter chain than configured, or `trusted_proxy_count=0` all fall back
to the peer address. `TRUSTED_PROXY_COUNT` is documented in `.env.example` and
must be set to the real chain length before deploying behind Cloudflare/Render.
Covered by 11 tests in `tests/test_client_info.py` plus an integration test proving
two clients behind one proxy get separate register buckets. Verified live: three
requests with rotating spoofed `X-Forwarded-For` values all landed in the single
bucket `ratelimit:login:172.20.0.1:...`, keyed on the real peer. **Closed
2026-08-26 by /audit:** re-derived the hop arithmetic and ran an exhaustive check
over 1-3 proxies against 0-5 spoofed prefix entries (45 combinations); the selected
address was the real client every time and never an attacker-supplied one. A caller
adding k entries shifts the index by exactly k, so the chosen hop stays pinned to
the first entry the trusted chain wrote. Misconfiguring the count too high falls
back to the peer; too low selects a proxy address, never a caller-supplied one.

### 01-user-authentication/F-05 [P2] closed - Two near-identical fetch wrappers in the web client

**File:** apps/web/lib/auth.ts:53
**Found:** 2026-08-25 by /audit (scope: current; lens: quality)
**Why it matters:** `postJson` in `lib/auth.ts` repeats what `apiFetch` in
`lib/api.ts` already does - same base URL, same JSON headers, same `toApiError`
conversion - differing only in that it hardcodes POST. Two wrappers over one API
means the next change to error handling, auth headers, or retries has to be made
twice, and the pair will drift.
**Suggested fix:** Have `postJson` call `apiFetch` with `method: "POST"`, or drop
it and use `apiFetch` directly at its four call sites.
**Resolution:** 2026-08-26 by /implement. Removed `postJson` and added `apiPost`
to `lib/api.ts`, so one wrapper now backs all four call sites. Doing so exposed a
latent bug: `/auth/logout` returns a bodyless 204 (verified: 0 bytes), and the old
wrapper called `response.json()` unconditionally, so every logout threw and landed
in the catch block that exists for failed revokes. A real failure was therefore
indistinguishable from success. `apiFetch` now returns undefined for 204. **Closed 2026-08-26 by /audit:** one
wrapper now backs all four call sites and the 204 handling is correct. The
unsound `undefined as T` cast it relies on is recorded separately as F-18.

### 01-user-authentication/F-06 [P2] closed - No test that an expired access token is rejected

**File:** apps/api/tests/test_auth_session.py:34
**Found:** 2026-08-25 by /audit (scope: current; lens: tests)
**Why it matters:** The suite covers a missing token, a garbage token, and a refresh
token presented as an access token, but never an access token whose `exp` has
passed. Expiry is the entire reason access tokens are short-lived, and it is
enforced by a PyJWT default rather than by project code, so a future change to
`decode_access_token` (adding `options={"verify_exp": False}`, or a custom
`decode` path) would silently grant permanent tokens with every test still green.
The refresh-token expiry path *is* covered, which makes the gap easy to miss.
**Suggested fix:** Add a test that encodes a token with a past `exp` using the same
key and algorithm, then asserts `/auth/me` returns 401.
**Resolution:** 2026-08-26 by /implement. Added
`test_me_rejects_an_expired_access_token`, which re-signs the real claims with a
past `exp` and asserts 401, plus
`test_a_freshly_signed_token_with_the_same_claims_is_accepted` as its
discriminator, so the 401 cannot be passing for the wrong reason. Verified the
regression is caught: adding `options={"verify_exp": False}` to
`decode_access_token` fails the expiry test. **Closed 2026-08-26 by /audit:**
re-read both tests. The pairing is what makes them meaningful - the discriminator
re-signs identical claims and expects 200, so a 401 can only come from the changed
`exp` rather than from the act of re-signing.

### 01-user-authentication/F-07 [P3] closed - `poolclass=None` reads as a setting but does nothing

**File:** apps/api/tests/conftest.py:66
**Found:** 2026-08-25 by /audit (scope: current; lens: quality)
**Why it matters:** Passing `poolclass=None` to `create_async_engine` selects the
default pool, which is what omitting the argument does. It reads as though pooling
was deliberately disabled for tests, so a later reader may preserve it as a
constraint that was never intended.
**Suggested fix:** Drop the argument, or pass `NullPool` explicitly if per-test
connection isolation is actually wanted.
**Resolution:** 2026-08-26 by /implement. Dropped the argument; the default pool
was what it selected anyway. **Closed 2026-08-26 by /audit:** argument gone,
suite green from three launch directories.

### 01-user-authentication/F-08 [P3] closed - Token writers lack the SSR guard their readers have

**File:** apps/web/lib/auth.ts:44
**Found:** 2026-08-25 by /audit (scope: current; lens: quality)
**Why it matters:** `getAccessToken` and `getRefreshToken` guard on
`typeof window === "undefined"`, but `storeTokens` and `clearTokens` touch
`window.localStorage` unguarded. Every current caller runs in a client component,
so this is latent rather than broken, but the asymmetry invites a server-side call
that throws at runtime instead of returning null.
**Suggested fix:** Apply the same guard to both writers.
**Resolution:** 2026-08-26 by /implement. `storeTokens` and `clearTokens` now
return early under the same `typeof window === "undefined"` check their readers
use. **Closed 2026-08-26 by /audit:** both writers now mirror their readers.

### 01-user-authentication/F-09 [P3] closed - Caller headers can override the Authorization header

**File:** apps/web/lib/auth.ts:141
**Found:** 2026-08-25 by /audit (scope: current; lens: quality)
**Why it matters:** In `authorizedFetch`, `...options?.headers` is spread after the
`Authorization` entry, so a caller passing its own `Authorization` (or a
lowercased `authorization`) silently replaces the session token. No caller does
this today, but the precedence is the opposite of what the function name implies.
**Suggested fix:** Spread `options.headers` first and set `Authorization` last.
**Resolution:** 2026-08-26 by /implement. Reordered so the session token is
applied after caller headers and can no longer be replaced by one. **Closed
2026-08-26 by /audit** for the same-case collision, which was the realistic
vector. The original entry also named a lowercased `authorization`, and that half
is **not** covered by an object spread: the two spellings are distinct object
keys, so both survive and `Headers` joins them. Reproduced in node - the result is
`Bearer ATTACKER, Bearer SESSION`. Tracked as F-17 rather than left implicit
here.

### 01-user-authentication/F-10 [P2] closed - A lost EXPIRE leaves a rate-limit counter that never resets

**File:** apps/api/app/core/rate_limit.py:50
**Found:** 2026-08-25 by /audit (scope: current; lens: security)
**Why it matters:** `enforce` runs `INCR` and then `EXPIRE` as two calls, and only
sets the expiry when `attempts == 1`. If the process dies or Redis errors between
them, the key survives with no TTL and the expiry is never retried, because every
later call takes the `attempts != 1` branch. The counter then climbs forever and
that key is blocked permanently, recoverable only by deleting it by hand. Confirmed
against Redis: after an `INCR` with no `EXPIRE`, `TTL` reports `-1` and stays `-1`
through further increments while the counter reaches 4.
**Suggested fix:** Make the window atomic - either `SET key 0 EX <window> NX`
followed by `INCR`, a `MULTI`/pipeline, or a small Lua script. Re-asserting the
expiry on every call would also work and is a one-line change.
**Resolution:** 2026-08-26 by /implement. `enforce` now issues `INCR` and
`EXPIRE ... NX` in one `transaction=True` pipeline, so a counter can never be left
without a TTL. `NX` keeps the window fixed rather than sliding, and repairs a key
that already lost its expiry instead of blocking it forever. Two tests added: one
seeds a TTL-less counter and asserts the next call restores an expiry, the other
asserts an existing short TTL is not extended. **Closed 2026-08-26 by /audit:**
re-read `enforce`; INCR and EXPIRE now share one MULTI/EXEC so no interleaving can
leave a counter unexpiring, `RateLimitExceeded` is still not a `RedisError` and so
is not swallowed by the fail-open handler, and `attempts` is never read on the
error path.

### 01-user-authentication/F-11 [P2] closed - The secret-key guard is skipped when ENVIRONMENT is unset

**File:** apps/api/app/core/config.py:97
**Found:** 2026-08-25 by /audit (scope: current; lens: security)
**Why it matters:** The F-02 validator only runs when `environment != "development"`,
and `environment` itself defaults to `"development"`. A deploy that sets neither
`ENVIRONMENT` nor `SECRET_KEY` - the same carelessness F-02 was written against -
still boots happily on the published placeholder key. Verified by constructing
`Settings(_env_file=None)`, which returns a placeholder secret with no error. The
guard covers the documented deployment, where `ENVIRONMENT` is set explicitly, but
not the misconfiguration it was meant to catch.
**Suggested fix:** Treat an unset environment as unsafe rather than as development.
Either require `ENVIRONMENT` to be set explicitly (no default), or invert the check
to run unless the environment is a known-local value **and** the host is local.
**Resolution:** 2026-08-26 by /implement. The guard now skips only when
`environment` is **explicitly set** to development, using `model_fields_set` to
tell a declared value from the field default, so a deploy that sets neither
variable is validated rather than waved through. `tests/__init__.py` declares
`ENVIRONMENT=development` for the test run, which otherwise cannot see the
repository `.env`. Two tests added covering unset-environment with a placeholder
key (raises) and with a strong key (passes). **Closed 2026-08-26 by /audit:**
verified that `Settings(_env_file=None)` with nothing configured now raises rather
than booting on the placeholder. The misleading wording of that failure message is
recorded separately as F-12.

### 01-user-authentication/F-12 [P3] closed - Startup failure message names the wrong environment

**File:** apps/api/app/core/config.py:104
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** When `ENVIRONMENT` is unset, the F-11 guard correctly refuses to
start, but the message interpolates `self.environment`, which is still the
`"development"` field default. A deploy that configured nothing therefore fails with
"Set a unique random value for ENVIRONMENT=development" - naming development as the
environment while refusing to boot precisely because no environment was declared.
An operator reading that during a failed production start is pointed away from the
actual cause. Reproduced with `Settings(_env_file=None)`.
**Suggested fix:** Branch the message on whether `environment` is in
`model_fields_set`, saying the variable is unset in that case rather than quoting
the default.
**Resolution:** 2026-08-26 by /implement. The message now branches: an undeclared
environment reads "because ENVIRONMENT is not set, which is treated as unsafe"
instead of naming the default. Verified against `Settings(_env_file=None)`.
**Closed 2026-08-26 by /audit:** both branches re-checked - an undeclared
environment reports the unset variable, a declared one still names it.

### 01-user-authentication/F-13 [P3] closed - Test suite depends on a package-import side effect

**File:** apps/api/tests/__init__.py:6
**Found:** 2026-08-26 by /audit (scope: current; lens: tests)
**Why it matters:** `tests/__init__.py` sets `ENVIRONMENT` so the F-11 guard does not
reject the placeholder signing key during tests. It works only because importing
`tests.conftest` imports the `tests` package first, which happens to run before
`app.core.config` builds its module-level settings. Nothing enforces that order. A
root-level `conftest.py` importing app code, or any future import that reaches
settings sooner, would break the entire suite with a pydantic validation error that
points at `SECRET_KEY` rather than at the ordering. The comment explains the intent
but cannot enforce it.
**Suggested fix:** Make the dependency explicit rather than incidental - a
`pytest_configure` hook, an `env` entry via a settings plugin, or having
`get_settings()` accept an override the test session installs deliberately.
**Resolution:** 2026-08-26 by /audit (re-examination, before the fix). Still
open, and load-bearing in a way the original entry did not capture: `.env` is
gitignored, so a CI checkout (build-plan item 28) has no environment file at all,
and this side effect was the only thing stopping the F-11 guard from failing the
entire suite there. Removing it without a replacement would have broken CI.
2026-08-26 by /implement. Replaced the package side effect with a rootdir
`conftest.py`, which pytest loads before collecting anything - the documented
hook rather than an incidental import order, and it still runs with no `.env`
present since it sets the variable directly. `tests/__init__.py` is now empty, and
the suite passes 78 from both the repository root and apps/api.
**Closed 2026-08-26 by /audit:** the rootdir conftest is loaded before collection
regardless of launch directory - confirmed from the repository root, apps/api, and
apps/ - and it imports no app code, so the ordering hazard the original entry
described is gone rather than relocated.

### 01-user-authentication/F-15 [P2] closed - The documented lint command does not pass

**File:** AGENTS.md:130
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** `AGENTS.md` now documents `ruff check apps/api` as the lint
command, and it reports 12 errors. All 12 are pre-existing and none are in files
this branch created - `alembic/env.py`, the empty `c8c17028b221` initial
migration, `health.py`, `database.py`, `redis.py`, and `test_health.py`, mostly
import ordering plus `UP035`/`UP007`/`F401` in Alembic's generated template. The
problem is that a documented command which fails is not a usable gate: build-plan
item 28 wires CI around these commands, and a lint job would fail on its first
run for reasons unrelated to any change. Earlier audits under-reported this by
running a narrowed `ruff check app/ tests/`, which excludes `alembic/`.
**Suggested fix:** Either fix the 12 (mostly `ruff check --fix`, but review the
Alembic template edits since `script.py.mako` will regenerate the same header for
every future migration) or scope the documented command to the paths that are
expected to be clean. Do not leave a failing command documented as the gate.
**Resolution:** 2026-08-26 by /implement. Fixed import ordering in the five
hand-written files, rewrote `alembic/script.py.mako` so generated migrations no
longer carry the `UP035`/`UP007` header, and excluded `alembic/versions/` from
ruff rather than reformatting the applied baseline migration. `ruff check
apps/api` now reports "All checks passed" from the repository root, and `pytest`
still passes 78. Note the spec's done-when about a freshly generated migration
being lint-clean was wrong: an empty migration imports `sa` and `op` without using
them, so F401 fires regardless of the template. Verified the equivalent claim
instead - the real `a3f9c4f8ccae` migration, which uses both, passes under the
project config. **Closed 2026-08-26 by /audit:** `ruff check apps/api` still
reports "All checks passed" after this round of edits.

### 01-user-authentication/F-16 [P2] closed - Settings resolution depends on the working directory

**File:** apps/api/app/core/config.py:16
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** `SettingsConfigDict(env_file=".env")` resolves relative to the
current directory. Launched from the repository root the suite loads the real
`.env`; launched from `apps/api` it finds no file and falls back to field
defaults. The two runs therefore execute against materially different
configuration - a different signing key and a different database host - while both
report 78 passed. This was harmless while tests only ever ran from `apps/api`; the
root `pytest.ini` repair made two launch directories normal, which is what
activated it. The same relative path also means `python -c "from app.main import
app"` from `apps/api` now raises the F-11 guard, because no environment file is
found there.
**Suggested fix:** Anchor the env file to a path derived from the source location
rather than the process working directory, so every launch resolves the same
file.
**Resolution:** 2026-08-26 by /implement. `env_file` now takes a tuple of
absolute paths derived from `Path(__file__).resolve().parents[2]`: the apps/api
directory and the repository root. Deriving the API directory first and stepping
up avoids the `IndexError` a fixed `parents[4]` would raise inside the container,
where `/app` is the code root. Verified identical resolution from both launch
directories (same 86-character key, same `postgres` database host, same
environment), `from app.main import app` now succeeds from both where it
previously raised from apps/api, the container still boots and resolves the same
values, and `pytest` passes 78 from each directory. The warning count from
apps/api dropped from 30 to 1, which is the visible proof the real env file is now
being read there. **Closed 2026-08-26 by /audit:** re-verified after the F-12 edit
touched the same file - both launch directories still resolve the same key,
database host, and environment.
