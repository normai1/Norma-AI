# Feature: Document processing pipeline

**From build-plan:** feature 17

**Status:** not started

## Goal

Turn each of the three existing knowledge source types (`file` - item 14, `website` - item
15, `manual_faq` - item 16) into retrievable `Chunk` rows: parse raw content into normalized
text, split it into bounded chunks, and record them with status tracking and retryable
failures. This is the last step before embeddings (item 18) and retrieval (item 19) - both of
those depend on `Chunk` existing with the right shape, so this feature creates the full locked
`Chunk` table now (including its `vector(1536)` column) even though nothing writes to that
column yet.

## Design reference

None. Backend-only, matching items 14/15/16's precedent. No frontend exists for Knowledge yet.

## In scope

- **`Chunk` model - the full locked shape from `project-overview.md`, created now.**
  `organization_id`/`workspace_id` (denormalized, NOT NULL - retrieval's hot-path filter),
  `knowledge_source_id` (FK, CASCADE), `text` (Text, NOT NULL), `ordering` (int), `metadata`
  (JSONB) - the citation/traceability field ("page/section/offset", per the locked contract) -
  doubles here as the lookup key for a manual-FAQ chunk's owning entry (`{"faq_entry_id":
  "<uuid>"}"`), avoiding a new column for a one-off need. `embedding` (`vector(1536)`,
  **nullable**) is created now so item 18 never has to `ALTER TABLE` a live column onto a
  populated table; item 17 never writes to it.
- **Enable the `vector` Postgres extension** (`CREATE EXTENSION IF NOT EXISTS vector`) in this
  feature's migration - the first table in the repository to need it. The `pgvector` Python
  package (`pgvector.sqlalchemy.Vector`) is a new dependency.
- **Document parsing** - `.txt`/`.md` (decode as text), `.pdf` (`pypdf`), `.docx`
  (`python-docx`) into one normalized plain-text string per document. A `.pdf`/`.docx` with no
  extractable text (e.g. a scanned/image-only PDF, a corrupted file) is a parse failure, not a
  silent empty result - new dependencies, added to `requirements.txt`.
- **Chunking** - a pure function splitting normalized text into paragraph-aware spans up to a
  fixed size (1,500 chars), each carrying its `char_start`/`char_end` offset in the source text
  for citation. A single paragraph longer than the limit falls back to a whitespace-boundary
  split so no chunk is ever truncated mid-word.
- **File-type wiring** - `upload_knowledge_source` (item 14) now parses and chunks the just-
  stored document synchronously, in the same request, transitioning `KnowledgeSource.status`
  and `Document.processing_status` through `pending -> processing -> completed`/`failed` with
  `error_message`/`processing_error` set on failure. A new `POST .../{id}/process` route
  retries parsing+chunking from the already-stored file - the retry path for a failure. A
  failed (re)parse never touches chunks already on record from an earlier successful run;
  `Chunk` rows for a source are only replaced after a new parse+chunk succeeds.
- **Website-type wiring** - `_crawl_and_reconcile` (item 15) chunks every crawled page's
  `extracted_text` after a successful crawl or recrawl, tagging each chunk's metadata with the
  page's `url`. A recrawl fully replaces the source's chunk set (matching how it already
  replaces/upserts `CrawledPage` rows) so a page removed from the live site does not leave
  stale chunks behind.
- **Manual-FAQ-type wiring** - `create_faq_entry`/`update_faq_entry`/`delete_faq_entry` (item
  16) each keep exactly one `Chunk` per `FaqEntry` in sync (`"Q: {question}\nA: {answer}"`),
  looked up by the `faq_entry_id` recorded in that chunk's `metadata`. This can't fail the way
  parsing can - it is plain string formatting - so there is no retry path for it.
- **`GET .../knowledge-sources/{id}/chunks`** - lists a source's chunks (any workspace member).
  Unbounded like `FaqEntry`'s own list endpoint (item 16), not nested inline like the
  bounded (max 20) `crawled_pages` - a parsed document can produce far more than 20 chunks.

## Out of scope

- **Embeddings and pgvector writes.** Item 18. `Chunk.embedding` stays `NULL` after this
  feature; nothing here computes or writes a vector.
- **Retrieval.** Item 19 - reading chunks back for an in-call answer.
- **A background job queue.** `apps/worker` is still a heartbeat-only skeleton (confirmed by
  reading its own docstring). Parsing/chunking runs synchronously inside the request, the same
  deliberate, temporary tradeoff item 15's crawl already established - bounded here by the
  existing 20MB upload cap (item 14), not a new limit.
- **OCR for scanned/image-only PDFs.** `pypdf` reads an embedded text layer only; a PDF without
  one is a parse failure, not a silently-empty success.
- **Pagination on `GET .../chunks`.** Not asked for, and no pagination convention exists
  anywhere else in this API yet. A genuinely huge document could produce a large chunk list;
  revisit if that proves a real problem once the (unbuilt) Knowledge UI or item 18/19 need it.
- **Configurable chunk size.** Fixed at 1,500 chars; not an operator setting.
- **A status change for `manual_faq` sources.** Item 16 explicitly left `KnowledgeSource.status`
  at `'pending'` forever for this type, reasoning "there is no operation here that could fail."
  Chunking a FAQ entry can't fail either, so that reasoning still holds - `manual_faq`'s
  `KnowledgeSource.status` is untouched by this feature, on purpose.
- **Any frontend.** No Knowledge UI exists yet, matching items 14/15/16.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `Chunk` model, `vector` extension, migration** - `app/models/chunk.py`;
  register in `app/db/base.py`; one migration: `CREATE EXTENSION IF NOT EXISTS vector` +
  `create_table('chunks', ...)` (downgrade drops the table only - leaving the extension
  installed, matching how nobody drops `pgcrypto`/`uuid-ossp` on downgrade either); add
  `pgvector` to `requirements.txt`; `docker compose build api && docker compose up -d api` to
  pick it up **and** `pip install pgvector` into the host Python environment - `pytest` runs
  from the host (confirmed convention, `TEST_DATABASE_URL`/`TEST_REDIS_URL` default to
  `localhost`), and it will fail to even collect `app/models/chunk.py` without the package
  installed there too, independent of the container rebuild.
  *Done when:* `alembic upgrade head` / `downgrade -1` / `upgrade head` all succeed. `ruff
  check apps/api` clean. No route/service/test yet - Step 3 gives this table its first writer.

- [x] **Step 2 - parser + chunker, pure functions** - `app/services/document_parser.py`
  (`parse_document(content: bytes, extension: str) -> str`, raising a local
  `DocumentParseError` for an unsupported extension, a corrupted file, or no extractable text);
  `app/services/chunker.py` (`chunk_text(text: str, *, max_chars: int = 1500) ->
  list[ChunkSpan]`, a frozen dataclass of `text`/`char_start`/`char_end`); add `pypdf` and
  `python-docx` to `requirements.txt`; rebuild the API container **and** `pip install pypdf
  python-docx` into the host Python environment, same reason as Step 1.
  *Done when:* `tests/test_document_parser.py` covers `.txt`/`.md` decode, a real small `.pdf`
  and `.docx` fixture parsing to the expected text, an unsupported extension, a corrupted/
  garbage-bytes file, and a PDF with no extractable text - all raising `DocumentParseError`
  where expected. `tests/test_chunker.py` covers empty/blank input (empty list), text shorter
  than the limit (one span), multi-paragraph text packed into multiple spans, a single
  paragraph longer than the limit (whitespace-split fallback), and that every span's
  `char_start`/`char_end` slice of the original text equals its `text`. Full backend suite
  green. `ruff check apps/api` clean.

- [x] **Step 3 - file-type wiring** - `app/repositories/chunk.py` (`list_for_source`,
  `replace_for_source` - delete-then-bulk-insert, sequential `ordering`); `app/core/
  exceptions.py` gets `InvalidKnowledgeSourceType`; `app/services/knowledge_source.py` gets a
  `_parse_and_chunk_document` helper (downloads via `StorageProvider`, parses, chunks, replaces
  the source's chunks only on success, updates both statuses) called from `upload_knowledge_
  source`, plus a new `process_knowledge_source` (re-runs the same helper, 422s via
  `InvalidKnowledgeSourceType` for a non-`file` source); `app/schemas/chunk.py`
  (`ChunkResponse{id, knowledge_source_id, text, ordering, metadata, created_at}`); `app/api/
  v1/knowledge_sources.py` gets `POST .../{id}/process` (owner/admin) and `GET .../{id}/chunks`
  (any member); new `tests/test_document_chunking.py`.
  *Done when:* uploading a valid `.txt`/`.md`/`.pdf`/`.docx` produces chunks reachable via `GET
  .../chunks` in order, with `KnowledgeSource.status`/`Document.processing_status` both
  `'completed'`; uploading a corrupted/unparseable file leaves both `'failed'` with an
  `error_message`/`processing_error` set and zero chunks persisted; `POST .../process` on that
  same source retries and can succeed or fail again without duplicating or losing chunks
  (reprocessing a source already parsed replaces, never appends); `POST .../process` on a
  `website` or `manual_faq` source returns 422; `GET .../chunks` 401 unauthenticated, reachable
  by any workspace member, 404 for a source in a sibling workspace. Full backend suite green.
  `ruff check apps/api` clean.

- [x] **Step 4 - website-type wiring** - `_crawl_and_reconcile` in `app/services/
  knowledge_source.py` chunks every crawled page's `extracted_text` after a successful crawl
  (metadata `{"url": page.url}` per chunk) and replaces the source's whole chunk set; extend
  `tests/test_document_chunking.py`.
  *Done when:* creating a website source produces chunks tagged with the crawled pages' URLs,
  reachable via `GET .../chunks`; a recrawl reflects the new crawl exactly - a dropped page's
  chunks are gone, a changed page's chunks reflect the new text, an unchanged page's chunks
  still read the same. Chunk `id`s are not required to stay stable across a recrawl (a full
  replace is an acceptable implementation - nothing yet depends on `id` stability). Full
  backend suite green. `ruff check apps/api` clean.

- [x] **Step 5 - manual-FAQ-type wiring** - `app/repositories/chunk.py` gets
  `get_for_faq_entry`/`upsert_for_faq_entry`/`delete_for_faq_entry` (JSONB `metadata->>
  'faq_entry_id'` lookup); `app/services/faq_entry.py`'s `create_faq_entry`/
  `update_faq_entry`/`delete_faq_entry` each sync that entry's one `Chunk`; extend `tests/
  test_document_chunking.py`.
  *Done when:* creating a FAQ entry produces exactly one chunk (`"Q: ...\nA: ..."`) reachable
  via `GET .../chunks`; updating the entry updates that same chunk's text (row count
  unchanged); deleting the entry removes its chunk. Full backend suite green. `ruff check
  apps/api` clean.

## Files / areas

**New**
- `apps/api/app/models/chunk.py`
- `apps/api/app/services/document_parser.py`
- `apps/api/app/services/chunker.py`
- `apps/api/app/repositories/chunk.py`
- `apps/api/app/schemas/chunk.py`
- `apps/api/tests/test_document_parser.py`
- `apps/api/tests/test_chunker.py`
- `apps/api/tests/test_document_chunking.py`
- One Alembic migration (Step 1).

**Modified**
- `apps/api/app/db/base.py` (register `Chunk`)
- `apps/api/app/core/exceptions.py` (`InvalidKnowledgeSourceType`)
- `apps/api/app/services/knowledge_source.py` (`_parse_and_chunk_document`,
  `process_knowledge_source`, chunking wired into `upload_knowledge_source` and
  `_crawl_and_reconcile`)
- `apps/api/app/services/faq_entry.py` (chunk sync on create/update/delete)
- `apps/api/app/api/v1/knowledge_sources.py` (`POST .../process`, `GET .../chunks`)
- `apps/api/requirements.txt` (`pgvector`, `pypdf`, `python-docx`)
- `apps/api/tests/conftest.py` (Step 2 fix: `engine` fixture now enables the
  `vector` extension itself, since the test schema is built from `Base.metadata`
  directly and never runs Step 1's migration)

**Unchanged**
- No frontend file. No new provider abstraction - parsing/chunking are pure computation over
  already-abstracted I/O (`StorageProvider` for bytes, `PageFetcher` already resolved by item
  15), the same reasoning that kept `crawl_website` a plain service function rather than a
  provider.

## Data / contracts

**`Chunk`** (new, locked shape from `project-overview.md`) - `id`, `organization_id`,
`workspace_id`, `knowledge_source_id`, `text`, `ordering`, `metadata` (JSONB - Python attribute
`chunk_metadata`, since `metadata` is reserved on a SQLAlchemy declarative `Base` subclass;
`mapped_column("metadata", JSONB, ...)` keeps the DB column name matching the locked contract),
`embedding` (`vector(1536)`, nullable), `created_at`, `updated_at`.

**`ChunkResponse`** - `{id, knowledge_source_id, text, ordering, metadata, created_at}`. No
`embedding` - not meant to round-trip over the API.

## Testing

The backend gate is live - every step ships its tests in the same diff. `document_parser`/
`chunker` are pure logic with a real wrong-answer surface (exactly the class `coding-
standards.md` calls out for a unit test), covered directly in Step 2. Steps 3-5 cover the
per-source-type wiring end to end through the API, mirroring items 14/15/16's own coverage
shape: success paths, failure paths, and cross-tenant/cross-type checks where relevant.

## Notes for the AI

- **`metadata` the column name, `chunk_metadata` the Python attribute.** SQLAlchemy's
  declarative `Base` reserves `metadata` for its own `MetaData` registry; naming a mapped
  attribute `metadata` raises `InvalidRequestError` at class-definition time. Use
  `mapped_column("metadata", JSONB, ...)` under a different attribute name.
- **Replace-on-success, not replace-then-parse.** For file and website sources, only call
  `replace_for_source` after parsing/chunking succeeds. A failed reprocess must never destroy
  chunks a prior successful run already produced.
- **`vector(1536)` is created now, written later.** Item 18 populates `embedding`; this feature
  never does. Do not defer creating the column - project-overview.md's locked schema already
  places it on `Chunk`, and adding it later would mean an `ALTER TABLE` against a table that
  already has rows, exactly the two-plane migration risk CLAUDE.md section 6.2 warns about.
- **`pgvector.sqlalchemy.Vector` needs no asyncpg-level codec registration** for this feature -
  it only ever binds `NULL`. Item 18 is responsible for confirming real vector reads/writes
  work correctly through the async engine when it starts writing actual embeddings.
- **Reuse `StorageProvider.download`, `PageFetcher`, `CanManageKnowledge`, `CurrentWorkspace`,
  `resolve_knowledge_source`.** No new provider, no duplicated tenant-resolution logic.
- **An empty or whitespace-only `.txt`/`.md` is not a parse failure** - it normalizes to empty
  text, `chunk_text` returns zero spans, and the source completes with zero chunks. Only
  `.pdf`/`.docx` treat "no extractable text" as a failure, since for those formats it almost
  always signals a real problem (a scanned page image, a corrupted file) rather than a
  genuinely empty document.
- **Website chunk-replacement strategy is intentionally simple.** Full replace on every
  crawl/recrawl (matching how chunking always works for file sources) is acceptable even though
  it means chunk `id`s churn on every recrawl of an unchanged page - nothing yet depends on
  chunk `id` stability. Revisit only if a later feature needs it.
- A push, if any, at the end of this feature still needs your explicit go-ahead, matching the
  standing project convention.
