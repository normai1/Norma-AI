# Feature: Vector knowledge indexing

**From build-plan:** feature 18

**Status:** not started

## Goal

Compute an OpenAI `text-embedding-3-small` embedding (1536 dims) for every `Chunk` item 17's
pipeline produces, behind a swappable `EmbeddingProvider`, and write it into the `vector(1536)`
column item 17 already created but never wrote to. Every chunk-writing call site (file
upload/reprocess, website crawl/recrawl, manual-FAQ entry create/update) gets embeddings wired
in; a provider failure or a dimension mismatch fails the operation explicitly rather than
writing a null, truncated, or padded vector.

## Design reference

None. Backend-only, matching items 14-17's precedent. No frontend exists for Knowledge yet.

## In scope

- **`EmbeddingProvider` protocol** (`app/providers/embedding.py`) - `async def embed(texts:
  list[str]) -> list[list[float]]`, one vector per input text, same order. Batched (one round
  trip for a whole document's chunks, not one per chunk) - OpenAI's embeddings endpoint natively
  accepts a list. Mirrors `SpeechToTextProvider`/`TextToSpeechProvider`'s exact shape:
  `EmbeddingProviderError` base, `EmbeddingProviderTimeout`, `EmbeddingProviderUnavailable`.
- **`MockEmbeddingProvider`** (`app/providers/mock_embedding.py`) - deterministic (same text ->
  same vector, always at the configured dimension), a public `embedded_texts: list[str]` a test
  can inspect (matching `MockStorage.objects`/`MockPageFetcher.pages`'s precedent), and a
  settable `failure: EmbeddingProviderError | None` constructor/attribute so a test can force the
  failure path (matching `MockSTT`'s exact `failure` parameter).
- **`OpenAIEmbeddingProvider`** (`app/providers/openai_embedding.py`) - `httpx`-based (no new SDK
  dependency; `httpx` is already a first-class dependency here, same restraint the web crawler
  already exercised), `POST https://api.openai.com/v1/embeddings`, Bearer auth, timeout-bounded.
  Validates the returned vector *count* matches the input text count, and every vector's
  *length* matches the configured dimension, raising `EmbeddingDimensionMismatch` rather than
  truncating or padding either way (CLAUDE.md section 6.4, a direct instruction). Maps HTTP/
  timeout failures onto `EmbeddingProviderTimeout`/`EmbeddingProviderUnavailable`. An empty
  `texts` list returns `[]` immediately with no API call - item 17 already allows a zero-chunk
  source (an empty `.txt`/`.md`), and that is not a reason to make a wasted request.
- **Factory + config** - `app/providers/factory.py` gets `get_embedding_provider`/
  `get_embedding_provider_dependency`, `UnknownEmbeddingProviderError`,
  `MissingOpenAiApiKeyError` (mirroring `MissingElevenLabsApiKeyError` exactly).
  `app/core/config.py` gets `embedding_provider: str = "mock"`, `openai_api_key: str = ""`,
  `embedding_model: str = "text-embedding-3-small"`, `embedding_dimension: int = 1536` -
  `OPENAI_API_KEY`/`EMBEDDING_MODEL`/`EMBEDDING_DIMENSION` are already in `.env`/`.env.example`
  (confirmed correctly named); `EMBEDDING_PROVIDER` is new, added to both alongside them.
  `app/api/deps.py` gets `EmbeddingProviderDep`. `tests/conftest.py`'s `client` fixture gets an
  `embedding_provider: MockEmbeddingProvider` fixture + dependency override, matching
  `storage`/`page_fetcher`'s exact wiring.
- **File-type wiring** - `_parse_and_chunk_document` embeds every chunk span's text right after
  chunking and before `replace_for_source`; a provider failure or dimension mismatch marks the
  source `'failed'` with a clear `error_message` (same shape as a parse failure), and - like a
  parse failure - never touches chunks already on record from an earlier successful run.
  `upload_knowledge_source` and `process_knowledge_source` (services) and their two routes
  (`POST .../knowledge-sources`, `POST .../{id}/process`) thread an `EmbeddingProviderDep`
  through.
- **Website-type wiring** - `_crawl_and_reconcile` embeds every chunk span the same way, right
  before the same `replace_for_source` call item 17 added. `create_website_knowledge_source`/
  `recrawl_knowledge_source` (services) and their two routes thread it through too.
- **Manual-FAQ-type wiring** - `create_faq_entry`/`update_faq_entry` embed the entry's one chunk
  text. Unlike file/website, a FAQ entry has no `status` field to report a failure through
  (item 16's own design), so a provider failure here fails the whole HTTP request instead (503 -
  "a provider outage they cannot fix," CLAUDE.md's own error-UX distinction) rather than
  creating/updating the entry with a missing or wrong embedding. `delete_faq_entry` is untouched
  - deleting needs no embedding.
- **`replace_for_source`/`upsert_for_faq_entry` (repository) accept embeddings** - extended with
  a small `ChunkWrite(text, metadata, embedding)` frozen dataclass replacing the current
  `(text, metadata)` tuple, and an `embedding: list[float] | None = None` parameter respectively.

## Out of scope

- **Retrieval.** Item 19 - reading embeddings back for a semantic search.
- **Backfilling embeddings for chunks that existed before this feature.** None do yet - item 17
  merged immediately before this feature in the same session, so there is no pre-existing
  unembedded data to migrate.
- **A background job queue.** Embedding stays synchronous inside the same request item 17's
  parsing/chunking already runs in - the identical deliberate, temporary tradeoff, now extended
  one stage further. `apps/worker` is still a heartbeat-only skeleton.
- **Retry/backfill tooling for a `'failed'` source caused by an embedding failure.** The existing
  `POST .../process` (file) and `POST .../recrawl` (website) retry paths already cover it - no
  new retry mechanism is needed.
- **An ANN index (HNSW/IVFFlat) on `Chunk.embedding`.** Not needed at MVP data volumes (a small
  business's knowledge base, not billions of vectors); flagged as a follow-up performance task
  if retrieval (item 19) or real load testing (item 61) later proves a sequential scan too slow.
- **Any frontend.** No Knowledge UI exists yet, matching items 14-17.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `EmbeddingProvider` foundation** - `app/providers/embedding.py` (protocol +
  exceptions), `app/providers/mock_embedding.py`, `app/providers/openai_embedding.py`; `app/
  providers/factory.py` gets the embedding branch; `app/core/config.py` gets the four new
  settings; `.env`/`.env.example` get `EMBEDDING_PROVIDER=mock`; `app/api/deps.py` gets
  `EmbeddingProviderDep`; `tests/conftest.py` gets the `embedding_provider` fixture + override.
  *Done when:* `tests/test_embedding_provider.py` covers `MockEmbeddingProvider` (deterministic,
  correct dimension, `embedded_texts` records calls, `failure` forces the error path) and
  `OpenAIEmbeddingProvider` against a fake `httpx` transport (success parses `data[].embedding`
  in order; a non-200 response and a timeout each map to the right exception; a response
  returning the wrong-length vector raises `EmbeddingDimensionMismatch`, never truncates or
  pads). No live OpenAI call anywhere in the suite. Full backend suite green. `ruff check
  apps/api` clean.

- [x] **Step 2 - file + website wiring** - `app/repositories/chunk.py` gets `ChunkWrite` and the
  updated `replace_for_source` signature; `app/services/knowledge_source.py`'s
  `_parse_and_chunk_document` and `_crawl_and_reconcile` embed before replacing; the four routes
  (`upload_knowledge_source`, `process_knowledge_source`, `create_website_knowledge_source`,
  `recrawl_knowledge_source`) thread `EmbeddingProviderDep` through; extend `tests/
  test_document_chunking.py`.
  *Done when:* after a file upload or a website crawl, every resulting chunk's `embedding` is
  non-null and exactly 1536 floats long (checked directly via the `db` fixture, since
  `ChunkResponse` never serializes embeddings - locked in item 17); forcing
  `embedding_provider.failure` marks the source `'failed'` with an `error_message` and leaves
  any prior successful chunks untouched, exactly like an existing parse-failure test already
  proves for file uploads. Full backend suite green. `ruff check apps/api` clean.

- [x] **Step 3 - manual-FAQ wiring** - `app/repositories/chunk.py`'s `upsert_for_faq_entry` gets
  an `embedding` parameter; `app/services/faq_entry.py`'s `create_faq_entry`/`update_faq_entry`
  embed the entry's chunk text; `app/api/v1/faq_entries.py`'s create/update routes thread
  `EmbeddingProviderDep` through and map `EmbeddingProviderError`/`EmbeddingDimensionMismatch` to
  503; extend `tests/test_document_chunking.py`.
  *Done when:* creating or updating a FAQ entry produces/updates a chunk with a non-null,
  1536-float embedding; forcing `embedding_provider.failure` on create returns 503 and the entry
  is not persisted (a follow-up list call shows it absent); same for update, leaving the
  original entry/chunk untouched. Full backend suite green. `ruff check apps/api` clean.

## Files / areas

**New**
- `apps/api/app/providers/embedding.py`
- `apps/api/app/providers/mock_embedding.py`
- `apps/api/app/providers/openai_embedding.py`
- `apps/api/tests/test_embedding_provider.py`

**Modified**
- `apps/api/app/providers/factory.py`, `apps/api/app/core/config.py`, `apps/api/app/api/deps.py`
- `apps/api/app/repositories/chunk.py` (`ChunkWrite`, embedding parameters)
- `apps/api/app/services/knowledge_source.py`, `apps/api/app/services/faq_entry.py`
- `apps/api/app/api/v1/knowledge_sources.py`, `apps/api/app/api/v1/faq_entries.py`
- `apps/api/tests/conftest.py`, `apps/api/tests/test_document_chunking.py`
- `.env`, `.env.example`

**Unchanged**
- `apps/api/app/models/chunk.py` - the `embedding` column already exists (item 17); this feature
  only ever writes to it, never alters its shape.
- No frontend file.

## Data / contracts

**`EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`** - one vector per input
text, same order, always at `settings.embedding_dimension` length or the call raises.

**`ChunkWrite`** - `{text: str, metadata: dict, embedding: list[float] | None}` - the repository
input shape `replace_for_source` now takes instead of a bare `(text, metadata)` tuple.

## Testing

The backend gate is live - every step ships its tests in the same diff. Step 1's provider pair
is pure/mocked logic with a real wrong-answer surface (exactly `coding-standards.md`'s unit-test
class), covered directly. Steps 2-3 extend item 17's own integration test file, proving the
embedding side-effect through the same API surface that file already exercises.

## Notes for the AI

- **Embed before replacing, exactly like parsing before replacing.** `replace_for_source` (file/
  website) must only run after both parsing/chunking AND embedding succeed - a failure at either
  stage must leave prior good chunks untouched, matching item 17's own established discipline.
- **Never truncate or pad a vector to fit the configured dimension.** A mismatch is
  `EmbeddingDimensionMismatch`, always a hard failure - CLAUDE.md section 6.4 is explicit about
  this.
- **Batch the embedding call per source, not per chunk.** One `embed(texts)` call for all of a
  document's (or crawl's) chunk texts, not N calls.
- **Reuse the exact `SpeechProviderError` hierarchy shape** for `EmbeddingProviderError` - same
  three-exception shape (base, timeout, unavailable), same reasoning.
- **`.env`/`.env.example` already have `OPENAI_API_KEY`/`EMBEDDING_MODEL`/`EMBEDDING_DIMENSION`
  correctly named** (per `project-overview.md`'s own drift note) - only `EMBEDDING_PROVIDER` is
  new. Do not rename or duplicate the existing three.
- A push, if any, at the end of this feature still needs your explicit go-ahead per the
  standing project convention - **except this run, where the user has explicitly waived that
  and asked for the recommendation to be carried out directly.**
