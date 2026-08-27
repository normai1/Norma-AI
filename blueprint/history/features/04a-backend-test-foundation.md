# Feature: Backend test foundation

**From build-plan:** feature 4a (under 4. Automated testing foundation)
**Status:** not started

## Goal

Consolidate the test-helper duplication `/audit` already flagged (F-27), then use that shared
foundation to add the one thing no existing test file proves on its own: that a user who belongs to
one organization cannot reach another organization's data through any existing endpoint. Also bring
`coding-standards.md`'s Testing section - still the unmodified Next.js/Prisma template stub - in
line with the pytest conventions this project has actually used since feature 1.

## In scope

- Extracting the byte-identical `_signed_in` helper (duplicated across five test files) and the two
  genuinely-identical `_org_with_owner` copies (`test_invitations.py`, `test_organization_members.py`)
  into `tests/conftest.py`, fixing F-27's core duplication.
- A new, consolidated cross-organization tenant-isolation regression suite covering every existing
  `organization_id`-scoped route.
- Tuning `coding-standards.md`'s Testing section to describe this project's actual pytest
  conventions and to record the provider-mock rule future features must follow.

## Out of scope

- **Frontend test tooling** (Vitest, React Testing Library, Playwright) - split to feature 4b.
- **Provider mocks themselves.** `MockEmbeddingProvider` is already a named convention in the
  plans, but no real provider exists in the repository yet (STT, TTS, LLM, and telephony
  abstractions are items 9, 18, and 23). There is nothing to mock today; Step 3 records the rule
  that each of those features ships its `Mock*` implementation in the same diff as the real one,
  rather than inventing a mock for a provider that doesn't exist.
- **Unifying `_org_with_role` and `_org_with_second_member`.** F-27's own note flagged their
  differing return shapes (2-tuple vs. 4-tuple) as "not as mechanical" - forcing a signature merge
  for a P3 duplication finding risks introducing a subtle bug in two already-correct, well-tested
  files for marginal benefit. Left as-is; recorded in F-27's resolution.
- **Workspace-level tenant scoping.** `Workspace`/`WorkspaceMember` don't exist yet (item 6). This
  suite covers organization-level isolation, which is everything the repository has today.
  Extending it to workspaces is item 6 or 8's job once that model ships.
- **A CI-enforced coverage threshold or a new `Verify` command.** That's `/ci` (item 59), not this.
- **`POST /invitations/accept`.** It takes no `organization_id` path parameter and is token-scoped,
  not membership-scoped - its isolation model (the accepting user's email must match the invited
  email) is a different concern, already covered by feature 2c's existing tests.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Extract shared test helpers, fixing F-27** - move `_signed_in(client, email)` into
  `tests/conftest.py` and update the five files that each define their own byte-identical copy
  (`test_organizations.py`, `test_organization_members.py`, `test_organization_authorization.py`,
  `test_invitations.py`, `test_permission_enforcement.py`) to import it instead. Move the two
  identical `_org_with_owner(client, email, name="Test Org")` copies (`test_invitations.py`,
  `test_organization_members.py`) into `conftest.py` the same way; leave the unrelated
  `_org_with_owner` in `test_organization_concurrency.py` alone, since it builds fixtures directly
  against the database rather than through the API and serves a different purpose. Leave
  `_org_with_role` and `_org_with_second_member` where they are (see Out of scope). *Done when:*
  the full backend suite still passes, and `git grep -rn "^async def _signed_in" apps/api/tests`
  outside `conftest.py` returns nothing.

- [x] **Step 2 - Cross-organization tenant-isolation regression suite** - add
  `tests/test_tenant_isolation.py`, built on the Step 1 helpers. Table-driven over the seven
  `organization_id`-scoped routes (`GET /organizations/{id}`, `PATCH /organizations/{id}`,
  `GET /organizations/{id}/members`, `PATCH /organizations/{id}/members/{member_id}`,
  `DELETE /organizations/{id}/members/{member_id}`, `POST /organizations/{id}/invitations`,
  `GET /organizations/{id}/invitations`, `DELETE /organizations/{id}/invitations/{id}`): for each,
  assert a caller who belongs to a different organization gets 404 against another organization's
  id, and a caller who belongs to no organization at all gets the same 404. Add one further test
  proving `GET /organizations` (list) never includes an organization the caller does not belong to.
  *Done when:* the new tests pass against current code, and temporarily commenting out the
  membership check in `require_org_member` (`app/api/org_deps.py`) makes at least one of them fail -
  proving the suite actually catches the regression it exists to catch, not just passing trivially.
  Revert that temporary change before moving on; it is a verification step, not a code change to ship.

- [x] **Step 3 - Tune coding-standards.md's Testing section** - replace the generic
  Next.js/Prisma/Vitest-only template language with this project's actual backend conventions:
  pytest + httpx, the root `pytest.ini` covering both launch directories, the `engine`/`connection`/
  `db`/`redis_client`/`client` fixtures in `tests/conftest.py`, the shared `_signed_in`/
  `_org_with_owner` helper pattern from Step 1, and the rule that every future provider abstraction
  (STT, TTS, LLM, telephony - items 9, 18, 23) ships a deterministic `Mock*` implementation in the
  same diff that introduces the real provider, matching the already-decided `MockEmbeddingProvider`
  precedent. Leave the frontend testing subsection marked not-yet-configured; feature 4b owns it.
  *Done when:* the section describes pytest as this project's actual, already-running backend
  runner rather than a hypothetical stack-swap example, names the real fixture file, and states the
  mock-ships-with-every-provider rule.

## Files / areas

| Path | Change |
| --- | --- |
| `apps/api/tests/conftest.py` | edit - shared `_signed_in`, `_org_with_owner` helpers |
| `apps/api/tests/test_organizations.py` | edit - use shared helper |
| `apps/api/tests/test_organization_members.py` | edit - use shared helpers |
| `apps/api/tests/test_organization_authorization.py` | edit - use shared helper |
| `apps/api/tests/test_invitations.py` | edit - use shared helpers |
| `apps/api/tests/test_permission_enforcement.py` | edit - use shared helper |
| `apps/api/tests/test_tenant_isolation.py` | new |
| `blueprint/context/coding-standards.md` | edit - Testing section |

## Data / contracts

None. This feature touches no production schema, API, or stored shape - test infrastructure and
documentation only.

## Testing

This feature *is* test coverage, so Steps 1 and 2 carry their own verification instead of a
separate section restating it:

| Step | Coverage |
| --- | --- |
| 1 | Full existing suite stays green after the extraction; no behavior changes, only where the helpers live |
| 2 | New table-driven suite in `test_tenant_isolation.py`; verified to actually catch a reintroduced regression, not just pass |
| 3 | Documentation only - no test, verified by reading the updated section |

Run with `pytest` from either the repository root or `apps/api` - `pytest.ini` at the root covers
both.

## Notes for the AI

- **`blueprint/context/coding-standards.md` still describes the wrong stack** until Step 3 lands -
  follow `CLAUDE.md` and the patterns already in `apps/api/app/` and `apps/api/tests/` for
  everything before that step.
- **Match the existing layering and style**: two blank lines between top-level definitions, a short
  docstring on multi-line helpers, explicit return types, no em dashes.
- **F-27 resolution**: mark it `fixed` when Step 1 lands, with a resolution note explaining the
  `_org_with_role`/`_org_with_second_member` scope cut. `/audit` closes it on review, same as any
  other repaired finding.
- **The regression suite's job is to fail loudly if tenant isolation ever regresses**, not to
  re-prove authorization logic already covered elsewhere (`test_organization_authorization.py`,
  `test_permission_enforcement.py` already cover role-based permission denial within one
  organization - this feature covers cross-organization access instead, which nothing currently
  tests directly as its own concern).
