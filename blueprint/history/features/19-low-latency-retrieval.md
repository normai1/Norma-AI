# Feature: Low-latency retrieval

**From build-plan:** feature 19

**Status:** not started

## Goal

Turn an embedded knowledge base (items 17-18) into answers: embed a caller's query, run a
tenant-scoped pgvector similarity search, and assemble the top matches into a bounded context
string an LLM turn can use - each result carrying its source (`knowledge_source_id`, type,
metadata) for citation. Built and measured as a pure backend service now; item 20 (the real-time
voice engine, unbuilt) is this function's first live caller.

## Design reference

None. Backend-only, matching items 14-18's precedent. No frontend exists for Knowledge yet.

## In scope

- **`retrieve()`** (`app/services/retrieval.py`) - embeds the query via `EmbeddingProvider`, runs
  a pgvector cosine-distance search over `Chunk` filtered by `organization_id` + `workspace_id`,
  excluding rows with a `NULL` embedding (not yet indexed), ordered by similarity, bounded to
  `top_k` (default 5). Returns `RetrievedChunk` - `{chunk_id, knowledge_source_id, source_type,
  text, metadata, score}` - `score` is `1 - cosine_distance` (higher is more similar). Joins
  `KnowledgeSource` for `source_type` rather than a second query per chunk.
- **`assistant_id` is accepted and validated, not yet a narrowing filter** - see reconciliation
  below. `retrieve()` confirms the given assistant exists in the given workspace (reusing
  `assistant_repo.get_by_id` + `AssistantNotFound`), so a caller can't retrieve under a bogus or
  cross-workspace assistant id, but the actual chunk set returned is bounded by organization +
  workspace only.
- **`build_context()`** (`app/services/context_builder.py`) - pure function, `list[RetrievedChunk]
  -> str`. Packs chunks in ranked order up to a fixed character budget (`4000`, a conservative
  starting point per `project-overview.md`'s "keep retrieved context bounded" guidance); once
  the next chunk would exceed the budget, it is dropped whole rather than truncated mid-chunk,
  so every chunk that appears in the context is either complete or absent, never partial - the
  same "never truncate to fit" discipline the embedding dimension check already established.
- **`app/repositories/chunk.py` gets a similarity-search query** - `search_by_similarity(db, *,
  organization_id, workspace_id, query_vector, top_k) -> list[tuple[Chunk, str]]` (chunk +
  source type), used only by `retrieve()`.
- **Latency regression test** - `retrieve()` + `build_context()` together, against the Mock
  embedding provider and a small realistic chunk set in the test database, asserting a
  documented ceiling. This is a regression guard against an obvious local slowdown (an N+1
  query, a missing filter forcing a full scan), not the product's real p95 measurement - that is
  item 61's job, against production topology and a real embedding provider, once a real call
  path exists.

## Out of scope

- **A live consumer.** Item 20 (the real-time voice session engine) is unbuilt; item 21 (in-
  browser test call) too. `retrieve()`/`build_context()` are complete, tested service functions
  with no caller yet - exactly the shape item 20's per-turn context assembly stage will call
  into once it exists.
- **An HTTP route to exercise retrieval directly.** With no consumer, a route here would be dead
  API surface with nothing to authorize against in practice. Tested instead by calling the
  service function directly against the test database and a `MockEmbeddingProvider` instance -
  the same pattern `test_organization_service.py` already establishes for this codebase.
- **Persisting "which chunks answered this call" as a durable record.** `Call`/`TranscriptTurn`
  (items 26, 20) don't exist yet, so there is nothing to attach that record to. `retrieve()`
  already returns full source attribution per chunk - satisfying "source attribution recorded
  per answer" is a matter of the future caller storing what this function already hands it, not
  something this feature can persist on its own.
- **Real per-assistant knowledge partitioning.** See the reconciliation below - no feature yet
  assigns a knowledge source to one specific assistant; every assistant in a workspace shares
  the workspace's one knowledge base today.
- **An ANN index (HNSW/IVFFlat) on `Chunk.embedding`.** Already flagged as a follow-up in item
  18's own spec; still not needed at MVP data volumes.
- **Caching embeddings for repeated caller phrasings, or preloading high-frequency FAQ content
  into the prompt.** Both are named as retrieval-latency techniques in `project-overview.md`,
  but neither is buildable sensibly without a real call loop (item 20) generating repeated
  phrasings or usage data to preload from.
- **Any frontend.** No Knowledge UI exists yet, matching items 14-18.

## Reconciliation: what "assistant-scoped" retrieval means today

CLAUDE.md section 6.4 and this build-plan line both say retrieval is scoped by "organization,
workspace, and assistant." Checked against the actual schema: `KnowledgeSource` (and therefore
`Chunk`) has **no relationship to `Assistant` at all** - it is purely workspace-scoped, and no
feature in the 62-item build plan assigns a knowledge source to one specific assistant (items
14-16's create routes never even take an `assistant_id`). Every assistant lives inside exactly
one workspace, so filtering by `workspace_id` already bounds retrieval to at most that
assistant's own workspace's knowledge - there is no second, narrower boundary to enforce yet
because nothing yet creates one.

Retrofitting mandatory per-assistant knowledge assignment onto items 14-16's already-shipped,
tested create routes would be a breaking schema change to three complete features, in service of
a capability nothing in the roadmap has asked for - that is out of scope for "low-latency
retrieval." Instead: `retrieve()` takes `assistant_id` as a required parameter now (so the
signature never has to change when real per-assistant assignment eventually lands), validates it
resolves to a real assistant in the given workspace, and uses it for nothing narrower than that
today. `project-overview.md`'s per-assistant "chunk-count ceiling" language reads the same
way - a per-call top-k cap, not a knowledge partition.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `retrieve()` and the similarity-search query** - `app/repositories/chunk.py`
  gets `search_by_similarity`; `app/services/retrieval.py` (`RetrievedChunk`, `retrieve()`).
  *Done when:* `tests/test_retrieval.py` proves: results are ordered by similarity - since
  `MockEmbeddingProvider`'s vectors are hash-seeded per exact string with no semantic
  relationship between similar text (item 18's own design), test ordering by giving one stored
  chunk the *exact same text* as the query (a provably zero cosine distance under the
  deterministic mock) and confirming it ranks first ahead of differently-worded chunks, not by
  relying on natural-language semantic similarity the mock does not model; tenant isolation (a
  chunk in a sibling organization/workspace never appears);
  `NULL`-embedding chunks (an unembedded/failed source) are excluded; `top_k` is respected;
  `assistant_id` belonging to a different workspace raises `AssistantNotFound`; each result
  carries the correct `source_type` for its knowledge source. Full backend suite green. `ruff
  check apps/api` clean.

- [x] **Step 2 - `build_context()` and the latency regression test** - `app/services/
  context_builder.py`.
  *Done when:* `tests/test_context_builder.py` covers empty input (empty string), a single
  chunk, multiple chunks packed under the budget, and a chunk set that exceeds the budget
  (the excess chunk is dropped whole, not truncated - the included chunks' full text is still
  present verbatim). `tests/test_retrieval.py` gets a latency regression test: `retrieve()` +
  `build_context()` together, `MockEmbeddingProvider`, a realistic small chunk set, wall-clock
  time under a documented ceiling - explicitly noted as a local regression guard, not item 61's
  production p95 measurement. Full backend suite green. `ruff check apps/api` clean.

## Files / areas

**New**
- `apps/api/app/services/retrieval.py`
- `apps/api/app/services/context_builder.py`
- `apps/api/tests/test_retrieval.py`
- `apps/api/tests/test_context_builder.py`

**Modified**
- `apps/api/app/repositories/chunk.py` (`search_by_similarity`)

**Unchanged**
- No route, no schema, no frontend. `app/models/chunk.py` - unchanged, `embedding` already
  exists and is already populated (item 18).

## Data / contracts

**`RetrievedChunk`** - `{chunk_id: UUID, knowledge_source_id: UUID, source_type: str, text: str,
metadata: dict, score: float}`.

## Testing

The backend gate is live - every step ships its tests in the same diff. Both new modules are
pure/service-layer logic with a real wrong-answer surface (ranking, isolation, budget-packing),
exactly `coding-standards.md`'s unit-test class. Tests call the service functions directly
against the `db` fixture and a locally-constructed `MockEmbeddingProvider`, matching
`test_organization_service.py`'s existing direct-service-call precedent - no route exists to
test through.

## Notes for the AI

- **`assistant_id` is validated, not yet a narrowing filter.** Do not add a `Chunk.assistant_id`
  column or an assistant-knowledge join table - that is real scope creep onto items 14-16's
  already-shipped routes. Read the Reconciliation section above before touching this.
- **Never truncate a chunk to fit the context budget.** Drop the whole chunk instead - matches
  item 18's "never truncate or pad a vector" discipline applied to text packing instead.
- **The latency test is a regression guard, not a product SLA measurement.** Say so in the test
  itself (docstring/comment) so nobody mistakes it for item 61's real validation later.
- **Reuse `assistant_repo.get_by_id` and `AssistantNotFound`** (already exist, item 11) - no new
  exception type needed for the assistant-validation check.
- A push, if any, at the end of this feature still needs your explicit go-ahead per the
  standing project convention - **except this run, where the user has explicitly waived that
  and asked for the recommendation to be carried out directly.**
