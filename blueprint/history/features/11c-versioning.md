# Feature: Versioning

**From build-plan:** feature 11c
**Status:** not started

## Goal

The versioning *behavior* on top of 11b's `AssistantVersion` storage: a real, enforced
immutability guarantee, a diff between two versions, and publish (which also serves as
rollback - publishing an older version than the current one *is* a rollback, so no separate
rollback endpoint is needed).

## Design reference

None. No UI in this sub-feature (11d's).

## In scope

- **Enforced immutability.** A SQLAlchemy `before_update` event listener on `AssistantVersion`
  that raises if anything ever tries to `UPDATE` a version row. Nothing in this codebase attempts
  that today (11b's repository never wrote an `update` function), so this turns an accidental
  absence into a structural guarantee - a regression test proves a direct attempt to mutate a
  version raises, rather than silently succeeding if some future code path tries.
- **`POST .../assistants/{assistant_id}/publish`** - body `{"version": int}`. Sets
  `Assistant.current_version_id` to that version's id. Transitions `Assistant.status` from
  `draft` to `published` on first publish; stays `published` on any later publish, including one
  naming an older version number than the currently-published one (a rollback). Publishing the
  version that is already current is idempotent (succeeds, no-op on status). Publishing an
  `archived` assistant is rejected (409) - archived is a terminal state in this codebase (11a
  deliberately shipped no restore/un-archive endpoint), so there is nothing that could legally
  publish it back to life without that first existing.
- **`GET .../assistants/{assistant_id}/versions/{from_version}/diff/{to_version}`** - returns
  only the fields that differ between the two versions (`voice_id`, `language`, `greeting`,
  `persona`, `speech_rate`, `turn_sensitivity`, `creativity`, `ambient_sound` - the config fields
  11b defined, not `id`/`assistant_id`/`version`/timestamps), each as `{"previous": ..., "current": ...}`
  (see Notes below on why not `from`/`to`).
  Both versions must exist for that assistant.
- Same access model as 11a/11b: `/publish` needs `CanManageAssistants` (owner/admin); the diff
  endpoint is a read, open to any workspace member via `CurrentWorkspace`.

## Out of scope

- **A separate rollback endpoint.** `/publish` accepting any valid version number - including
  one older than current - already is rollback. Building a second endpoint that does the
  identical thing under a different name would be pure duplication.
- **Un-archiving.** Still not asked for (11a's own scope note). `/publish` on an archived
  assistant stays a hard 409, not a trigger that quietly un-archives it.
- **Diff across assistants, or a diff that includes non-config fields.** The diff is strictly
  "what changed in the configuration between these two snapshots of the same assistant" -
  `id`, `assistant_id`, `version`, and the timestamps are never meaningfully "diffable" in the
  sense an operator cares about.
- **The editor UI, or anything that calls `/publish`/`/diff` from a browser** - 11d's.
- **Un-publishing** (going back to `draft` with no live version). Not in the build-plan line;
  the only documented lifecycle transitions are draft -> published (via `/publish`) and anything
  -> archived (11a's existing endpoint).

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Enforced immutability** - a SQLAlchemy event listener (in
  `app/models/assistant_version.py`, alongside the model it protects) hooked to `before_update`
  on `AssistantVersion`, raising a new `AssistantVersionImmutable` exception in
  `app/core/exceptions.py`.
  *Done when:* `pytest apps/api/tests/test_assistant_versions.py` (extended) passes, including a
  new test that directly mutates and flushes an `AssistantVersion` row and asserts it raises,
  proving the guard actually fires rather than merely existing. `ruff check apps/api` clean.

- [x] **Step 2 - Publish (and rollback)** - `app/schemas/assistant.py` gets `AssistantPublish
  {version: int}`; `app/services/assistant.py` gets `publish_assistant` (resolve the assistant,
  reject if `status == 'archived'` with a new `AssistantArchived` exception, resolve the target
  version via `assistant_version_repo.get_by_version` raising `AssistantVersionNotFound` if it
  doesn't exist, set `current_version_id`, set `status = 'published'` if not already, flush);
  route `POST .../assistants/{assistant_id}/publish` in `app/api/v1/assistants.py`, requiring
  `CanManageAssistants`, mapping `AssistantArchived` to 409.
  *Done when:* `pytest apps/api/tests/test_assistants.py` (extended) passes, proving: publishing
  version 1 on a fresh assistant sets `current_version_id` and flips `status` to `published`;
  publishing version 1 again is a no-op success; publishing an out-of-range version number
  returns 404; publishing an archived assistant returns 409; publishing an older version after a
  newer one was already published (a rollback) succeeds and moves `current_version_id` back; a
  member gets 403; unauthenticated gets 401; the existing cross-workspace/cross-org checks still
  hold. `ruff check apps/api` clean.

  **Real gap found and fixed:** `AssistantResponse` (11a's schema) never had a
  `current_version_id` field at all - 11b added the column but correctly never exposed it since
  nothing set it yet. Publishing now sets it, so the response needed the field to make the
  effect observable; added `current_version_id: uuid.UUID | None`, and a `test_create_...`
  assertion that it is `None` on a fresh assistant.

- [x] **Step 3 - Diff** - `app/schemas/assistant_version.py` gets `AssistantVersionDiffResponse`
  (`from_version`, `to_version`, `changes: dict[str, AssistantVersionFieldDiff]`);
  `app/services/assistant_version.py` gets `diff_versions` (resolve both versions via the
  existing scope-checked path, compare the eight config fields, include only the ones that
  differ); route `GET .../assistants/{assistant_id}/versions/{from_version}/diff/{to_version}`
  in `app/api/v1/assistant_versions.py`.
  *Done when:* `pytest apps/api/tests/test_assistant_versions.py` (extended) passes, proving: a
  diff between two versions that changed `greeting` and `speech_rate` returns exactly those two
  fields, each with correct `previous`/`current`; a diff between a version and itself returns an
  empty `changes` object; either version missing returns 404; member read-access holds. Full
  backend suite still green. `ruff check apps/api` clean.

  **Deviation from the spec's literal shape:** used `{"previous": ..., "current": ...}` instead
  of the spec's `{"from": ..., "to": ...}` - `from` is a Python keyword and collides with the
  natural attribute name, and rather than depend on Pydantic's alias/`by_alias` serialization
  behavior (untested in this codebase, no existing precedent to match), a plain field-name
  rename sidesteps the whole question. Not load-bearing for anything else built so far.

## Files / areas

**New**
- Nothing new at the file level - everything extends 11a/11b's existing files.

**Modified**
- `apps/api/app/models/assistant_version.py` - adds the immutability event listener.
- `apps/api/app/core/exceptions.py` - adds `AssistantVersionImmutable`, `AssistantArchived`.
- `apps/api/app/schemas/assistant.py` - adds `AssistantPublish`.
- `apps/api/app/schemas/assistant.py` (again) - adds `AssistantResponse.current_version_id`.
- `apps/api/app/schemas/assistant_version.py` - adds `AssistantVersionFieldDiff`,
  `AssistantVersionDiffResponse`.
- `apps/api/app/repositories/assistant.py` - adds `PUBLISHED_STATUS`, `publish()`.
- `apps/api/app/services/assistant.py` - adds `publish_assistant`.
- `apps/api/app/services/assistant_version.py` - adds `diff_versions`.
- `apps/api/app/api/v1/assistants.py` - adds the `/publish` route.
- `apps/api/app/api/v1/assistant_versions.py` - adds the `/diff` route.
- `apps/api/tests/test_assistants.py`, `apps/api/tests/test_assistant_versions.py`.

**Unchanged**
- No frontend file. No migration - nothing here changes the schema, only behavior on top of it.

## Data / contracts

**`POST .../assistants/{assistant_id}/publish`** - request `{"version": int}`, response is the
updated `AssistantResponse` (`current_version_id` now set, `status` now `published` unless it
already was). 404 if the version doesn't exist for that assistant, 409 if the assistant is
archived.

**`GET .../assistants/{assistant_id}/versions/{from_version}/diff/{to_version}`** - response
`{"from_version": int, "to_version": int, "changes": {"<field>": {"previous": ..., "current": ...}}}`.
Only fields that actually differ appear in `changes`.

**`AssistantResponse.current_version_id`** - now part of the locked `Assistant` response shape,
`null` until `/publish` has been called at least once.

## Testing

The backend gate is live - every step ships its tests in the same diff.

**In-scope logic needing tests:** the immutability guard actually firing (not just existing),
every publish transition and rejection case, the diff computation's field-by-field correctness
including the empty-diff and missing-version cases.

**No frontend tests** - no frontend code in this sub-feature.

## Notes for the AI

- **`/publish` is rollback.** Don't build a second endpoint for "roll back to an earlier
  version" - it is the exact same operation as publish with an older version number.
- **Archived is terminal in this codebase today.** `/publish` on an archived assistant is a hard
  409, not a soft "un-archive and publish."
- **The immutability guard protects a real invariant, not a hypothetical one.** Item 20's call
  engine will read whatever version is current mid-call, so a version silently changing under it
  would be a live-call correctness bug.
- **Diff compares only the eight 11b config fields**, using `previous`/`current` as the field
  diff's key names (not `from`/`to` - a Python-keyword collision this feature deliberately
  avoided).
- Continuing straight through 11d after this one completes, per the "entire step 11 in a single
  go" instruction - it still gets its own spec, branch, review, and merge.
