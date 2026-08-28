# Feature: Validated organization and workspace settings

**From build-plan:** feature 8b
**Status:** not started

## Goal

Replace the unvalidated `settings` JSONB blob on `Organization` and `Workspace` with a
real, validated shape. This is the load-bearing decision item 8 flagged: which fields
live at the organization level versus the workspace level, because later features read
these values directly — item 11b (assistant business-hours behavior), item 29 (forward
outside hours), and item 45 (timezone-aware campaign windows) all depend on this split
being right the first time.

Backend only. The forms that edit these values are 8c.

## Design reference

None. No UI ships in this feature.

## In scope

- `app/schemas/settings.py` — new module: `OrganizationSettings` /
  `OrganizationSettingsUpdate`, `WorkspaceSettings` / `WorkspaceSettingsUpdate`,
  `BusinessHoursWindow`, `DAYS_OF_WEEK`.
- `organization_service.update_organization` — new service function (the route
  currently calls `organization_repo` directly; this feature adds real business logic
  - a partial-merge-then-validate - so it earns a service layer, matching
  `workspace_service.update_workspace`'s existing shape).
- `workspace_service.update_workspace` — extended with the same merge-then-validate
  logic for `WorkspaceSettings`.
- `PATCH /organizations/{id}` and `PATCH /organizations/{id}/workspaces/{id}` — same
  routes, same URLs; their `settings` field now validates instead of accepting
  anything JSON-serializable.
- `OrganizationResponse.settings` / `WorkspaceResponse.settings` typed as the real
  models instead of `dict[str, Any]`.
- Organization and workspace creation stamp real validated defaults instead of relying
  on the `'{}'::jsonb` column default.
- One Alembic data migration backfilling every existing row's `settings` from `{}` (or
  any pre-validation shape) to the validated default.
- pytest coverage for validation, the merge semantics, creation-time defaults, and the
  backfill.

## Out of scope

- **Inheritance between organization and workspace settings.** Each workspace stores
  its own complete `timezone`/`locale`/`business_hours` - it does not fall back to an
  organization-level default. There is no organization-level equivalent of those three
  fields to inherit from; see Data / contracts for why the fields split this way.
- **Per-day partial merge of `business_hours`.** A `settings` PATCH that includes
  `business_hours` replaces the whole object, not one day within it. Deep-merging a
  single day is a client-side (8c) concern if it turns out to matter.
- **Actual currency conversion or Stripe wiring.** `currency` is a stored preference
  field only; billing integration is item 55.
- **A settings-change audit trail.** `AuditLog` (item 51) is a real, planned entity;
  this feature does not add an ad-hoc one early.
- **All UI.** That is 8c.
- **Any change to `PATCH .../workspaces/{id}/members` or org/workspace membership
  routes.** Untouched by this feature.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Organization settings validation** - add `OrganizationSettings`
  (`currency`, validated against a small allow-list) and `OrganizationSettingsUpdate`
  to `app/schemas/settings.py`. Add `organization_service.update_organization`: fetch
  the organization, merge only the fields present in the partial update
  (`exclude_unset`) onto the existing stored `settings` dict, validate the merged
  result through `OrganizationSettings`, store `model_dump(mode="json")`. Change
  `OrganizationUpdate.settings` and `OrganizationResponse.settings` to the new types.
  Change the `PATCH /organizations/{id}` route to call the new service function
  instead of `organization_repo` directly.
  *Done when:* `pytest` passes with tests proving: a valid `currency` persists; an
  unsupported currency code returns 422; omitting `settings` entirely on a
  name-only PATCH leaves the stored `currency` untouched; `GET` after PATCH reflects
  the change; and the full existing `test_organizations.py` /
  `test_organization_members.py` suite still passes unchanged (the route's URL,
  method, and non-settings behavior do not move).

- [x] **Step 2 - Workspace settings validation** - add `WorkspaceSettings`
  (`timezone` validated against `zoneinfo.available_timezones()`, `locale` validated
  against a BCP-47-lite pattern, `business_hours` as an optional dict keyed by
  `DAYS_OF_WEEK` to a `BusinessHoursWindow | null`) and `WorkspaceSettingsUpdate` to
  the same module. Extend `workspace_service.update_workspace` with the identical
  merge-then-validate logic from Step 1. Change `WorkspaceUpdate.settings` and
  `WorkspaceResponse.settings` to the new types.
  *Done when:* `pytest` passes with tests proving: a valid `timezone` persists; an
  unknown IANA zone name returns 422; a malformed `locale` returns 422; a
  `business_hours` entry with `close` not after `open` returns 422; an unknown day key
  (e.g. `"funday"`) returns 422; sending only `timezone` leaves `locale` and
  `business_hours` untouched (the same partial-merge proof as Step 1, now with three
  fields instead of one); and the full existing `test_workspaces.py` suite still
  passes unchanged.

- [x] **Step 3 - Creation-time defaults and backfill** - `organization_repo.create`
  and `workspace_repo.create` accept an explicit `settings: dict[str, Any]` argument
  (no default value - every call site must decide, catching a forgotten call site at
  review time rather than silently falling through to the raw `'{}'::jsonb` column
  default); `organization_service.create_organization` and
  `workspace_service.create_workspace` pass
  `OrganizationSettings().model_dump(mode="json")` /
  `WorkspaceSettings().model_dump(mode="json")`. Add one Alembic migration with a data
  update (not a schema change) setting `settings` to the same validated defaults for
  every existing row where `settings = '{}'::jsonb`.
  *Done when:* `pytest` passes with tests proving a newly created organization's
  `settings` already contains `{"currency": "USD"}` (no PATCH needed first), a newly
  created workspace's `settings` already contains the three default fields, and
  `alembic upgrade head` / `alembic downgrade -1` / `alembic upgrade head` round-trips
  cleanly against the dev database with pre-existing `{}` rows (seed one manually
  before the round-trip to prove the backfill actually ran, not just that the
  migration applies).

## Files / areas

**New**
- `apps/api/app/schemas/settings.py`
- `apps/api/alembic/versions/<hash>_backfill_settings_defaults.py`
- `apps/api/tests/test_organization_settings.py`
- `apps/api/tests/test_workspace_settings.py`

**Modified**
- `apps/api/app/schemas/organization.py` — `OrganizationUpdate.settings`,
  `OrganizationResponse.settings` retyped.
- `apps/api/app/schemas/workspace.py` — same, for `WorkspaceUpdate` /
  `WorkspaceResponse`.
- `apps/api/app/services/organization.py` — new `update_organization`;
  `create_organization` passes explicit default settings.
- `apps/api/app/services/workspace.py` — `update_workspace` gains merge-then-validate;
  `create_workspace` passes explicit default settings.
- `apps/api/app/repositories/organization.py`,
  `apps/api/app/repositories/workspace.py` — `create()` takes `settings` explicitly.
- `apps/api/app/api/v1/organizations.py` — `update_organization` route calls the new
  service function.
- `apps/api/app/api/v1/workspaces.py` — unchanged call shape, verify it still matches
  `workspace_service.update_workspace`'s signature after Step 2.

**Unchanged**
- The `settings` column itself: still `JSONB`, still `nullable=False`, still
  `server_default text("'{}'::jsonb")` at the DB level. That default becomes a
  defensive fallback only — application code always supplies real defaults at
  creation from Step 3 onward - not something this feature needs to alter, so the
  migration is data-only.
- Everything under `apps/web/` — no frontend change until 8c.

## Data / contracts

**1. The organization/workspace field split, locked:**

```
Organization.settings -> OrganizationSettings
    currency: str = "USD"      # billing-adjacent, one per organization

Workspace.settings -> WorkspaceSettings
    timezone: str = "UTC"                                    # IANA zone name
    locale: str = "en-US"                                     # BCP-47-lite
    business_hours: dict[str, BusinessHoursWindow | None] | None = None
```

**Why this split.** `Organization` owns `Subscription`/billing (locked in feature 1-3's
data model) and nothing else in the current schema is genuinely organization-wide
across locations - a business with two clinics in different time zones needs different
hours and a different zone per clinic, so those live on `Workspace`. `currency` is the
reverse: one organization has one subscription and one billing relationship, so a
per-workspace currency would create an ambiguity items 54-55 would have to resolve
later for no product benefit today. `Campaign` (item 45) already has its own
`timezone` field independent of this - a workspace default does not remove the need
for a per-campaign override later, it just gives item 11b and item 29 something
sensible to read when no more specific value exists yet.

**2. Validation rules, locked:**

- `currency`: one of a small allow-list - `USD`, `EUR`, `GBP`, `CAD`, `AUD` - not open
  ISO 4217 format-matching. A wrong-but-well-formed code stored now would need a data
  migration to fix once item 55 wires up real billing; a tight allow-list that grows
  by editing one tuple is cheaper than that migration.
- `timezone`: must be a real IANA name, checked against
  `zoneinfo.available_timezones()` (Python stdlib, no new dependency).
- `locale`: format-validated only against `^[a-z]{2}(-[A-Z]{2})?$` (e.g. `en`,
  `en-US`), not restricted to English despite "only English ships in the MVP" -
  that constraint is about UI copy (`project-overview.md`'s Localization section), not
  this field, which describes the business's operating locale and will matter to
  prompt/assistant language defaults before the UI is ever translated.
- `business_hours`: keys restricted to `DAYS_OF_WEEK` (lowercase full day names); an
  unrecognized key is a 422, not a silently-ignored extra field. A day mapped to
  `null` means closed that day. The whole field being `null` means "not configured
  yet" - distinct from "closed every day" - so item 29's forwarding logic can tell
  "no rule" from "always forward."

**3. Partial-update semantics, reusing 8a's pattern.** The outer `settings` key on
`OrganizationUpdate`/`WorkspaceUpdate` keeps its existing meaning: omitted means
"don't touch settings at all." When `settings` **is** provided, only the inner fields
actually present in the request (`exclude_unset` on the inner
`*SettingsUpdate` model) are merged onto the existing stored dict; the rest are left
alone. This is the same discipline 8a's `ProfileUpdate` established for nullable
fields, now applied one level deeper. Do not require the whole nested object on every
settings PATCH - that would force a client to resend `locale` and `business_hours`
just to change `timezone`.

**4. Storage format.** The validated Pydantic model is dumped with `mode="json"`
before assignment to the `JSONB` column (so a value like a `date` would serialize
correctly if one is ever added; today every field is already a plain string or
nested dict, so this mostly future-proofs the pattern rather than changing behavior).

## Testing

The backend gate is live - every step ships its tests in the same diff.

**In-scope logic needing tests:**
- `OrganizationSettings`/`WorkspaceSettings` field validators (Steps 1-2).
- The merge-then-validate service logic, proving partial updates don't clobber
  untouched fields (Steps 1-2).
- Creation-time default stamping (Step 3).
- The backfill migration, round-tripped (Step 3).

**Test files:** `test_organization_settings.py` and `test_workspace_settings.py`,
mirroring the existing per-resource test-file convention
(`test_organizations.py`, `test_workspaces.py`) rather than adding to those files
directly, since settings validation is a large enough concern to read on its own.
Reuse `_signed_in`/`_org_with_owner` from `conftest.py`.

**Migration testing:** run `alembic upgrade head`, confirm via a direct query that a
manually-seeded `{}` row was backfilled, `alembic downgrade -1` then
`alembic upgrade head` again to prove the round-trip is clean - matching the existing
project convention from every prior migration in this repo.

**Not tested:** nothing meaningful deferred. No UI in this feature.

**Manual path:** `PATCH /api/v1/organizations/{id}` with
`{"settings": {"currency": "EUR"}}`, then `GET` to see it; try an unsupported
currency and see 422; `PATCH .../workspaces/{id}` with
`{"settings": {"timezone": "America/Chicago"}}` and confirm `locale` and
`business_hours` are unchanged in the response.

## Notes for the AI

- **Follow the layering.** The merge-then-validate logic is real business logic; it
  belongs in the service functions, not the routes and not the repositories. The
  repositories stay dumb setters, unchanged in shape (Steps 1-2 only touch schemas and
  services; Step 3 is the only step that touches the repositories, and only to make
  `settings` an explicit, required creation argument).
- **`organization_service.update_organization` is a new function.** Today the
  `PATCH /organizations/{id}` route calls `organization_repo.update` directly with no
  service in between - that was fine when the route had nothing to decide. It now
  does (the merge), so this is a justified, not premature, new service function. Model
  it on `workspace_service.update_workspace`'s existing shape (fetch, then delegate to
  the repo) rather than inventing a different pattern.
- **Step 3's `settings` becoming a required repository argument is deliberate.** A
  default value there would let a future call site silently fall through to the
  DB-level `'{}'::jsonb` default and skip validation. Requiring it forces every
  creation path to state its default explicitly, and a forgotten call site fails at
  review (or at worst, at the type checker / first test run) instead of silently
  storing an unvalidated row.
- **The migration is data-only, not a schema change.** No column, type, or constraint
  changes - only an `UPDATE ... WHERE settings = '{}'::jsonb`. This is safe under the
  two-plane additive-migration rule (CLAUDE.md §6.2) with no further care needed, but
  double-check the migration only touches rows still at the empty default, never a row
  a user has already customized.
- **Do not touch `Campaign.timezone`** or invent a settings-inheritance mechanism
  connecting it to `Workspace.timezone`. That connection, if any, is item 45's
  decision to make against a real `Campaign` model that does not exist yet.
- **`currency`'s allow-list lives as a plain tuple constant**, not a database table or
  a new dependency - extending it later is a one-line diff. Match the project's
  existing preference (CLAUDE.md §6.2, §29) for small explicit constants over
  premature configurability.
