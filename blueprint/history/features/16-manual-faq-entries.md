# Feature: Manual FAQ entries

**From build-plan:** feature 16

**Status:** not started

## Goal

`type='manual_faq'` knowledge sources: operator-authored question/answer pairs, stored as a
first-class knowledge source alongside file uploads (item 14) and crawled websites (item 15).
Much smaller than either sibling feature - no storage provider, no crawler, just a name plus a
list of Q&A pairs the operator directly authors and edits.

## Design reference

None. Backend-only, matching items 14/15's precedent.

## In scope

- **Schema gap, reconciled explicitly - same shape as item 15's `source_url`.**
  `project-overview.md`'s locked `KnowledgeSource` model has no field identifying *which* FAQ
  set a `manual_faq` row is ("General FAQ" vs. "Billing Questions") the way a file source is
  identified by its document's filename and a website source by its `source_url`. `
  KnowledgeSource` gets one additive column: `name` (nullable text, only ever set for
  `type='manual_faq'` rows).
- **`FaqEntry`** - a new model, `knowledge_source_id` (FK -> `knowledge_sources.id`, CASCADE),
  `question` (text, required, max 2000 chars), `answer` (text, required, max 4000 chars - the
  same generous bound `AssistantVersion.persona` already uses for longer free text). Many
  entries per source, matching `CrawledPage`'s per-source relationship shape, not
  `GlossaryEntry`'s per-assistant one. No uniqueness constraint on `question` - unlike glossary
  terms, two similarly-worded questions are not inherently a data-integrity problem.
- **`POST .../knowledge-sources/manual-faq`** - body `{name: str}`, creates a
  `type='manual_faq'` source with `status='pending'` - matching item 14's exact reasoning, not
  item 15's: nothing in this feature parses or chunks the entries (that is still item 17's
  job), so there is no genuine success/failure outcome to report the way a crawl has. Reuses
  `CanManageKnowledge` (owner/admin), the same permission items 14/15 already use for this
  resource.
- **FAQ entry CRUD, nested under the owning source**: `POST/GET .../knowledge-sources/
  {knowledge_source_id}/faq-entries`, `PATCH/DELETE .../faq-entries/{faq_entry_id}` - 404 if the
  source doesn't exist, isn't in the caller's workspace, or isn't `type='manual_faq'`. `PATCH`
  is a partial update (question and/or answer, both optional) using the same `_UNSET`-sentinel
  pattern `GlossaryEntry`'s `update()` already established, since both fields are plain
  required-at-creation text with no legitimate "clear it" case - omitted simply means
  untouched. `DELETE` is a real hard delete (`204`) - a plain reference row, not a versioned
  snapshot, matching `GlossaryEntry`'s precedent exactly.
- **`KnowledgeSourceResponse` gains `name`** (mirroring `source_url`'s `| None = None` shape).
  FAQ entries are **not** nested inline - unlike the bounded (max 20) crawled-page list, a FAQ
  set has no size cap, so entries get their own list endpoint, matching how `AssistantVersion`/
  `PromptVersion` are separate from their parent rather than inlined.

## Out of scope

- **Any parsing, chunking, or embedding of FAQ entries.** Item 17/18, same as every other
  source type.
- **A cap on how many entries a FAQ source can hold.** Not asked for; unlike the crawl's
  bounded-request-latency concern, adding entries one at a time has no equivalent synchronous-
  request-latency problem to bound against.
- **Bulk import (e.g., CSV/JSON upload of many Q&A pairs at once).** Not named in the build-plan
  line; one entry per request, matching how item 14 deliberately excluded batch file upload.
- **Reordering, categorizing, or tagging entries.** Not asked for.
- **A `status`/`error_message` transition mechanism for `manual_faq` sources.** There is no
  operation in this feature that could fail the way a crawl can - status stays `'pending'`,
  matching item 14's file sources exactly.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `FaqEntry` model, `KnowledgeSource.name`, migration, exceptions** - `app/
  models/faq_entry.py`; `app/models/knowledge_source.py` gets `name`; one migration (new table
  + additive column together); `app/core/exceptions.py` gets `FaqEntryNotFound`; registered in
  `app/db/base.py`.
  *Done when:* `alembic upgrade head` / `downgrade -1` / `upgrade head` all succeed. `ruff
  check apps/api` clean (no route/test yet - Step 2 gives these models their first behavior).

- [x] **Step 2 - CRUD** - `app/repositories/faq_entry.py` (`get_by_id`, `list_for_source`,
  `create`, `update` with the `_UNSET` sentinel, `delete`); `app/services/knowledge_source.py`
  gets `create_manual_faq_knowledge_source`; `app/services/faq_entry.py`
  (`resolve_faq_entry` reusing `knowledge_source_service.resolve_knowledge_source` for tenant
  scope plus a `type == 'manual_faq'` check, `create_faq_entry`, `list_faq_entries`,
  `update_faq_entry`, `delete_faq_entry`); `app/schemas/knowledge_source.py` gets
  `ManualFaqKnowledgeSourceCreate{name}`, `KnowledgeSourceResponse.name`; `app/schemas/
  faq_entry.py` (`FaqEntryCreate{question, answer}`, `FaqEntryUpdate` - both optional,
  `FaqEntryResponse{id, knowledge_source_id, question, answer, created_at}`); `app/api/v1/
  knowledge_sources.py` gets the create-source route; `app/api/v1/faq_entries.py` (new file,
  the four CRUD routes).
  *Done when:* a new `tests/test_faq_entries.py` passes - creating a manual FAQ source returns
  `type='manual_faq'`, `status='pending'`, the given `name`; add/list/update/delete all work;
  a partial `PATCH` (question only, or answer only) leaves the other field untouched; delete
  actually removes the row (a subsequent list no longer contains it) and returns `204`;
  owner/admin allowed, member/viewer 403, unauthenticated 401; an entry route 404s for a
  nonexistent source, a source that exists but is `type='file'` or `type='website'`, or a
  source in a sibling workspace/organization. Full backend suite green. `ruff check apps/api`
  clean.

## Files / areas

**New**
- `apps/api/app/models/faq_entry.py`
- `apps/api/app/repositories/faq_entry.py`
- `apps/api/app/services/faq_entry.py`
- `apps/api/app/schemas/faq_entry.py`
- `apps/api/app/api/v1/faq_entries.py`
- `apps/api/tests/test_faq_entries.py`
- One Alembic migration.

**Modified**
- `apps/api/app/models/knowledge_source.py` (`name`), `apps/api/app/schemas/
  knowledge_source.py`, `apps/api/app/services/knowledge_source.py`, `apps/api/app/api/v1/
  knowledge_sources.py` (the one new create-source route), `apps/api/app/core/exceptions.py`,
  `apps/api/app/db/base.py`.

**Unchanged**
- No frontend file. `app/core/permissions.py`/`app/api/org_deps.py` - `CanManageKnowledge`
  already exists, reused as-is. No new provider abstraction - this feature needs neither
  storage nor a crawler.

## Data / contracts

**`KnowledgeSourceResponse`** gains `name: str | None`.

**`FaqEntryResponse`** - `{id, knowledge_source_id, question, answer, created_at}`.

## Testing

The backend gate is live - every step ships its tests in the same diff. Step 2's coverage
mirrors `GlossaryEntry`'s exact shape: success paths, partial-update semantics, real delete,
403/401, and cross-tenant/cross-type 404s.

## Notes for the AI

- **`status` stays `'pending'`. There is no crawl-style success/failure outcome here** - do not
  invent one.
- **Mirror `GlossaryEntry`'s `_UNSET`-sentinel partial-update pattern exactly** for
  `FaqEntry.update()` - do not use the plain "`None` means untouched" convention, since that
  only works when a field is not itself legitimately nullable, which does not apply here anyway
  (both `question`/`answer` are always required once set - `_UNSET` is used purely for "this
  field was omitted from the PATCH body", not because either field can be cleared to null).
- **FAQ entries get their own list endpoint, not an inline array on the source.** Unlike the
  bounded crawl, there is no size cap to justify inlining.
- **Reuse `CanManageKnowledge` and `resolve_knowledge_source`.** No new permission, no
  duplicated tenant-resolution logic.
- A push, if any, at the end of this feature still needs your explicit go-ahead, matching the
  standing project convention.
