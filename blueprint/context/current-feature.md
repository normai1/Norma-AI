# Feature: User identity foundation

**From build-plan:** feature 1a (under 1. User authentication)
**Status:** not started

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

- [ ] **Step 1 - Model conventions and the User model** - add `app/models/mixins.py` with
  `UUIDPrimaryKeyMixin` (UUID PK, server-side default) and `TimestampMixin` (`created_at`,
  `updated_at`, both timestamptz, server defaults). Add `app/models/user.py` (the file exists but is
  empty) with the `User` model. Import the models into `app/db/base.py` so
  `Base.metadata` is populated. *Done when:* `python -c "from app.db.base import Base; print(sorted(Base.metadata.tables))"` prints `['users']`, and `ruff check .` is clean.

- [ ] **Step 2 - Session model** - add `app/models/session.py` with the `Session` model (hashed
  refresh token, expiry, revocation, client metadata) and its FK to `users.id` with
  `ondelete="CASCADE"`. Register it in `app/db/base.py`. *Done when:* the same import prints
  `['sessions', 'users']`, and `ruff check .` is clean.

- [ ] **Step 3 - Alembic migration** - autogenerate against the current head (`c8c17028b221`), then
  read the generated file and correct it by hand: confirm both tables, the unique index on
  `users.email`, the index on `sessions.user_id`, the FK cascade, and that `downgrade()` actually
  drops both tables. *Done when:* `alembic upgrade head` succeeds on the dev database, `alembic
  current` shows the new revision, `\d users` and `\d sessions` in psql show the expected columns and
  indexes, and `alembic downgrade -1` followed by `alembic upgrade head` round-trips cleanly.

- [ ] **Step 4 - Security primitives** - add `app/core/security.py` with `hash_password`,
  `verify_password` (pwdlib argon2), `hash_token` (SHA-256, for refresh tokens) and
  `normalize_email`. Ship `tests/test_security.py` in the same diff. *Done when:* `pytest
  tests/test_security.py` passes, covering: a hash never equals its plaintext, verify succeeds on
  the right password, verify fails on the wrong one, two hashes of the same password differ (salted),
  `hash_token` is stable and 64 hex chars, and `normalize_email` lowercases and strips surrounding
  whitespace.

- [ ] **Step 5 - Test database fixtures and persistence tests** - add `tests/conftest.py` with an
  async engine and session fixture bound to a **separate** test database, creating and dropping
  schema around the session. Add `tests/test_models.py`. *Done when:* `pytest` passes the whole
  suite (including the two existing health tests), and the new tests prove: a `User` persists and
  reads back with a generated UUID and populated timestamps, a duplicate email raises
  `IntegrityError`, a `Session` persists against its user, and deleting the user cascades the session
  away. Confirm the dev database is untouched - its `users` table still holds whatever it held.

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
