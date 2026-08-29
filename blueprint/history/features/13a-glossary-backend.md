# Feature: Glossary backend

**From build-plan:** feature 13a
**Status:** not started

## Goal

`GlossaryEntry` and its CRUD: per-assistant terms, meanings, and phonetic pronunciation
overrides, laying the data foundation for STT keyword biasing and TTS pronunciation - both
already named as "item 13" in the STT provider contract's own docstring (`app/providers/
speech.py`), and both actually wired only once the real-time voice engine (item 20) exists to
call them.

## Design reference

None. Backend-only; no UI in this sub-feature (13b's).

## In scope

- **Scope reconciliation, made explicit rather than silently picked.** The build plan's own
  line for item 13 says "**per-assistant** terms, abbreviations, and phonetic overrides."
  `project-overview.md`'s locked `GlossaryEntry` model, written before assistants existed as a
  concept, lists only `organization_id`/`workspace_id` - no `assistant_id`. The build plan's
  explicit wording wins (source-of-truth hierarchy: the build plan outranks a generated
  reference doc), so `GlossaryEntry` gets `assistant_id` as its real owning scope, with
  `organization_id`/`workspace_id` denormalized alongside it - the same pattern `Chunk` already
  uses in this codebase ("denormalized, NOT NULL - retrieval filters on these directly").
- **`GlossaryEntry`** - `organization_id`, `workspace_id` (denormalized), `assistant_id` (FK ->
  `assistants.id`, CASCADE, indexed - the real scope), `term` (text, required), `meaning` (text,
  nullable), `phonetic_spelling` (text, nullable), `stt_boost_weight` (numeric, bounded
  0.0-1.0, default 0.5 - matching `AssistantVersion`'s existing `Numeric(3, 2)` bounded-weight
  convention; nothing in the current `SpeechToTextProvider.stream()` interface accepts a
  per-keyword weight yet, so this field is stored now and wired once item 20 either extends
  that interface or aggregates weights some other way). `UniqueConstraint(assistant_id, term)`
  - an assistant should not have two conflicting entries for the same term.
- **CRUD, nested under the owning assistant**: `POST/GET .../assistants/{assistant_id}/
  glossary`, `PATCH/DELETE .../assistants/{assistant_id}/glossary/{glossary_entry_id}`. No
  archive/publish lifecycle - unlike `Assistant`/`PromptTemplate`, a glossary entry is a plain
  reference row, not a versioned configuration snapshot; delete is a real hard delete (`204`),
  mirroring `DELETE .../workspaces/{id}/members/{member_id}`'s exact pattern.
- **Permission reuse, not a new permission.** A glossary entry is a sub-resource of a specific
  assistant - the same relationship `AssistantVersion` already has - so mutations reuse the
  existing `CanManageAssistants` dependency exactly as `AssistantVersion`'s create/publish
  routes do, rather than introducing a `MANAGE_GLOSSARY` permission for what is not an
  independently-scoped resource (unlike `PromptTemplate`, which does get its own permission
  because it *is* independently workspace-scoped, not nested under one assistant).
- **A duplicate term is a 409**, following the exact `WorkspaceMemberAlreadyExists` pattern
  (`app/services/workspace.py`): pre-check, then a race-safe `db.begin_nested()` +
  `IntegrityError` catch against the unique constraint, mapped to a new
  `GlossaryEntryAlreadyExists`.

## Out of scope

- **Any actual STT/TTS wiring.** `SpeechToTextProvider.stream()`'s `keywords` parameter is a
  plain `Sequence[str]` today with no per-term weight, and `TextToSpeechProvider.synthesize()`
  has no pronunciation-override parameter at all - both are locked interfaces from feature 9a.
  Extending either interface is a decision for whoever builds item 20 with a real consumer in
  hand, not something to guess at here.
- **The editor UI.** 13b's - a glossary section on the assistant editor page.
- **Bulk import/export of glossary terms.** Not asked for in the build-plan line.
- **A closed enum for anything.** `term`/`meaning`/`phonetic_spelling` are all plain validated
  strings.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `GlossaryEntry` model, migration, exceptions, permission wiring** -
  `app/models/glossary_entry.py`; one migration; `app/core/exceptions.py` gets
  `GlossaryEntryError`, `GlossaryEntryNotFound`, `GlossaryEntryAlreadyExists`; registered in
  `app/db/base.py`.
  *Done when:* `alembic upgrade head` / `downgrade -1` / `upgrade head` all succeed; `ruff
  check apps/api` clean (no tests yet - the model has no behavior of its own until the routes
  exist in Step 2).

- [x] **Step 2 - CRUD** - `app/repositories/glossary_entry.py` (`get_by_id`,
  `list_for_assistant`, `create`, `update`, `delete`); `app/services/glossary_entry.py`
  (`resolve_glossary_entry` reusing `assistant_service.resolve_assistant` for tenant scope,
  then checking `assistant_id` match; `create_glossary_entry`, `list_glossary_entries`,
  `update_glossary_entry`, `delete_glossary_entry`); `app/schemas/glossary_entry.py`
  (`GlossaryEntryCreate{term, meaning, phonetic_spelling, stt_boost_weight}`,
  `GlossaryEntryUpdate` - all fields optional, partial update, `GlossaryEntryResponse{id,
  organization_id, workspace_id, assistant_id, term, meaning, phonetic_spelling,
  stt_boost_weight, created_at}`); `app/api/v1/glossary_entries.py` (POST/GET-list under
  `.../assistants/{assistant_id}/glossary`, PATCH/DELETE under `.../glossary/
  {glossary_entry_id}`, `CanManageAssistants` for mutations, `CurrentWorkspace` for reads,
  404-not-403 cross-tenant); registered in `app/main.py`.
  *Done when:* a new `tests/test_glossary_entries.py` passes - create/list/update/delete,
  owner/admin allowed, member/viewer 403, unauthenticated 401, cross-assistant and cross-
  workspace 404s, a duplicate `term` on the same assistant returns 409, `stt_boost_weight`
  out-of-bounds returns 422, a partial `PATCH` leaves untouched fields untouched, delete
  actually removes the row (a subsequent `GET` 404s) and returns 204. Full backend suite
  green. `ruff check apps/api` clean.

## Files / areas

**New**
- `apps/api/app/models/glossary_entry.py`
- `apps/api/app/repositories/glossary_entry.py`
- `apps/api/app/services/glossary_entry.py`
- `apps/api/app/schemas/glossary_entry.py`
- `apps/api/app/api/v1/glossary_entries.py`
- `apps/api/tests/test_glossary_entries.py`
- One Alembic migration.

**Modified**
- `apps/api/app/core/exceptions.py`, `apps/api/app/main.py`, `apps/api/app/db/base.py`.

**Unchanged**
- No frontend file. `app/core/permissions.py`/`app/api/org_deps.py` are not touched - this
  sub-feature deliberately reuses the existing `CanManageAssistants` dependency rather than
  adding a new permission constant.
- `app/providers/speech.py` is not touched - see Out of scope.

## Data / contracts

**`GlossaryEntryResponse`** - `{id, organization_id, workspace_id, assistant_id, term, meaning,
phonetic_spelling, stt_boost_weight, created_at}`. Locked now since 13b consumes it directly.

## Testing

The backend gate is live - every step ships its tests in the same diff. Step 2's coverage
mirrors the established shape: success paths, 403/401, cross-tenant 404s, the duplicate-term
409, bounds validation, and partial-update semantics.

## Notes for the AI

- **`assistant_id` is the real scope; reuse `assistant_service.resolve_assistant`** for the
  tenant-scoped lookup rather than re-implementing organization/workspace resolution here.
- **Reuse `CanManageAssistants`, do not add a new permission.** Glossary entries are a
  sub-resource of one assistant, the same relationship `AssistantVersion` has.
- **No archive/publish lifecycle.** This is a plain reference table, not a versioned
  configuration snapshot - delete really deletes the row.
- **Mirror `WorkspaceMemberAlreadyExists`'s exact race-safe pattern** for the duplicate-term
  409 (pre-check + `db.begin_nested()` + `IntegrityError` catch), not a bespoke one.
- **Do not touch `app/providers/speech.py`.** Extending the STT/TTS interfaces for real
  glossary application is item 20's decision, made with a real consumer in hand.
- Continuing straight through 13b after this one completes, per the "entire step 12 and step
  13 in one go" instruction - it still gets its own spec, branch, and merge.
