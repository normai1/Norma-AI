# Feature: Configuration schema

**From build-plan:** feature 11b
**Status:** not started

## Goal

`AssistantVersion`: the validated, versioned configuration snapshot for an assistant - voice,
language, greeting, persona, speech rate, turn sensitivity, creativity, ambient sound - plus the
ability to save a new version and browse version history. Per the resolved 11b/11c split (see
below), this sub-feature creates the real `AssistantVersion` table now, matching the locked data
model exactly, rather than a throwaway config blob on `Assistant` that 11c would have to migrate
away from.

**Resolved contradiction, confirmed with the user before writing this spec:** the build-plan's
11b/11c one-liners read as if 11c ("Versioning") owns `AssistantVersion` entirely, but
`project-overview.md`'s locked data model puts every field 11b names directly on
`AssistantVersion`. Building 11b's validation against a table that doesn't exist yet, or against
fields bolted onto `Assistant`, would mean real rework in 11c. Resolved: **11b creates
`AssistantVersion`** (table, validation, save/list/get) and adds `Assistant.current_version_id`
as a nullable, additive column that stays unset. **11c** builds the versioning *behavior* on top:
true immutability enforcement, diffing, rollback, and the `POST .../publish` action that actually
sets `current_version_id`. No new build-plan numbering - this is a scope clarification within the
existing 11b/11c split, not a new sub-item.

## Design reference

None. No UI in this sub-feature (11d's).

## In scope

- `AssistantVersion` model: `assistant_id` (FK), `version` (int, unique per `(assistant_id,
  version)`, server-assigned - never client-supplied, never reused), `voice_id` (text, required),
  `language` (text, required), `greeting` (text, required), `persona` (text, optional - a bare
  assistant can function on its default persona), `speech_rate` (numeric, bounded 0.5-2.0,
  default 1.0), `turn_sensitivity` (numeric, bounded 0.0-1.0, default 0.5), `creativity`
  (numeric, bounded 0.0-1.0, default 0.3 - CLAUDE.md's own "bounded temperature" phrase), and
  `ambient_sound` (text, nullable). All numeric bounds enforced at the Pydantic schema layer,
  matching this codebase's existing settings-validation precedent (no DB-level numeric CHECK
  constraints - the string-enum CHECK pattern used for `status` fields doesn't extend to numeric
  ranges anywhere in this codebase yet, and this doesn't need to be the first).
- `Assistant.current_version_id` - nullable UUID FK to `assistant_versions`, additive migration.
  **Stays unset by every route in this sub-feature** - 11c's `/publish` is the only thing that
  ever sets it.
- Create a version (full snapshot - every field required or defaulted, never a partial `PATCH`,
  since a version is a point-in-time snapshot, not a mutable record), list an assistant's
  versions (newest first), get one version by its version number.
- Version numbering: server computes `next = (max existing version for this assistant) + 1`, or
  `1` if none exist. A unique constraint on `(assistant_id, version)` is the DB-level backstop
  against a race; this codebase's realistic concurrency profile (one operator editing one
  assistant at a time) doesn't justify more than that for MVP.
- Same access model as 11a: creating a version needs `CanManageAssistants` (owner/admin);
  listing/getting versions is open to any workspace member via `CurrentWorkspace`, matching how
  assistant read-access already works.

## Out of scope

- **`greeting_interruptible`, `business_hours_behavior`, `fallback_behavior`, `enabled_skills`,
  `prompt_template_id`/`prompt_version`.** All are in the locked data model's full
  `AssistantVersion` shape, but none are named in 11b's own build-plan line, and each has a
  natural home elsewhere: `enabled_skills` is item 33's tool-permission framework,
  `prompt_template_id`/`prompt_version` is item 12's prompt templates (neither exists yet -
  referencing them now would be a dangling forward reference). `greeting_interruptible`,
  `business_hours_behavior`, and `fallback_behavior` have no other owning item, but nothing
  consumes them yet either (item 20's call engine is what would actually read
  `fallback_behavior`/business-hours routing). Adding unused columns speculatively is exactly
  what this project's additive-migration philosophy exists to avoid - add them, additively, when
  the feature that reads them is built.
- **Enforcing true version immutability.** 11b's `create` only ever inserts; nothing in this
  sub-feature updates an existing `AssistantVersion` row, so there's no enforcement gap in
  practice yet, but the explicit guard (reject an update attempt, if one is ever wired) is 11c's
  stated job, not duplicated here.
- **Diffing between two versions, rollback, and `POST .../publish`.** All explicitly 11c's, per
  the resolved split above.
- **Validating `voice_id` against the real, live voice catalogue** (item 10's `GET
  /api/v1/voices`). "Validated" here means required, well-typed, and bounded where numeric - not
  cross-referenced against a live, potentially paid provider call on every version save. Revisit
  if a real "you picked an unknown voice" bug ever shows up; item 11d's editor UI is the more
  natural place to prevent that at the source (a picker populated from item 10's catalogue can't
  submit an invalid id in the first place).
- **The editor UI** - 11d's.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Model, migration, exceptions** - `app/models/assistant_version.py`
  (`AssistantVersion`, matching `Assistant`'s exact structural conventions); a migration adding
  the `assistant_versions` table (unique constraint on `(assistant_id, version)`, FK to
  `assistants` with `ondelete="CASCADE"`) **and**, in the same migration, the additive
  `Assistant.current_version_id` column (nullable UUID FK to `assistant_versions`, no `ondelete`
  cascade - archiving/deleting a version should never be a thing that also breaks the assistant
  row FK-wise, though nothing sets or deletes it yet); `AssistantVersionNotFound` in
  `app/core/exceptions.py`.
  *Done when:* `alembic upgrade head` applies cleanly from the current head and `alembic
  downgrade -1` reverses cleanly; `ruff check apps/api` clean.

  **Two real bugs found and fixed during this step**, both from the genuine circular FK between
  `assistants` and `assistant_versions` (`assistant_versions.assistant_id` -> `assistants.id`,
  `assistants.current_version_id` -> `assistant_versions.id`): (1) Alembic's autogenerated
  downgrade used an unnamed constraint drop (`op.drop_constraint(None, ...)`), which raises a
  `CompileError` at runtime - fixed by naming the FK constraint explicitly
  (`fk_assistants_current_version_id`) in both the migration and the model. (2)
  `Base.metadata.create_all()`/`drop_all()` (used by the dev/test database bootstrap and the
  test fixtures) could not resolve table-creation order at all with a true circular FK - fixed
  with `use_alter=True` on `Assistant.current_version_id`'s `ForeignKey`, the standard SQLAlchemy
  way to break a circular dependency by creating that one constraint as a separate `ALTER TABLE`
  after both tables exist. Both the dev and test databases had stale schema from before this fix
  (auto-generated constraint names); renamed both in place rather than dropping data.

- [x] **Step 2 - Repository and service** - `app/repositories/assistant_version.py` (`create`,
  `get_by_version`, `list_for_assistant`, `next_version_number`); `app/services/assistant_version.py`
  reusing 11a's assistant-resolution scope check (organization -> workspace -> assistant) before
  computing the next version number and inserting. `assistant_service`'s `_resolve_assistant` was
  renamed to `resolve_assistant` (no leading underscore) since it's now a shared internal helper
  across the assistant domain's service modules, not private to one file.
  *Done when:* `ruff check apps/api` clean. No test yet, matching 11a's Step 2 precedent - proven
  through the route layer in Step 3.

- [x] **Step 3 - Schemas, routes, and tests** - `app/schemas/assistant_version.py`
  (`AssistantVersionCreate` with all the bounded/required fields above, `AssistantVersionResponse`
  including the server-assigned `version` and `created_at`); routes in a new
  `app/api/v1/assistant_versions.py`, nested under `.../assistants/{assistant_id}/versions`,
  matching 11a's exact dependency and error-mapping style.
  *Done when:* `pytest apps/api/tests/test_assistant_versions.py` (new) passes, proving: creating
  a version succeeds for an owner and returns `version: 1`; a second create on the same assistant
  returns `version: 2`; a member gets 403 on create but can list/get (after an explicit workspace
  membership grant - own test bug caught and fixed: a "member" role needs an explicit
  `WorkspaceMember` row to pass `CurrentWorkspace`'s access check, the same as every other
  workspace-scoped read test in this codebase); an unauthenticated request gets 401; out-of-bounds
  `speech_rate`/`turn_sensitivity`/`creativity` are rejected with 422; listing an assistant with no
  versions returns `[]`; getting a nonexistent version number returns 404; a version in one
  assistant is not reachable through a sibling assistant's URL. Full backend suite still green
  (324/324). `ruff check apps/api` clean.

## Files / areas

**New**
- `apps/api/app/models/assistant_version.py`
- `apps/api/alembic/versions/a9c2aadaa88d_assistant_versions.py`
- `apps/api/app/repositories/assistant_version.py`
- `apps/api/app/services/assistant_version.py`
- `apps/api/app/schemas/assistant_version.py`
- `apps/api/app/api/v1/assistant_versions.py`
- `apps/api/tests/test_assistant_versions.py`

**Modified**
- `apps/api/app/models/assistant.py` - adds `current_version_id` (nullable FK, `use_alter=True`).
- `apps/api/app/core/exceptions.py` - adds `AssistantVersionNotFound`.
- `apps/api/app/db/base.py` - registers `AssistantVersion`.
- `apps/api/app/main.py` - registers the new router.
- `apps/api/app/services/assistant.py` - `_resolve_assistant` renamed to `resolve_assistant`.

**Unchanged**
- No frontend file. No `/publish`. No diffing or rollback. `Assistant.status` never changes here.

## Data / contracts

**`AssistantVersion`** - `id` (UUID PK), `assistant_id` (UUID FK, indexed, CASCADE), `version`
(int, unique with `assistant_id`), `voice_id`/`language`/`greeting` (text, NOT NULL), `persona`
(text, nullable), `speech_rate`/`turn_sensitivity`/`creativity` (numeric, NOT NULL, with the
bounds and defaults above enforced at the schema layer), `ambient_sound` (text, nullable),
`created_at`/`updated_at`. **Locked for 11c**: 11c must not need to alter any column defined
here - it only adds behavior (immutability enforcement, diff, rollback, publish) on top.

**`Assistant.current_version_id`** - nullable UUID FK to `assistant_versions`, added here,
**set by nothing until 11c's `/publish` exists.** Every response in this sub-feature that
includes it must show `null`.

**`POST/GET .../assistants/{assistant_id}/versions[/{version}]`** - request/response as in Step
3. `version` in the URL is the integer version number, not the row's UUID `id` - matching how an
operator would actually refer to "version 3."

**`resolve_assistant`** (in `app/services/assistant.py`, no longer private) - the shared
scope-resolution helper (organization -> workspace -> assistant) every assistant-domain service
should call, not reimplement. `assistant_version` service is its second consumer; treat it the
same way going forward.

## Testing

The backend gate is live - every step ships its tests in the same diff where the logic becomes
observable (Step 3), matching 11a's own precedent.

**In-scope logic needing tests:** version-number assignment (sequential, per-assistant, starting
at 1), every field's validation bound, the full 401/403/404 and cross-scope matrix inherited from
11a's resolution chain, and the empty-list first-run case.

**No frontend tests** - no frontend code in this sub-feature.

## Notes for the AI

- **Reuse 11a's scope-resolution shape, don't reinvent it.** `assistant_version` service
  functions call `assistant_service.resolve_assistant` before touching versions - an assistant
  that fails that check must never let a version operation proceed.
- **`version` is server-assigned, always.** `AssistantVersionCreate` never accepts a `version`
  field as input - don't accept and then ignore it, which would silently mislead a caller who
  thinks they picked the version number.
- **Every version create is a full snapshot.** No partial-update endpoint for a version - if a
  future step is tempted to add a `PATCH` here, that's 11c's immutability rule already being
  violated before 11c even lands.
- **Don't set `current_version_id` anywhere in this sub-feature.** Even though the column exists
  after Step 1, no route in Step 3 touches it - that write belongs entirely to 11c's `/publish`.
- **Don't add `enabled_skills`, `prompt_template_id`/`prompt_version`,
  `business_hours_behavior`, `fallback_behavior`, or `greeting_interruptible`.** See Out of
  scope - each belongs to a different, not-yet-built feature.
- **A circular FK between two tables needs `use_alter=True` on one side, and both the create and
  drop constraint operations in any hand-adjusted migration need an explicit constraint name.**
  Autogenerate's `None`-named `drop_constraint`/`create_foreign_key` calls will compile-error on
  downgrade otherwise. This will matter again if a future feature adds another circular
  reference.
- Continuing straight through 11c and 11d after this one completes, per the "entire step 11 in a
  single go" instruction - each still gets its own spec, branch, review, and merge.
