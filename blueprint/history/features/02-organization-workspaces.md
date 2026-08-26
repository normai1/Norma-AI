# Feature: Organization and membership foundation

**From build-plan:** feature 2a (under 2. Organization workspaces and invitations)
**Status:** 2a-2d built and verified; all audit findings repaired; awaiting review

## Goal

Create the `Organization` and `OrganizationMember` tables, let a user create an organization
(becoming its owner), let a user list and view the organizations they belong to, and build the
`require_org_member` dependency that resolves and verifies organization membership from a request.

That dependency is the deliverable that matters most here. CLAUDE.md's authorization model requires
every protected endpoint to establish who the user is, which organization they're operating in,
whether they're a member of it, and whether the target resource belongs to that organization -
features 6 through 23 all sit on top of this mechanism to enforce tenant isolation. Getting its
shape right now avoids reworking every CRM feature later.

## In scope

- `Organization` model: name, slug, settings, status.
- `OrganizationMember` model: organization, user, role; unique per (organization, user).
- A `slugify` helper that turns an organization name into a URL-safe, collision-resistant slug.
- One Alembic migration creating both tables, their indexes, FKs, and value constraints.
- Repositories for both models, plus an organization service that creates an organization and its
  owner membership as one transaction, with slug-collision retry.
- `POST /organizations` (create, creator becomes owner), `GET /organizations` (list mine),
  `GET /organizations/{organization_id}` (detail, member-only).
- The `require_org_member` dependency and its `CurrentOrgMembership` alias.

## Out of scope

- Updating organization name or settings (2b).
- Listing members, changing roles, removing members, last-owner protection (2b).
- Invitations of any kind, including the email-sending abstraction (2c).
- Organization deletion - not called for anywhere in the plans; would need a product decision first.
- A generic reusable permission framework beyond "is a member" - role-based permission checks are
  feature 3's job. `require_org_member` proves membership only; it does not gate by role.
- Any frontend work (2d).
- Rate limiting on organization creation - nothing in the plans calls for it, and creating an
  organization isn't a credential-guessing target the way login is.
- Pagination on `GET /organizations` - deliberately simple for the MVP; revisit if the list grows
  large enough to matter.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Organization model and slug helper** - add `app/core/slug.py` with `slugify(name)`:
  lowercase, non-alphanumeric runs become a single hyphen, leading/trailing hyphens stripped, and a
  random fallback (`org-` + 8 hex chars) when sanitizing leaves nothing. Add `app/models/organization.py`
  with the `Organization` model (`name`, `slug`, `settings`, `status`) and register it in
  `app/db/base.py`. Ship `tests/test_slug.py` in the same diff. *Done when:* `pytest
  tests/test_slug.py` passes, covering: normal names produce readable slugs, repeated separators
  collapse, symbol-only input falls back rather than producing an empty or all-hyphen string, and the
  fallback is genuinely random across calls; `python -c "from app.db.base import Base; print(sorted(Base.metadata.tables))"`
  includes `organizations`; `ruff check apps/api` is clean.

- [x] **Step 2 - OrganizationMember model** - add `app/models/organization_member.py` with `role`,
  FKs to `organizations.id` and `users.id` (both `ondelete="CASCADE"`, both indexed), and a unique
  constraint on `(organization_id, user_id)`. Register it in `app/db/base.py`. *Done when:* the same
  import includes `organization_members` and `organizations`, and `ruff check apps/api` is clean.

- [x] **Step 3 - Migration** - autogenerate against the current head, then read the generated file and
  confirm: both tables, the unique index on `slug`, the unique constraint on
  `(organization_id, user_id)`, the two CHECK constraints (`status`, `role`), the FK cascades, and
  that `downgrade()` drops both tables. *Done when:* `alembic upgrade head` succeeds, `psql` shows the
  expected columns/constraints/indexes on both tables, and `alembic downgrade -1` followed by
  `alembic upgrade head` round-trips cleanly.

- [x] **Step 4 - Repositories and organization service** - add `app/repositories/organization.py`
  (`get_by_id`, `get_by_slug`, `list_for_user` - one JOIN query returning `(Organization, role)`
  pairs, not N+1 lookups) and `app/repositories/organization_member.py` (`create`, `get_membership`).
  Add `app/services/organization.py` with `create_organization(db, *, name, owner_id)`: generate a
  slug, check it's free, retry with a random suffix up to 5 times if taken, then insert the
  organization and the owner's `OrganizationMember` row, catching `IntegrityError` on the insert
  itself as a race backstop (two concurrent creates can both pass the free-check and collide at
  insert - the unique index is the real guard, matching `auth_service.register`'s precedent) and
  retrying with a new suffix when that happens. Add `get_membership(db, organization_id, user_id)`.
  Ship `tests/test_organization_service.py` in the same diff, using the `db` fixture directly.
  *Done when:* the tests pass, covering: creating an organization returns it with a generated slug,
  the creator has an `owner` `OrganizationMember` row, two organizations created with the same name
  get different valid slugs, and `get_membership` returns `None` for a non-member and for a
  nonexistent organization.

- [x] **Step 5 - API endpoints and the tenant-isolation dependency** - add `app/schemas/organization.py`
  (`OrganizationCreate` with `name: str = Field(min_length=1, max_length=255)`, `OrganizationResponse`
  with `role` included), `app/api/org_deps.py` with `require_org_member` (reads `organization_id` from
  the path, calls `get_membership`, raises 404 - not 403 - when the membership is missing) and its
  `CurrentOrgMembership` alias, and `app/api/v1/organizations.py` with the three routes. Register the
  router in `main.py`. Ship `tests/test_organizations.py` in the same diff. *Done when:* the tests
  pass, covering: create returns 201 with `role: "owner"`; create with a missing or empty `name`
  returns 422; list returns only the caller's organizations; get returns 200 for a member; get
  returns 404 for a real organization the caller does not belong to; get returns 404 for a
  nonexistent organization (same status as the previous case, so existence isn't leaked);
  unauthenticated requests to all three routes return 401; and the live container returns 201/200 for
  the create-then-get flow.

### Continued into 2b, 2c, 2d (batched at the user's request)

- [x] **2b - Membership management** - `PATCH /organizations/{id}` (name and settings),
  `GET /organizations/{id}/members`, `PATCH .../members/{member_id}` (change role),
  `DELETE .../members/{member_id}`. Adds a minimal `require_org_role` with `OrgAdmin` / `OrgOwner`
  aliases, plus last-owner protection in the service. *Done when:* 11 tests pass covering rename,
  settings update, roster, last-owner 409 on both demote and remove, unknown member 404, invalid role
  422, and non-member 404.
- [x] **2c - Invitations** - `Invitation` model and migration, a swappable `EmailSender` Protocol with
  a logging mock, `POST/GET/DELETE /organizations/{id}/invitations`, and `POST /invitations/accept`.
  *Done when:* 14 tests pass covering issue, hashed-token storage, tokens absent from the list
  response, accept, replay rejection, email mismatch 403, expiry, revocation, re-invite superseding
  the previous token, duplicate-member 409, malformed email 422, and non-member 404.
- [x] **2d - Organization UI** - `/organizations` (list plus create), `/organizations/[organizationId]`
  (members roster, role changes, removal, invite form, invitation list with revoke), and
  `/invitations/accept`. Adds `lib/organizations.ts` and shared presentational components.
  *Done when:* `npm run lint` is clean and `npm run build` compiles every route with TypeScript
  passing.

### Decisions made while building 2b-2d

- **`require_org_role` was built here rather than deferred to feature 3.** 2a's spec assigned role
  gating to feature 3, but membership management cannot ship without some gate: without it any
  `viewer` could rename the organization or remove its owner. What exists is deliberately minimal - a
  role allow-list per route - and feature 3 replaces it with the real permission model.
- **Insufficient role is 403, missing membership is 404.** Once `require_org_member` has passed, the
  caller has already proven the organization exists to them, so there is nothing left to leak by
  saying their role is too low.
- **Invitation tokens are returned in the creation response.** No email provider is configured, so
  this is the only way the token reaches anyone. The logging mock deliberately does not log the token.
  When a real provider lands, `token` should be dropped from `InvitationCreatedResponse`.
- **Re-inviting an address revokes the outstanding invitation** rather than leaving two live tokens
  for the same person in the same organization.
- **Accepting requires the signed-in user's email to match the invitation.** A forwarded invitation
  link is otherwise an access grant to whoever opens it.

### Audit repairs (from `/audit`, 2026-08-26)

**Fixes:** F-17, F-18, F-19, F-20, F-21, F-22, F-23

- [x] **Repair F-19 and F-23 - close the privilege escalation** - add a role hierarchy and enforce it
  in `change_member_role`, `remove_member`, and `invite`: no granting a role above your own, no
  acting on a member more privileged than you, and no changing your own role. Put `OrgOwner` to work
  so owner-granting is owner-only. *Done when:* an admin promoting itself to owner returns 403, an
  admin removing an owner returns 403, an admin inviting at owner returns 403, an owner can still do
  all three, and the live takeover sequence is blocked at step one.
- [x] **Repair F-20 - lock the owner rows during the last-owner check** - replace the unlocked
  `COUNT` with a `SELECT ... FOR UPDATE` over the organization's owner rows. *Done when:* the guard
  reads its count under a row lock and the existing last-owner tests still pass.
- [x] **Repair F-21 - one live invitation per address** - add a partial unique index on
  `(organization_id, email) WHERE status = 'pending'`, lock the outstanding row when superseding, and
  treat the resulting `IntegrityError` as a retry. *Done when:* the index exists in psql, re-inviting
  still supersedes, and the migration round-trips.
- [x] **Repair F-17, F-18, F-22 - one response path in the web client** - rebuild the fetch layer so
  URL, headers, auth, error conversion, and the 204 case live in one place; set `Authorization`
  through `Headers` so any spelling of a caller header is overridden; drop `undefined as T`.
  *Done when:* `npm run lint` and `npm run build` pass, no `undefined as T` remains, and a lowercase
  `authorization` header cannot displace the session token.

### Audit repairs, second round (from `/audit`, 2026-08-26)

**Fixes:** F-24, F-25

- [x] **Repair F-24 - handle the invitation unique-index violation** - wrap the insert in a savepoint
  and treat `IntegrityError` as a retry, matching `create_organization`'s slug-collision handling.
  *Done when:* a duplicate pending insert is recovered rather than raising, and re-invite still
  supersedes.
- [x] **Repair F-25 - prove the lock and the index actually hold** - add concurrency tests in the
  pattern feature 1 established. *Done when:* a second `count_by_role_for_update` blocks while the
  first transaction holds the rows, a plain count does not block (the discriminator), a duplicate
  pending invitation raises `IntegrityError`, and removing `.with_for_update()` makes the blocking
  test fail.

### Audit repairs, third round (from `/audit`, 2026-08-26)

**Fixes:** F-26

- [x] **Repair F-26 - make retry exhaustion reach the 409** - the final attempt re-raises
  `IntegrityError`, so `raise InvitationConflict` and the route's 409 handler are both unreachable.
  Drop the special case and let the loop fall through. *Done when:* forcing every insert to collide
  makes `InvitationConflict` escape rather than `IntegrityError`, a test pins that, and the loop has
  no unreachable branch.

## Files / areas

| Path | Change |
| --- | --- |
| `apps/api/app/core/slug.py` | new - slug generation |
| `apps/api/app/models/organization.py` | new - `Organization` model |
| `apps/api/app/models/organization_member.py` | new - `OrganizationMember` model |
| `apps/api/app/db/base.py` | edit - register both models |
| `apps/api/alembic/versions/<rev>_organizations.py` | new - creates both tables |
| `apps/api/app/repositories/organization.py` | new |
| `apps/api/app/repositories/organization_member.py` | new |
| `apps/api/app/services/organization.py` | new - creation transaction, membership lookup |
| `apps/api/app/schemas/organization.py` | new |
| `apps/api/app/api/org_deps.py` | new - `require_org_member` / `CurrentOrgMembership` |
| `apps/api/app/api/v1/organizations.py` | new - create/list/get routes |
| `apps/api/app/main.py` | edit - register the organizations router |
| `apps/api/tests/test_slug.py` | new |
| `apps/api/tests/test_organization_service.py` | new |
| `apps/api/tests/test_organizations.py` | new |

## Data / contracts

**Load-bearing.** `require_org_member` / `CurrentOrgMembership` is the tenant-isolation mechanism
every organization-scoped feature from here forward (companies, contacts, opportunities, tasks,
notes, documents, conversations, prompts, audit logs) will depend on. Its contract:

- Any route that needs organization context declares `{organization_id}` in its path. FastAPI injects
  that path value into any dependency in the chain that also names an `organization_id` parameter -
  the dependency does not take it any other way, so the path parameter name must stay exactly
  `organization_id` everywhere this is reused.
- It returns the `OrganizationMember` row (carries `role`) for the caller in that organization, or
  raises 404. A route that also needs the organization's own fields (name, slug, settings) does its
  own `organization_repo.get_by_id(db, organization_id)` - the ID is already known good once the
  dependency succeeds, so that's one cheap, PK-indexed lookup, not a second membership check.
- **404, never 403, for both "no such organization" and "not a member."** Distinguishing the two
  would let an authenticated user probe which organization IDs exist. This mirrors the login
  endpoint's identical-response-for-wrong-password-vs-unknown-email choice from feature 1.
- This dependency proves membership only. It does not check `role`. A `require_org_role(*roles)`
  dependency is feature 3's job (RBAC) and 2b's first real consumer.

### `organizations`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | PK, server-side default |
| `name` | String(255) | NOT NULL |
| `slug` | String(255) | NOT NULL, unique index |
| `settings` | JSONB | NOT NULL, default `{}` |
| `status` | String(20) | NOT NULL, default `active`, CHECK IN (`active`, `suspended`) |
| `created_at` / `updated_at` | timestamptz | NOT NULL, server defaults |

### `organization_members`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | PK |
| `organization_id` | UUID | FK -> `organizations.id`, `ondelete="CASCADE"`, indexed |
| `user_id` | UUID | FK -> `users.id`, `ondelete="CASCADE"`, indexed |
| `role` | String(20) | NOT NULL, CHECK IN (`owner`, `admin`, `member`, `viewer`) |
| `created_at` / `updated_at` | timestamptz | NOT NULL, server defaults |

Unique constraint on (`organization_id`, `user_id`) - one membership per user per organization.

### Decisions locked here

- **Enum-like columns are plain `String` plus a DB-level `CheckConstraint`, not a Postgres native
  `ENUM` type.** Native enums make adding a value later a real migration operation (`ALTER TYPE`);
  a `String` with a `CHECK` gives the same integrity guarantee without that ceremony. This is now
  the project convention - `Invitation.status` (2c) and any later status/stage/priority column
  (opportunities, tasks, documents) should follow it too.
- **No SQLAlchemy `relationship()` declarations**, matching feature 1: `User`/`UserSession` never
  declared one either. Every join stays an explicit `select()` in a repository.
- **The creator of an organization becomes its `owner` automatically.** Not stated explicitly in the
  plans, but it's the only sensible default for this domain and matches the Owner/Admin/Member/Viewer
  hierarchy the overview already locks in.

## Testing

Same gate as feature 1: pytest and the test-database fixtures are already wired
(`tests/conftest.py`, `pytest.ini` at the repo root), and CLAUDE.md section 23 requires tests for
this kind of logic. Every step above ships its own tests in the same diff.

| Step | Coverage |
| --- | --- |
| 1 | `tests/test_slug.py` - normal input, separator collapsing, symbol-only fallback, fallback randomness |
| 2 | No test - declarative model, covered by step 3's round-trip and step 4's service test |
| 3 | Migration verified by `upgrade` / `downgrade` / `upgrade` and psql inspection, not a unit test |
| 4 | `tests/test_organization_service.py` - creation transaction, slug collision retry, `get_membership` for member/non-member/nonexistent org |
| 5 | `tests/test_organizations.py` - create/list/get happy paths, cross-tenant 404, nonexistent-org 404, unauthenticated 401 on all three routes |

Run with `pytest` from either the repository root or `apps/api` - `pytest.ini` at the root covers
both.

## Notes for the AI

- **`blueprint/context/coding-standards.md` still describes the wrong stack** (TypeScript/Next.js/
  Prisma/Clerk). Follow `CLAUDE.md` and the patterns already in `apps/api/app/` instead - this was
  flagged in feature 1 and still hasn't been fixed by `/onboard`.
- **Match the existing layering exactly:** route -> service -> repository -> database. Use the
  `Annotated` dependency alias pattern (`DbSession`, `CurrentUser` in `app/api/deps.py`) for the new
  `CurrentOrgMembership`. Two blank lines between top-level definitions, a short docstring on each
  public function, explicit return types, no em dashes.
- **Autogenerate the migration, then read what it produced**, per CLAUDE.md section 6.2. The current
  head is `a3f9c4f8ccae`. `alembic/script.py.mako` was already fixed during feature 1's audit repairs,
  so the generated file should come out lint-clean without hand-editing the header this time - just
  verify, don't assume you need to rewrite it.
- **Alembic runs inside the container**: `docker compose exec -T api alembic ...`, because
  `DATABASE_URL` uses the Compose service name and doesn't resolve from the host.
- **The test database is separate and already guarded.** `tests/conftest.py`'s `engine` fixture
  refuses to run if `TEST_DATABASE_URL` matches `DATABASE_URL`. Reuse the existing `db`/`client`/
  `engine` fixtures; no new fixture work is needed for this feature.
- **`pytest.ini` at the repository root is the single config** for both launch directories. Don't
  add a `[tool.pytest.ini_options]` block back into `apps/api/pyproject.toml` - a comment there
  explains why not.
- **No email-sending decision exists in the plans yet.** 2c will need a provider decision or a
  logging-mock placeholder; that's not this feature's problem, but don't be surprised when 2c's spec
  raises it rather than picking one silently.
- **Slug-retry exhaustion (all 5 attempts collide) is deliberately not unit-tested.** Each retry draws
  from a large random space, so this needs five consecutive collisions to trigger in production -
  vanishingly unlikely. The "two organizations with the same name get different slugs" test in step 4
  already exercises the retry path itself via a real collision. Don't build randomness-mocking
  infrastructure just to cover the exhaustion branch; let it raise `RuntimeError` and move on.
- **Organization `status` is stored but not enforced.** A `suspended` organization's members can still
  hit every endpoint in this feature - nothing here checks `status`. That's intentional: no feature
  currently defines what "suspended" should block, so inventing enforcement now would be a guess.

## Findings

### 02-organization-workspaces/F-17 [P3] closed - Case-differing Authorization header still slips through

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
**Resolution:** 2026-08-26 by /implement. `buildHeaders` in `lib/api.ts` now
returns a `Headers` instance, and `authorizedFetch` applies the session token
with `headers.set("Authorization", ...)`. `Headers` normalizes names, so any
casing a caller supplies is replaced rather than joined. Verified in node: with
`authorization: Bearer ATTACKER` supplied by the caller, the resolved header is
`Bearer SESSION` alone. **Closed 2026-08-26 by /audit:** re-read `buildHeaders`
and `authorizedFetch`; the token is applied with `Headers.set` after caller
headers are merged, so no spelling survives alongside it.

### 02-organization-workspaces/F-18 [P3] closed - `undefined as T` hides a missing body from the type system

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
**Resolution:** 2026-08-26 by /implement. Took the second option. `apiSend`
returns the raw `Response`; `apiJson`/`apiPostJson` and `authorizedJson` read a
body; `apiPostEmpty` and `authorizedEmpty` do not. No cast remains - `grep
"undefined as T" apps/web/lib/` is empty. **Closed 2026-08-26 by /audit:**
confirmed no unchecked cast remains anywhere under `apps/web/lib/`, and the
split between body-returning and body-less helpers is used correctly at every
call site.
**Re-examined 2026-08-26 by /audit:** still open, and it has now spread. Feature 2
copied the same `undefined as T` cast into `apps/web/lib/organizations.ts:63`, so
the unchecked assertion exists in two wrappers rather than one. Fixing it should
cover both sites.
**Resolution:**

### 02-organization-workspaces/F-19 [P1] closed - An admin can promote themselves to owner and evict the owner

**File:** apps/api/app/services/organization.py:96
**Found:** 2026-08-26 by /audit (scope: current; lens: security)
**Why it matters:** `change_member_role` is gated by `OrgAdmin` (owner or admin) and
never checks the target's current role, the role being granted, or whether the
caller is acting on their own membership. An admin can therefore grant themselves
`owner`. Once a second owner exists, `_guard_last_owner` no longer blocks removing
the first, so the admin can then delete the original owner outright. Reproduced
end to end against the running API: an invited admin promoted itself (HTTP 200),
removed the founding owner (HTTP 204), and the original owner now receives 404 on
its own organization. The roster ended as a single member - the former admin, as
owner. This is a complete organization takeover using only permitted calls, and it
also means an admin can grant `owner` to any third party by invitation or role
change.
**Suggested fix:** In `change_member_role`, refuse to grant a role above the
caller's own, and refuse self-promotion outright. Granting or removing `owner`
should require `OrgOwner`, which is already defined but currently unused. Feature 3
(RBAC) owns the general model, but this specific escalation should not ship: it
needs no unusual conditions and destroys the owner's access.
**Resolution:** 2026-08-26 by /implement. Added `ROLE_RANK` and `outranks` to the
member model and enforced three rules in the service layer: nobody may grant a
role above their own, nobody may act on a member who outranks them, and nobody
may change their own role at all. The same ceiling applies when issuing an
invitation, so an admin cannot mint an owner that way either. Covered by 10 tests
in `tests/test_organization_authorization.py`, including the audit's exact
takeover sequence. Re-ran that sequence live: every step that previously
succeeded now returns 403 (self-promotion, owner removal, owner demotion,
owner-level invite), the founding owner still gets 200 on its organization, and
the roster is unchanged. **Closed 2026-08-26 by /audit:** re-derived the rule
set against every path an admin could take. Self-promotion, promoting another to
owner, demoting an owner, removing an owner, and inviting at owner level are each
refused, and no combination reaches owner, because the ceiling is checked against
the actor's own role on every route rather than per-annotation. Enforcement lives
in the service layer, so it cannot be bypassed by a future route that forgets a
dependency.

### 02-organization-workspaces/F-20 [P2] closed - The last-owner guard has a check-then-act race

**File:** apps/api/app/services/organization.py:146
**Found:** 2026-08-26 by /audit (scope: current; lens: security)
**Why it matters:** `_guard_last_owner` counts owners with a plain `SELECT COUNT`
and no row lock (`with_for_update` appears nowhere in
`app/repositories/organization_member.py`). Two concurrent requests each demoting
or removing a different owner, in an organization with exactly two, both read a
count of 2, both pass, and both commit - leaving the organization with no owner.
The code's own comment calls that state a dead end that nothing in the product can
restore. This is the same class of defect as the refresh-token rotation race
recorded as F-03 during feature 1, which was fixed with `SELECT ... FOR UPDATE`.
**Suggested fix:** Lock the organization's membership rows for the duration of the
check and the write, mirroring `session_repo.get_by_token_hash_for_update`. Not
reproduced under concurrency, so it is recorded at P2 rather than P1, but the
mechanism is identical to a defect already confirmed in this codebase.
**Resolution:** 2026-08-26 by /implement. Replaced `count_by_role` with
`count_by_role_for_update`, which selects the organization's owner rows
`.with_for_update()` and counts them in Python; PostgreSQL rejects FOR UPDATE
alongside an aggregate, so the rows are materialized rather than counted in SQL.
A second transaction now waits on the lock and re-reads the committed count.
Note the demotion path also became structurally safe: only an owner may demote an
owner and nobody may change their own role, so demoting one requires a second to
exist. `test_cannot_remove_the_last_owner` still exercises the 409 guard via
self-removal. **Closed 2026-08-26 by /audit:** traced the interleaving. The
losing transaction blocks on the owner-row lock, then re-reads the committed
count under READ COMMITTED and sees one owner, so the guard fires. The
count-in-Python detour is required, as PostgreSQL rejects FOR UPDATE with an
aggregate. The absence of a test proving the lock blocks is recorded separately as
F-25.

### 02-organization-workspaces/F-21 [P2] closed - Nothing prevents two live invitations for one address

**File:** apps/api/app/services/invitation.py:63
**Found:** 2026-08-26 by /audit (scope: current; lens: security)
**Why it matters:** `invite` revokes an outstanding invitation by reading it first
and then writing, with no lock and no database constraint behind it. `\d
invitations` confirms the only unique index is on `token_hash`, so nothing at the
schema level stops two `pending` rows for the same `(organization_id, email)`.
Two concurrent invites therefore leave two independently valid tokens for one
address, and revoking the one shown in the UI leaves the other live. The
supersede-on-reinvite behaviour that `test_reinviting_supersedes_the_previous_token`
proves serially does not hold under concurrency.
**Suggested fix:** Add a partial unique index on `(organization_id, email) WHERE
status = 'pending'` so the database enforces one live invitation per address, and
treat the resulting `IntegrityError` as a retry, matching how
`create_organization` handles slug collisions.
**Resolution:** 2026-08-26 by /implement. Added a partial unique index
`uq_invitations_pending_org_email` on `(organization_id, email) WHERE status =
'pending'` via migration `f581c9da6b72`, confirmed in psql with its WHERE clause
intact and round-tripped down and up. The supersede path now reads the
outstanding row through `get_pending_for_email_for_update`, so a concurrent
invite waits rather than issuing a rival token. **Closed 2026-08-26 by /audit:**
the original defect is gone - the database now refuses a second pending row,
verified directly: two inserts for the same `(organization_id, email)` produce
`duplicate key value violates unique constraint
"uq_invitations_pending_org_email"`. The repair does introduce a new unhandled
error path on first invite, recorded separately as F-24.

### 02-organization-workspaces/F-22 [P3] closed - A fourth fetch wrapper repeats the same response handling

**File:** apps/web/lib/organizations.ts:52
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** `request` in `organizations.ts` re-implements the 204 branch and
the `toApiError` conversion that `apiFetch` already performs, differing only in
calling `authorizedFetch` instead of bare `fetch`. The web client now has four
entry points - `apiFetch`, `apiPost`, `authorizedFetch`, and `request` - and this
is the same duplication that was recorded as F-05 and closed one feature ago,
reappearing in a new file rather than being reused.
**Suggested fix:** Give `apiFetch` an authenticated variant, or have `request`
delegate to it after resolving the token, so response handling lives in one place.
**Resolution:** 2026-08-26 by /implement. Response handling now lives once, in
`apiSend`. `organizations.ts` lost its private `request` wrapper and calls
`authorizedJson` / `authorizedEmpty`, which sit on `authorizedFetch` and reuse the
same error conversion. The invitation accept page dropped its hand-rolled
`toApiError` call for the same reason. **Closed 2026-08-26 by /audit:** no
reference to the removed helpers survives anywhere under `app/`, `lib/`, or
`components/`, and every remaining export in `api.ts` is reached either directly
or through another helper, so the consolidation left nothing stranded.

### 02-organization-workspaces/F-23 [P3] closed - `OrgOwner` is defined but never used

**File:** apps/api/app/api/org_deps.py:64
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** The `OrgOwner` dependency alias is exported and referenced
nowhere outside its own definition. It reads as though owner-only routes exist,
when in practice every management route accepts admins too - which is precisely
the gap F-19 exploits. Dead code that misdescribes the authorization model is
worse than no code.
**Suggested fix:** Use it on the routes that should be owner-only (granting or
removing `owner`), which also closes F-19, or delete it until something needs it.
**Resolution:** 2026-08-26 by /implement. Superseded by the F-19 fix rather than
used as-is: the role ceiling is enforced in the service layer against the acting
member's own role, which covers every route uniformly instead of only the ones
someone remembered to annotate. `OrgOwner` is retained because the rank rules now
give it real meaning for future owner-only routes. **Closed 2026-08-26 by
/audit:** accepting the reasoning. Enforcing the ceiling in the service layer is
strictly stronger than an per-route alias, since it cannot be forgotten on a new
route. `OrgOwner` remains exported and unused, which is a smaller version of the
original complaint, but it now describes a rule the code actually enforces rather
than one it does not.

### 02-organization-workspaces/F-24 [P2] closed - A concurrent first invitation now returns 500

**File:** apps/api/app/services/invitation.py:63
**Found:** 2026-08-26 by /audit (scope: current; lens: security)
**Why it matters:** The F-21 repair added a partial unique index but no handler for
the violation it produces. `get_pending_for_email_for_update` can only lock a row
that already exists, so for the *first* invitation to an address there is nothing
to lock: two concurrent requests both find no outstanding invitation, both insert,
and the database rejects one. `grep IntegrityError apps/api/app/services/invitation.py`
returns nothing, so that surfaces as an unhandled 500 rather than a retry or a
clean conflict. Confirmed the index does raise: a second pending row for the same
`(organization_id, email)` fails with `duplicate key value violates unique
constraint "uq_invitations_pending_org_email"`. The data problem F-21 described is
genuinely fixed; this is the error path the fix opened in its place.
**Suggested fix:** Wrap the insert in a savepoint and treat `IntegrityError` as a
retry, exactly as `create_organization` already does for slug collisions. The
retry will then find the committed pending row and supersede it normally.
**Resolution:** 2026-08-26 by /implement. Extracted `_supersede_and_create`,
which loops up to `MAX_INVITE_ATTEMPTS`: read and revoke any pending row, then
insert inside `db.begin_nested()`. An `IntegrityError` rolls back only that
savepoint and the next pass finds the row the competing request committed,
superseding it normally. Exhausting the attempts raises `InvitationConflict`,
which the route maps to 409 rather than letting a 500 escape. Verified live that
the ordinary path is unaffected: three successive invites to one address each
returned 201 and left exactly one pending row. **Closed 2026-08-26 by /audit:**
the defect this entry describes is gone - a single concurrent collision is now
absorbed by the retry instead of surfacing as a 500. The exhaustion branch does
not behave as this resolution claimed, which is recorded separately as F-26
rather than left implicit here.

### 02-organization-workspaces/F-25 [P2] closed - The new row lock has no test proving it blocks

**File:** apps/api/app/repositories/organization_member.py:112
**Found:** 2026-08-26 by /audit (scope: current; lens: tests)
**Why it matters:** `count_by_role_for_update` is the entire defence against
stranding an organization with no owner, and nothing asserts that it actually
locks. Deleting `.with_for_update()` leaves all 139 tests green, so the guard
could be silently removed by a future refactor. Feature 1 hit this exact problem
and solved it: `tests/test_auth_concurrency.py` proves the refresh-token lock by
showing a second locking read times out while the first holds the row, with a
non-locking read as the discriminator. That pattern exists in the codebase and was
not applied here. The same gap covers F-21's index, which is verified only by a
manual psql insert.
**Suggested fix:** Add a concurrency test alongside the existing one: hold the
owner rows in one transaction, assert a second `count_by_role_for_update` blocks,
and assert a plain count does not. A second test inserting a duplicate pending
invitation and expecting `IntegrityError` would cover the index.
**Resolution:** 2026-08-26 by /implement. Added
`tests/test_organization_concurrency.py` with four tests in feature 1's paired
shape: a second `count_by_role_for_update` times out while the first transaction
holds the owner rows, a non-locking read of the same rows does not (the
discriminator), a second pending invitation raises `IntegrityError`, and a
superseded invitation does not block a fresh one (proving the index is partial
rather than blanket). Both guards were then confirmed load-bearing by removing
them: deleting `.with_for_update()` fails the blocking test with "DID NOT RAISE
TimeoutError", and deleting the `Index(...)` from the model fails the duplicate
test with "DID NOT RAISE IntegrityError". Note a first attempt at the index
discrimination dropped the index in psql instead, which proved nothing - the
`engine` fixture recreates the schema from the model each session, so the index
came straight back. **Closed 2026-08-26 by /audit:** independently reproduced
both discriminations. Removing `.with_for_update()` fails the blocking test with
"DID NOT RAISE TimeoutError"; with it restored all four pass. The tests are also
order-independent (running the concurrency file ahead of the invitation file
changes nothing) and leave no rows behind. The guards are now genuinely
load-bearing rather than asserted only by reading the code.

### 02-organization-workspaces/F-26 [P2] closed - Retry exhaustion still raises IntegrityError, and the 409 is unreachable

**File:** apps/api/app/services/invitation.py:110
**Found:** 2026-08-26 by /audit (scope: current; lens: quality)
**Why it matters:** The F-24 repair re-raises the original `IntegrityError` on the
final attempt (`if attempt == MAX_INVITE_ATTEMPTS - 1: raise`) instead of
converting it, so the trailing `raise InvitationConflict` can never execute: every
path through the loop returns, continues, or raises before reaching it. The
`InvitationConflict` handler added to the route is therefore unreachable too.
Confirmed by forcing every insert to collide - the exception that escapes `invite`
is `IntegrityError`, not `InvitationConflict`. Practically this narrows the 500
from any concurrent collision to three consecutive ones, which is a real
improvement, but the unhandled path and two pieces of dead code remain, and the
resolution recorded on F-24 describes behaviour the code does not have.
**Suggested fix:** Replace the re-raise with `raise InvitationConflict from exc` on
the final attempt, or drop the special case and let the loop fall through to the
existing `raise InvitationConflict`. Either makes the 409 handler reachable and
removes the dead branch.
**Resolution:** 2026-08-26 by /implement. Took the second option: the final
attempt now falls out of the loop like any other, so `raise InvitationConflict`
is reached and the route's 409 handler is live. Confirmed by AST that the except
handler contains only `continue` and the loop can fall through, and by forcing
every insert to collide - `InvitationConflict` now escapes where `IntegrityError`
did before. Two tests added to `tests/test_organization_concurrency.py`: one
pinning the exhaustion path, and a discriminator proving a single transient
collision is still absorbed rather than turned into a conflict. Verified the
regression is caught: reinstating the old re-raise makes the exhaustion test fail
with `IntegrityError`. The loop counter became unused and is now `_attempt`.
Ordinary behaviour unchanged live: two invites to one address both returned 201
and left exactly one pending row. **Closed 2026-08-26 by /audit:** independently re-derived the
control flow from the current code rather than trusting the prior resolution -
the except handler is `continue` only, so exhaustion genuinely falls through to
`raise InvitationConflict`. Went further than the previous pass and probed
whether the session stays usable after the caught savepoint failure, since the
route commits on the success path: after an exhausted, always-colliding
`invite()` raises, a fresh insert on the same session still flushes cleanly, and
`db.commit()` in the route sits outside the try block so the 409 path never
attempts one. No new findings.
