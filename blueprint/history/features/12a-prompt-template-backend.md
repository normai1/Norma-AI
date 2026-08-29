# Feature: Prompt template backend

**From build-plan:** feature 12a
**Status:** not started

## Goal

The data model and API for reusable, versioned prompt templates: `PromptTemplate` (the
top-level, named, use-case-tagged resource) and `PromptVersion` (an immutable content
snapshot, exactly mirroring how `AssistantVersion` works). This sub-feature builds the whole
backend - CRUD, immutable versioning, publish/rollback, and diff - in one pass, since items
11a-11c already proved this exact pattern end to end; there is no remaining design risk to
de-risk by splitting it into three separate merges again.

## Design reference

None. Backend-only; no UI in this sub-feature (12c's).

## In scope

- **`PromptTemplate`** - `organization_id`, `workspace_id` (scoped exactly like `Assistant`),
  `name` (text, required), `use_case` (text, required, free-form - "receptionist", "support",
  "scheduling", "answering machine", "field service", "order intake" are the named examples in
  CLAUDE.md and the build plan, but nothing in either document closes the set, so this is a
  plain validated string, not a DB enum that would need a migration for every new use case),
  `status` (`draft`/`published`/`archived`, identical lifecycle to `Assistant`),
  `current_version_id` (nullable FK -> `prompt_versions.id`, `use_alter=True`, named
  constraint - the exact fix 11b had to discover the hard way for the same circular-FK shape,
  applied from the start here. Landing this column requires `prompt_versions` to already
  exist, so - mirroring 11a/11b exactly - it is added as an additive column in **Step 2**, not
  present on `PromptTemplate` in Step 1 at all).
- **`PromptVersion`** - `prompt_template_id` FK -> `prompt_templates.id` (CASCADE), `version`
  (int, unique per template), `content` (text, required, the template body - the literal
  `{{namespace.field}}` placeholders 12b's renderer will consume, though this sub-feature does
  not parse or validate placeholder syntax at all; `content` is opaque text here). Immutable via
  a `before_update` listener raising `PromptVersionImmutable`, identical to `AssistantVersion`.
- **CRUD**: create, list, get, rename, archive a `PromptTemplate` - same route shapes, same
  `CanManagePromptTemplates` (owner/admin) for mutations, same `CurrentWorkspace` (any member)
  for reads, same 404-not-403 cross-tenant behavior as `assistants.py`.
- **Versions**: create (save a new content snapshot), list, get one by version number.
- **Publish (= rollback)**: `POST .../prompt-templates/{id}/publish` with `{"version": int}`,
  identical semantics to `Assistant`'s publish - sets `current_version_id`, flips `draft` ->
  `published`, idempotent, rejects an archived template with 409.
- **Diff**: `GET .../prompt-templates/{id}/versions/{from}/diff/{to}` - the one diffable field
  is `content` (there is nothing else on `PromptVersion` to diff), same
  `{"previous": ..., "current": ...}` shape 11c locked.
- A new `MANAGE_PROMPT_TEMPLATES` permission, granted to owner/admin only, matching
  `MANAGE_ASSISTANTS` exactly.

## Out of scope

- **Variable interpolation.** Rendering `{{namespace.field}}` placeholders against real
  assistant/workspace/caller data is 12b's job - a pure function with its own tests, decoupled
  from this CRUD layer. This sub-feature stores `content` as opaque text.
- **Wiring `AssistantVersion.prompt_template_id`/`prompt_version`.** Also 12b - a separate
  additive migration on a different table, not needed for prompt templates to exist and be
  versioned on their own.
- **The editor UI.** 12c's, mirroring 11d.
- **A closed `use_case` enum or a seeded catalogue of the six named templates.** Nothing in
  CLAUDE.md, the build plan, or `project-overview.md` describes an onboarding wizard or an
  admin-owned global catalogue as a build-plan item yet, and fabricating production prompt
  copy for "field service", "order intake", etc. is real product content someone should
  actually write, not something to invent here. Operators create their own templates through
  this CRUD, same as they create their own assistants.
- **Un-archiving, un-publishing.** Same deliberate absence as `Assistant` (11a/11c).

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `PromptTemplate` model and CRUD** - `app/models/prompt_template.py`
  (mirrors `assistant.py`'s `organization_id`/`workspace_id`/`name`/`status` shape and CHECK
  constraint; no `current_version_id` yet - see the model's own docstring); migration;
  `app/core/exceptions.py` gets `PromptTemplateError`, `PromptTemplateNotFound`; `app/core/
  permissions.py` gets `MANAGE_PROMPT_TEMPLATES` (added to `_ELEVATED`, and this is the moment
  to fix that module's docstring - still naming the abandoned CRM/RAG entities per open finding
  F-32 - to name real ones, since I'm already editing the file); `app/api/org_deps.py` gets
  `CanManagePromptTemplates`; `app/repositories/prompt_template.py` (`get_by_id`,
  `list_for_workspace`, `create`, `update_name`, `archive`, `ARCHIVED_STATUS`); `app/services/
  prompt_template.py` (`_resolve_workspace_id`, `resolve_prompt_template`,
  `create_prompt_template`, `list_prompt_templates`, `get_prompt_template`,
  `rename_prompt_template`, `archive_prompt_template`); `app/schemas/prompt_template.py`
  (`PromptTemplateCreate{name, use_case}`, `PromptTemplateUpdate{name}`,
  `PromptTemplateResponse{id, organization_id, workspace_id, name, use_case, status,
  created_at}` - no `current_version_id` yet, matching 11a's own precedent exactly (that field
  was added to `AssistantResponse` only in 11c, once the column existed); `app/api/v1/
  prompt_templates.py` (POST/GET-list/GET-one/PATCH/POST-archive under `/organizations/
  {organization_id}/workspaces/{workspace_id}/prompt-templates`); registered in `app/main.py`
  and `app/db/base.py`.
  *Done when:* a new `tests/test_prompt_templates.py` passes - create/list/get/rename/archive,
  owner/admin allowed, member/viewer 403, unauthenticated 401, cross-workspace and cross-org
  404s. `ruff check apps/api` clean.

- [x] **Step 2 - `PromptVersion` model and CRUD, and `PromptTemplate.current_version_id`** -
  `app/models/prompt_version.py` (mirrors `assistant_version.py`'s shape minus the
  config-specific fields - just `prompt_template_id`, `version`, `content`); a migration
  adding the `prompt_versions` table AND, in the same migration, the additive
  `current_version_id` column on `prompt_templates` (nullable FK -> `prompt_versions.id`,
  `use_alter=True`, named constraint `fk_prompt_templates_current_version_id` from the start -
  do not repeat 11b's two-bug discovery of an unnamed constraint breaking downgrade and a
  circular-dependency `SAWarning` breaking `create_all`); `app/models/prompt_template.py` gets
  the `current_version_id` column added; `app/core/exceptions.py` gets
  `PromptVersionNotFound`; `app/repositories/prompt_version.py` (`get_by_version`,
  `list_for_template` newest-first, `next_version_number`, `create`); `app/services/
  prompt_version.py` (`create_version`, `list_versions`, `get_version`); `app/schemas/
  prompt_version.py` (`PromptVersionCreate{content}`, `PromptVersionResponse{id,
  prompt_template_id, version, content, created_at}`); `app/api/v1/prompt_versions.py`
  (POST/GET-list/GET-one-by-version under `.../prompt-templates/{prompt_template_id}/
  versions`); registered in `app/main.py` and `app/db/base.py`.
  *Done when:* a new `tests/test_prompt_versions.py` passes - create bumps the version number
  correctly (1, then 2, ...), list returns newest-first, get-by-version 404s for a missing
  version, member read-access holds, owner/admin-only write holds, cross-tenant 404s hold, the
  full suite (`test_prompt_templates.py` included) is still green after both models exist
  together, `alembic upgrade head` / `downgrade -1` / `upgrade head` all succeed. `ruff check
  apps/api` clean.

- [x] **Step 3 - Immutability, publish/rollback, diff** - `app/models/prompt_version.py` gets
  the `before_update` listener raising a new `PromptVersionImmutable`; `app/schemas/
  prompt_template.py` gets `PromptTemplatePublish{version: int}` and `PromptTemplateResponse`
  gets `current_version_id: uuid.UUID | None` (mirroring 11c's own "real gap" fix - the column
  exists since Step 2 but has nothing to observe until `/publish` sets it); `app/schemas/
  prompt_version.py` gets `PromptVersionFieldDiff{previous, current}`,
  `PromptVersionDiffResponse{from_version, to_version, changes}`; `app/repositories/
  prompt_template.py` gets `PUBLISHED_STATUS`, `publish()`; `app/services/prompt_template.py`
  gets `publish_prompt_template` (rejects archived with a new `PromptTemplateArchived`);
  `app/services/prompt_version.py` gets `diff_versions` (the one diffable field is `content`);
  routes: `POST .../prompt-templates/{id}/publish`, `GET .../prompt-templates/{id}/versions/
  {from_version}/diff/{to_version}`.
  *Done when:* a direct-mutation test proves the immutability guard actually fires; publish
  tests mirror `test_assistants.py`'s publish suite exactly (first publish flips status, no-op
  on already-current, 404 on a bad version number, 409 on an archived template, rollback to an
  older version succeeds); diff tests prove a changed-`content` pair returns exactly that field,
  a version diffed against itself returns empty `changes`, either version missing 404s. Full
  backend suite green. `ruff check apps/api` clean.

## Files / areas

**New**
- `apps/api/app/models/prompt_template.py`, `apps/api/app/models/prompt_version.py`
- `apps/api/app/repositories/prompt_template.py`, `apps/api/app/repositories/prompt_version.py`
- `apps/api/app/services/prompt_template.py`, `apps/api/app/services/prompt_version.py`
- `apps/api/app/schemas/prompt_template.py`, `apps/api/app/schemas/prompt_version.py`
- `apps/api/app/api/v1/prompt_templates.py`, `apps/api/app/api/v1/prompt_versions.py`
- `apps/api/tests/test_prompt_templates.py`, `apps/api/tests/test_prompt_versions.py`
- Two Alembic migrations (`prompt_templates`, then `prompt_versions` + the `use_alter` FK back
  onto `prompt_templates`).

**Modified**
- `apps/api/app/core/exceptions.py`, `apps/api/app/core/permissions.py` (also repairs F-32),
  `apps/api/app/api/org_deps.py`, `apps/api/app/main.py`, `apps/api/app/db/base.py`.

**Unchanged**
- No frontend file. `apps/api/app/models/assistant_version.py` is not touched here - the
  `prompt_template_id`/`prompt_version` columns land on it in 12b, not this sub-feature.

## Data / contracts

**`PromptTemplateResponse`** - `{id, organization_id, workspace_id, name, use_case, status,
created_at}` through Step 1/2; Step 3 adds `current_version_id` once `/publish` exists to set
it, matching 11a->11c's exact sequencing. `current_version_id` is `null` until `/publish` is
called at least once - locked now since 12c consumes it.

**`PromptVersionResponse`** - `{id, prompt_template_id, version, content, created_at}`.

**`PromptVersionDiffResponse`** - `{from_version, to_version, changes: {"content":
{"previous": ..., "current": ...}}}` when they differ, `{}` when they don't. Same shape as
`AssistantVersionDiffResponse`, load-bearing for 12c's diff view.

## Testing

The backend gate is live - every step ships its tests in the same diff, mirroring
`test_assistants.py`/`test_assistant_versions.py`'s exact coverage shape: success paths,
403/401, cross-tenant 404s, idempotency, and the immutability/publish/diff edge cases.

## Notes for the AI

- **This is a known pattern, not a fresh design.** Copy `assistant.py`/`assistant_version.py`'s
  model, repository, service, schema, and route shapes as closely as the field differences
  allow. Where something must differ (fewer fields, no config validation bounds), keep the
  difference minimal and explained, not a reinterpretation of the pattern.
- **Apply the circular-FK fix from the start.** `use_alter=True` plus an explicit constraint
  name on `PromptTemplate.current_version_id`'s `ForeignKey`, in both the model and the
  migration, from Step 1/2 - not discovered again the hard way.
- **`use_case` is a plain string, not an enum.** Do not add a CHECK constraint closing it to
  the six named examples; that would block a legitimate seventh use case from being created
  through the UI CLAUDE.md's own onboarding description implies will exist eventually.
- **Publish is rollback**, exactly as 11c established - no second endpoint.
- **`content` is opaque here.** Do not parse, validate, or interpret `{{...}}` placeholders in
  this sub-feature; that is 12b's pure-function job, kept decoupled so it can be unit-tested
  without a database.
- Continuing straight through 12b, 12c, 13a, 13b after this one completes, per the "entire
  step 12 and step 13 in one go" instruction - each still gets its own spec, branch, and merge,
  just without a pause for interactive approval at each merge.
