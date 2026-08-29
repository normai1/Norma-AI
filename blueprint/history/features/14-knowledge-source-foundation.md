# Feature: Knowledge source foundation

**From build-plan:** feature 14
**Status:** not started

## Goal

The data model, storage abstraction, and upload API for the first knowledge-source type:
`KnowledgeSource` + `Document`, a `StorageProvider` abstraction (mock for tests, local
filesystem for development, S3 for production - CLAUDE.md section 8's exact list), and an
upload endpoint accepting PDF/DOCX/Markdown/TXT with processing status and error fields
surfaced through the API. This is explicitly a **foundation** item: build-plan items 15/16 add
the other two source types (website, manual FAQ), and item 17 adds the actual parsing/chunking
worker that transitions status past `pending`. Nothing here invents behavior those later items
own.

## Design reference

None. Backend-only; no UI in this feature (a knowledge-management UI is a later item, not named
in this build-plan line).

## In scope

- **`StorageProvider`** protocol (`app/providers/storage.py`, mirroring `speech.py`'s exact
  shape): `upload(key, content, content_type)`, `download(key) -> bytes`, `delete(key)`. Three
  implementations: `MockStorage` (in-memory dict, the test suite's actual provider - injected
  via `app.dependency_overrides` in `conftest.py`'s `client` fixture, the same mechanism
  already used for `get_db`/`get_redis`, not a new pattern), `LocalStorage` (writes under a
  configured directory - CLAUDE.md section 20's own "development-only adapter"), `S3Storage`
  (real `boto3` calls, wrapped in `asyncio.to_thread` since boto3 is synchronous and this is a
  control-plane upload request, not the real-time audio path the "no blocking I/O" rule
  targets). A `app/providers/factory.py` addition (`get_storage_provider`,
  `get_storage_provider_dependency`) mirrors `get_stt_provider`/`get_tts_provider` exactly,
  including failing at construction (`MissingS3ConfigError`) if `STORAGE_PROVIDER=s3` is
  selected without AWS credentials configured - the same reasoning
  `MissingElevenLabsApiKeyError` already established.
- **Config**: `STORAGE_PROVIDER` (default `local` - unlike `STT_PROVIDER`/`TTS_PROVIDER`'s
  `mock` default, a fresh checkout should be able to actually exercise file storage end to end
  with zero paid dependency, and local disk is free; the test suite gets `mock` via the
  dependency-override, not by changing this default), `LOCAL_STORAGE_DIR` (default
  `data/uploads`, resolved under the API's working directory - already visible on the host via
  the existing `./apps/api:/app` bind mount, so no new Docker volume is needed), `AWS_REGION`,
  `AWS_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (all empty by default, matching
  `elevenlabs_api_key`'s pattern). `data/` added to `.gitignore` so local dev uploads are never
  committed.
- **`KnowledgeSource`** - `organization_id`, `workspace_id`, `type` (CHECK constraint listing
  the full locked set from `project-overview.md` - `'file'`, `'website'`, `'manual_faq'` - even
  though this feature only ever creates `'file'` rows; the three-value contract is already
  locked in the schema doc, so widening the CHECK later purely to add values that were already
  documented would be pure churn), `status` (CHECK: `'pending'`, `'processing'`, `'completed'`,
  `'failed'`, default `'pending'`), `error_message` (text, nullable), `owner_user_id` (FK ->
  `users.id`, set from the authenticated caller, never client-supplied).
- **`Document`** - `knowledge_source_id` (FK -> `knowledge_sources.id`, CASCADE), `filename`,
  `content_type`, `storage_key` (never exposed in the API response - an internal detail, and
  there is no download endpoint yet for it to serve), `processing_status` (same four-value
  CHECK as `KnowledgeSource.status`), `processing_error` (nullable). One `Document` per
  `KnowledgeSource` in this feature (upload creates exactly one of each, in that order:
  upload the bytes to storage first, then insert both rows in one transaction; if the DB
  insert fails after a successful upload, the uploaded object is deleted as a compensating
  action rather than left orphaned).
- **`POST .../knowledge-sources`** - `multipart/form-data` upload. Accepts `.pdf`, `.docx`,
  `.md`, `.txt` by extension (the authoritative check - client-declared MIME types are
  inconsistent across browsers, especially for `.md`), rejects anything else with `422`, caps
  size at 20 MB (`422` if exceeded - a plain constant, not a new env var; not specified anywhere
  in CLAUDE.md, so a documented, adjustable default rather than an invented "requirement").
  Creates `type='file'`, `status='pending'` unconditionally - there is no client-supplied
  `type`, since the other two types aren't buildable through this endpoint. A new
  `MANAGE_KNOWLEDGE` permission (owner/admin only), matching `PromptTemplate`'s precedent of
  its own permission rather than reusing an unrelated one, since `KnowledgeSource` is an
  independently workspace-scoped resource, not a sub-resource of something else.
- **`GET .../knowledge-sources`**, **`GET .../knowledge-sources/{id}`** - list and get one, any
  workspace member, 404-not-403 cross-tenant, matching every other read endpoint in this
  codebase. Response nests the one `Document` (`KnowledgeSourceResponse.document`), since the
  1:1 relationship makes a separate fetch pure ceremony for this feature's UI-less scope.

## Out of scope

- **Website and manual-FAQ knowledge sources.** Items 15/16. The `type` CHECK constraint
  already accommodates them (see above), but no code path in this feature creates either.
- **Any actual parsing, text extraction, chunking, or embedding.** Item 17 (parsing pipeline)
  and item 18 (vector indexing). `status` and `processing_status` are stored and returned
  as-is; nothing in this feature ever transitions either past `'pending'` - there is no worker
  yet to do that transitioning correctly, and inventing a fake one here would have to be
  un-invented later.
- **A download endpoint, or exposing `storage_key`.** Nothing in the build-plan line asks for
  retrieving the original file back through the API; `StorageProvider.download` exists in the
  interface for item 17's parser to call internally, not for an HTTP route in this feature.
- **Update or delete of a `KnowledgeSource`/`Document`.** Not asked for by the build-plan line;
  re-uploading to replace content, or removing a source, is a decision for whichever later
  feature needs it.
- **Signed/short-lived URLs.** That convention (CLAUDE.md section 20) is specifically about
  serving recordings; nothing here serves a URL to a caller at all.
- **Batch upload (multiple files in one request).** The build-plan line describes uploading
  documents, not a bulk-import flow; one file per request, one `KnowledgeSource` per file.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `StorageProvider` abstraction** - `app/providers/storage.py` (protocol +
  `StorageProviderError`, `StorageObjectNotFound`); `app/providers/mock_storage.py`
  (`MockStorage`, in-memory dict); `app/providers/local_storage.py` (`LocalStorage`, real
  filesystem writes under `LOCAL_STORAGE_DIR`); `app/providers/s3_storage.py` (`S3Storage`,
  `boto3` wrapped in `asyncio.to_thread`); `app/providers/factory.py` gets
  `get_storage_provider`/`get_storage_provider_dependency`/`UnknownStorageProviderError`/
  `MissingS3ConfigError`; `app/core/config.py` gets the settings listed above; `app/api/deps.py`
  gets a `StorageProviderDep` annotation; `requirements.txt` gets `python-multipart`, `boto3`;
  `.env.example` gets the Storage section; `.gitignore` gets `data/`; `tests/conftest.py`'s
  `client` fixture overrides `get_storage_provider_dependency` to a fresh `MockStorage()` per
  test, mirroring the existing `get_db`/`get_redis` overrides.
  *Done when:* the API container is rebuilt (`docker compose build api && docker compose up -d
  api` - a dependency change doesn't reach a running container otherwise) and starts cleanly; a
  new `tests/test_storage_providers.py` passes - `MockStorage` and `LocalStorage` both
  round-trip upload/download/delete correctly, downloading a missing key raises
  `StorageObjectNotFound` on both, `LocalStorage` writes are confined under its configured
  directory; `S3Storage` gets a focused unit test against a stubbed `boto3` client (matching
  9b's "adapter tests against a stubbed transport"), proving it calls `put_object`/
  `get_object`/`delete_object` with the right bucket/key. `MissingS3ConfigError` fires when
  `STORAGE_PROVIDER=s3` is selected with no AWS credentials configured. Full backend suite
  still green (nothing here should touch existing behavior). `ruff check apps/api` clean.

- [x] **Step 2 - `KnowledgeSource`/`Document` models and migration** - `app/models/
  knowledge_source.py`, `app/models/document.py`; one migration; `app/core/exceptions.py` gets
  `KnowledgeSourceError`, `KnowledgeSourceNotFound`; `app/core/permissions.py` gets
  `MANAGE_KNOWLEDGE` (added to `_ELEVATED`); `app/api/org_deps.py` gets `CanManageKnowledge`;
  registered in `app/db/base.py`.
  *Done when:* `alembic upgrade head` / `downgrade -1` / `upgrade head` all succeed. `ruff
  check apps/api` clean (no route/test yet - the models have no behavior of their own until
  Step 3's endpoints exist).

- [x] **Step 3 - Upload, list, get** - `app/repositories/knowledge_source.py` (`get_by_id`,
  `list_for_workspace`, `create_with_document`); `app/services/knowledge_source.py`
  (`_resolve_workspace_id`, `resolve_knowledge_source`, `upload_knowledge_source` - validates
  extension and size, uploads to storage, creates both rows, deletes the uploaded object on a
  DB failure; `list_knowledge_sources`, `get_knowledge_source`); `app/schemas/
  knowledge_source.py` (`DocumentResponse{id, filename, content_type, processing_status,
  processing_error, created_at}`, `KnowledgeSourceResponse{id, organization_id, workspace_id,
  type, status, error_message, owner_user_id, created_at, document: DocumentResponse | None}`);
  `app/api/v1/knowledge_sources.py` (`POST`/`GET`-list/`GET`-one under `/organizations/
  {organization_id}/workspaces/{workspace_id}/knowledge-sources`); registered in `app/main.py`.
  *Done when:* a new `tests/test_knowledge_sources.py` passes - uploading each of the four
  accepted extensions succeeds and the response's `document.filename`/`content_type` match, the
  uploaded bytes are retrievable byte-for-byte from the injected `MockStorage` afterward
  (proving the real upload path ran, not just a DB write); an unsupported extension returns
  422; a file over 20 MB returns 422; owner/admin can upload, member/viewer get 403,
  unauthenticated gets 401; any workspace member can list/get; cross-workspace and cross-org
  404s hold; `status`/`error_message`/`processing_status`/`processing_error` all default
  correctly (`'pending'`, `null`, `'pending'`, `null`). Full backend suite green. `ruff check
  apps/api` clean.

## Files / areas

**New**
- `apps/api/app/providers/storage.py`, `mock_storage.py`, `local_storage.py`, `s3_storage.py`
- `apps/api/app/models/knowledge_source.py`, `apps/api/app/models/document.py`
- `apps/api/app/repositories/knowledge_source.py`
- `apps/api/app/services/knowledge_source.py`
- `apps/api/app/schemas/knowledge_source.py`
- `apps/api/app/api/v1/knowledge_sources.py`
- `apps/api/tests/test_storage_providers.py`, `apps/api/tests/test_knowledge_sources.py`
- One Alembic migration.

**Modified**
- `apps/api/app/providers/factory.py`, `apps/api/app/core/config.py`,
  `apps/api/app/api/deps.py`, `apps/api/app/core/exceptions.py`,
  `apps/api/app/core/permissions.py`, `apps/api/app/api/org_deps.py`,
  `apps/api/app/db/base.py`, `apps/api/app/main.py`, `apps/api/requirements.txt`,
  `.env.example`, `.gitignore`, `apps/api/tests/conftest.py`.

**Unchanged**
- No frontend file. No change to any existing route or model outside what's listed.

## Data / contracts

**`KnowledgeSourceResponse`** - `{id, organization_id, workspace_id, type, status,
error_message, owner_user_id, created_at, document}`. `document` is `DocumentResponse | null`
- always populated by this feature (upload always creates both rows together), but nullable in
the schema since a future source type (website, item 15) won't have one.

**`DocumentResponse`** - `{id, filename, content_type, processing_status, processing_error,
created_at}`. No `storage_key` - internal only.

**`StorageProvider`** - `upload(key: str, content: bytes, content_type: str) -> None`,
`download(key: str) -> bytes`, `delete(key: str) -> None`. Locked now since item 17's parser
depends on `download` directly.

## Testing

The backend gate is live - every step ships its tests in the same diff. Step 1's provider
tests need no database fixture (pure in-memory/filesystem/stubbed-client behavior). Step 3's
coverage mirrors the established shape: success paths including a real storage round-trip
(not just a DB assertion), 403/401, cross-tenant 404s, and the two upload-validation failure
cases.

## Notes for the AI

- **This is a foundation item - resist finishing it.** Do not add parsing, chunking,
  embeddings, a download route, or the other two source types. Each has its own build-plan
  item; building any of them here would have to be partially undone or awkwardly reconciled
  when that item's own spec arrives.
- **Storage upload happens before the DB insert, with compensating delete on failure.** Never
  insert a `Document` row pointing at a `storage_key` that was never actually written.
- **`storage_key` never appears in an API response.** It is an internal detail with no
  consumer yet.
- **Mirror `factory.py`'s existing STT/TTS pattern exactly** for the storage provider factory -
  same fail-at-construction reasoning for `MissingS3ConfigError`, same
  `get_..._provider_dependency()` wrapper shape closing off a query-parameter override.
- **Extension is the authoritative upload-type check, not the client-declared content type.**
  Browsers are inconsistent about MIME types for `.md` in particular.
- **The API container needs a rebuild after Step 1's `requirements.txt` change** - a running
  container does not pick up new dependencies on its own (AGENTS.md's own documented gotcha).
- A push, if any, at the end of this feature still needs your explicit go-ahead, matching the
  standing project convention.
