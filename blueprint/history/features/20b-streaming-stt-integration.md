# Feature: Streaming STT integration

**From build-plan:** feature 20b

**Status:** not started

## Goal

Wire real streaming speech-to-text into the voice pipeline 20a established: audio arriving over
the WebSocket flows through Norma's own `SpeechToTextProvider` (item 9a/9b, currently only
usable from `apps/api`), producing partial and final transcripts biased toward each assistant's
glossary terms (item 13a). This is the first time `apps/voice` needs application code that today
lives only in `apps/api` - a real architectural boundary CLAUDE.md section 4 already anticipates
("Shared models, schemas, and provider abstractions used by both `apps/api` and `apps/voice`
must live in a shared location, not be duplicated") and section 5.1 names the two ways across it
("shared repositories or a narrow internal API"). This feature has to actually build that
boundary, not just the STT logic sitting on top of it.

## Design reference

None. Backend-only.

## Architecture decisions (read before building)

- **Speech providers move to a shared package; glossary data crosses via a narrow internal
  API - not the other way round.** `SpeechToTextProvider`/`TextToSpeechProvider` are pure
  provider abstractions with no database dependency - moving them to a package both services
  import is a clean, low-risk extraction. `GlossaryEntry`, by contrast, is a full tenant-scoped
  database model with its own repository, migrations, and existing CRUD API (item 13a) - moving
  *that* into a shared package would mean `apps/voice` also needing a database connection,
  SQLAlchemy, and Alembic awareness, which is a much bigger commitment than one feature fetching
  a handful of term strings needs. A narrow internal endpoint (`apps/voice` asks `apps/api` for
  an assistant's glossary terms over HTTP) is the smaller, more honest slice for what 20b
  actually requires. Revisit only if a later item (20d's assistant-config/retrieval needs, most
  likely) makes the shared-repository path clearly worth the bigger investment.
- **`packages/shared/` is a new, genuinely pip-installable local package** (`norma_shared`),
  holding `speech.py`/`mock_speech.py`/`elevenlabs_speech.py` moved unchanged from
  `apps/api/app/providers/`. Both `apps/api` and `apps/voice` install it via a local editable
  path in their own `requirements.txt`. Docker Compose's *additional build contexts* feature
  (`build.additional_contexts` under each service) pulls `packages/shared` into each image
  without changing either service's existing primary build context or `COPY` paths - a smaller,
  lower-risk change than repointing both services' build context to the repo root.
- **The custom `SpeechToTextProcessor` (Pipecat `FrameProcessor`) is hand-written, not a
  subclass of Pipecat's own `STTService`/`AIService`.** Verified in the installed package:
  Pipecat's `STTService.run_stt(audio: bytes) -> AsyncGenerator[Frame, None]` is a per-chunk
  contract, while Norma's `SpeechToTextProvider.stream(audio: AsyncIterator[bytes], ...)` owns
  the whole stream itself (matching how `ElevenLabsSTT` manages one realtime connection). Forcing
  the whole-stream provider into the per-chunk base class would be an awkward, lossy fit. This
  mirrors item 20a's own precedent (a hand-written `EchoProcessor`, not Pipecat's `IdentityFilter`)
  and item 20a's core reason for existing: pipeline stages sit behind Norma's own interfaces, not
  Pipecat's vendor-specific service classes - using Pipecat's *own* ElevenLabs STT service
  directly would silently bypass the swappable-provider abstraction items 9a/9b were built for.
- **Transcripts flow through Pipecat's own `TranscriptionFrame`/`InterimTranscriptionFrame`** -
  real, provider-agnostic frame types already in the installed package (verified, not assumed),
  carrying `text`/`user_id`/`timestamp`/`finalized`. Reusing Pipecat's own frame types (as
  opposed to inventing a parallel Norma-specific frame class) keeps the pipeline mechanically
  compatible with anything else in Pipecat's ecosystem that already expects them, while the
  *provider* behind those frames stays entirely Norma's own.
- **The test proof is transcript-over-websocket-as-JSON, not audio-in-audio-out.** 20a's echo
  stage is replaced, not kept alongside; `RawAudioFrameSerializer` (20a) gains a branch so
  `TranscriptionFrame`/`InterimTranscriptionFrame` serialize to a JSON text WebSocket message
  (`{"type": "transcript", "text", "is_final", "confidence"}`) instead of audio bytes. This is
  the only way to observe a transcript at all from outside the pipeline right now - there is no
  LLM/TTS stage yet to turn it into a spoken reply.
- **`MockSTT`'s drain-then-yield limitation (finding F-40, already recorded) blocks writing a
  meaningful test here and gets fixed as part of this feature**, not deferred again. Item 20b is
  exactly the "whoever builds item 20" F-40's own suggested fix named. Proving "partial and
  final transcripts" stream realistically - a partial arriving before all audio has been sent -
  needs `MockSTT` able to interleave, which it cannot do today.

## In scope

- **`packages/shared/norma_shared/`** - `speech.py`, `mock_speech.py`, `elevenlabs_speech.py`
  moved from `apps/api/app/providers/` verbatim (import paths inside them updated to
  `norma_shared.*`), plus a minimal `pyproject.toml` making it a local installable package.
- **`apps/api` consumes the moved package** - `app/providers/factory.py`, `app/api/deps.py`,
  `app/api/v1/voices.py`, and the existing speech-provider tests update their imports from
  `app.providers.speech`/`mock_speech`/`elevenlabs_speech` to `norma_shared.*`. No behavior
  change - a pure extraction, proven by the full existing test suite passing unchanged.
- **`apps/voice` consumes the same package** - its own `requirements.txt` gets the local
  editable install; a small `app/provider_factory.py` (mirroring `apps/api`'s factory shape, but
  scoped to just an `STT_PROVIDER` env var for now - TTS provider selection is item 20e's job)
  resolves `MockSTT` or `ElevenLabsSTT`.
- **`GET /internal/v1/assistants/{assistant_id}/glossary`** (new, `apps/api`) - returns
  `{"terms": ["term1", "term2", ...]}` for the given assistant (plain term strings only -
  `SpeechToTextProvider.stream()`'s `keywords: Sequence[str]` takes nothing richer; a term's
  `phonetic_spelling` is TTS-pronunciation data for item 20e, not STT input). Authenticated by a
  shared secret header (`X-Internal-Secret` matched against a new `INTERNAL_API_SECRET` setting)
  - there is no user session inside a live call for a JWT to belong to. 404 if the assistant
  does not exist (no tenant context to scope against here - the secret itself is the trust
  boundary, matching the same reasoning telephony webhook signatures already establish).
- **`apps/voice` glossary client** - a small `httpx`-based function calling the endpoint above,
  given the assistant id and using two new env vars (`API_INTERNAL_URL`, defaulting to
  `http://api:8000` inside Compose; `INTERNAL_API_SECRET`, shared with `apps/api`). A connection
  failure or non-200 response returns an empty term list rather than raising - glossary biasing
  is an enhancement, not a hard dependency; STT must still work without it (CLAUDE.md's "provider
  failure never produces silence" reasoning extended to a non-critical enhancement failing open).
- **`MockSTT` gains an interleaving mode** (`packages/shared/norma_shared/mock_speech.py`) -
  resolves finding F-40. A new optional scripting shape lets a test say "yield this transcript
  event after consuming N audio chunks," rather than only "drain everything, then yield the
  whole script."
- **`SpeechToTextProcessor`** (new, `apps/voice/app/`) - the `FrameProcessor` bridging
  `InputAudioRawFrame`s into `SpeechToTextProvider.stream()` and pushing
  `TranscriptionFrame`/`InterimTranscriptionFrame` downstream, per the architecture decisions
  above.
- **`RawAudioFrameSerializer` gains a text-serialization branch** for the two transcript frame
  types (20a's module, `apps/voice/app/media_session.py`).
- **Pipeline wiring** - the `/media/echo` route and its pipeline builder are replaced by a
  `/media/session` route (name reflects that it is no longer just an echo) built around
  `SpeechToTextProcessor`, taking a `language` and the assistant's glossary terms (fetched via
  the client above) as construction inputs.
- **End-to-end test** - a WebSocket test proving: audio sent in produces a `transcript` JSON
  message out; glossary terms fetched for the assistant reach the provider's `keywords`
  argument; `MockSTT`'s new interleaving mode demonstrates a partial transcript arriving before
  all audio has been sent, followed by a final transcript.

## Out of scope

- **Turn detection (VAD + semantic).** Item 20c - this feature streams every transcript event
  Norma's provider produces; nothing yet decides when the caller has "finished" a turn.
- **The LLM turn loop and streaming TTS.** Items 20d/20e - transcripts are observed as JSON over
  the test WebSocket, not fed into a model or spoken back.
- **Real telephony audio (Twilio media streams).** Item 23+ - the test client is still a raw
  WebSocket, matching 20a's own precedent.
- **`TextToSpeechProvider` usage from `apps/voice`.** The shared package carries it (it lives in
  the same `speech.py` file as the STT contract), but nothing calls it until item 20e.
- **Moving any other `apps/api` provider** (storage, embedding, web crawler) **into the shared
  package.** Only what 20b concretely needs moves now; item 20d moves whatever it concretely
  needs when it gets there.
- **`stt_boost_weight`-driven per-term biasing strength.** The current `SpeechToTextProvider`
  contract only accepts a flat list of keyword strings; extending it to carry a per-term weight
  is a real contract change to a locked item-9a shape, out of scope here. Every glossary term
  is passed as an equally-weighted keyword.
- **A durable record of which glossary terms informed a given transcript.** No `Call`/
  `TranscriptTurn` tables exist yet (item 26) to attach that to.
- **Retrying a failed glossary fetch, or caching it across turns.** One fetch per session for
  now; failing open to an empty list is the extent of this feature's resilience story - item
  20g (session resilience) is where a real retry/caching policy belongs.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - extract the shared speech-provider package, `apps/api` side** -
  `packages/shared/norma_shared/{speech,mock_speech,elevenlabs_speech}.py` (moved, internal
  imports updated) + minimal `pyproject.toml`; `apps/api/requirements.txt` gets a local editable
  install line; `apps/api/app/providers/factory.py`, `app/api/deps.py`, `app/api/v1/voices.py`,
  `tests/test_elevenlabs_speech.py`, `tests/test_speech_providers.py`, `tests/test_voices.py`
  update their imports; `apps/api/Dockerfile` + `docker-compose.yml`'s `api` service get an
  additional build context for `packages/shared`.
  *Done when:* full `apps/api` backend suite green, unchanged in count and content (a pure
  extraction - no behavior change). `docker compose build api && docker compose up -d api`
  succeeds; `GET /api/v1/health` still 200. `ruff check apps/api` clean.

- [x] **Step 2 - `apps/voice` consumes the shared package** - `apps/voice/requirements.txt` gets
  the same local editable install; `apps/voice/app/provider_factory.py` (new, `STT_PROVIDER`-
  based, mirrors `apps/api`'s factory shape); `apps/voice/Dockerfile` +
  `docker-compose.yml`'s `voice` service get the same additional build context.
  *Done when:* a new `apps/voice/tests/test_provider_factory.py` proves the factory resolves
  `MockSTT` by default and raises a clear error for an unknown `STT_PROVIDER` value. `docker
  compose build voice && docker compose up -d voice` succeeds; `GET /health` (voice) still 200.
  `ruff check apps/voice` clean.

- [x] **Step 3 - internal glossary endpoint + `apps/voice` client** - `apps/api` gets
  `INTERNAL_API_SECRET` setting, `app/api/internal_deps.py` (secret-header auth dependency), a
  new `app/api/internal/glossary.py` route under `/internal/v1`; `.env`/`.env.example` get
  `INTERNAL_API_SECRET`. `apps/voice` gets `app/config.py` (plain `os.environ` reads for
  `API_INTERNAL_URL`/`INTERNAL_API_SECRET`, matching `apps/worker`'s existing minimalism) and
  `app/glossary_client.py` (the `httpx` call, failing open to `[]`).
  *Done when:* `apps/api` gets a new `tests/test_internal_glossary.py` - the endpoint returns
  the right terms for a real assistant with glossary entries, 404s for an unknown assistant,
  401s with a missing/wrong secret header, and is unreachable without the header regardless of
  any user session. `apps/voice` gets `tests/test_glossary_client.py` proving a successful
  fetch, a non-200 response, and a connection failure all resolve to `[]` (via a fake `httpx`
  transport, no real network call). Full `apps/api` suite still green. `ruff check` clean on
  both services.

- [x] **Step 4 - `MockSTT` interleaving and keyword recording (resolves F-40)** -
  `packages/shared/norma_shared/mock_speech.py` gets the interleaving capability described in
  the Architecture decisions above. While already touching this class: `keywords` is currently
  discarded entirely (verified - `stream()` takes it but never stores it) with nothing recording
  what was passed, unlike `MockEmbeddingProvider.embedded_texts`'s established precedent for
  this exact class of test need; add a public `received_keywords: list[str] | None` attribute
  set on each `stream()` call, so Step 5 can assert glossary terms actually reached the provider.
  *Done when:* a new test in `apps/api/tests/test_speech_providers.py` proves a scripted
  transcript event is yielded after its configured number of audio chunks are consumed, not
  only after the whole iterator is drained - i.e., a partial event can arrive while the mock is
  still mid-stream. A second new test proves `received_keywords` captures exactly what `stream()`
  was called with. Existing `MockSTT` tests (drain-then-yield default behavior) keep passing
  unchanged. Full `apps/api` suite green. `ruff check apps/api` clean. Mark F-40 `fixed` in
  `blueprint/context/findings.md`.

- [x] **Step 5 - `SpeechToTextProcessor`, serializer, pipeline wiring, end-to-end test** -
  `apps/voice/app/media_session.py` gets `SpeechToTextProcessor` and the serializer's new
  transcript-to-JSON branch; `/media/echo` becomes `/media/session` in `app/main.py`, built with
  the glossary client's terms and the provider factory's resolved STT provider; new
  `apps/voice/tests/test_speech_to_text_processor.py` (or extending `test_media_session.py`).
  *Done when:* sending audio over the WebSocket produces one or more `{"type": "transcript",
  ...}` JSON messages back, using `MockSTT`'s new interleaving mode to prove a partial transcript
  arrives before the final one; the glossary client's returned terms are asserted to reach the
  provider's `stream(..., keywords=...)` call (via `MockSTT`'s own call-recording, matching
  `MockEmbeddingProvider.embedded_texts`'s precedent). Full `apps/voice` test suite green.
  `ruff check apps/voice` clean. `docker compose build voice && docker compose up -d voice`
  succeeds.

## Files / areas

**New**
- `packages/shared/norma_shared/speech.py`, `mock_speech.py`, `elevenlabs_speech.py`,
  `pyproject.toml`, `__init__.py`
- `apps/voice/app/provider_factory.py`, `app/config.py`, `app/glossary_client.py`
- `apps/api/app/api/internal_deps.py`, `app/api/internal/glossary.py`
- `apps/voice/tests/test_provider_factory.py`, `test_glossary_client.py`,
  `test_speech_to_text_processor.py` (or extended `test_media_session.py`)
- `apps/api/tests/test_internal_glossary.py`

**Modified**
- `apps/api/app/providers/factory.py`, `app/api/deps.py`, `app/api/v1/voices.py`, `app/main.py`
  (new internal router registration), `app/core/config.py` (`internal_api_secret`)
- `apps/api/tests/test_elevenlabs_speech.py`, `test_speech_providers.py`, `test_voices.py`
- `apps/api/requirements.txt`, `apps/voice/requirements.txt`
- `apps/api/Dockerfile`, `apps/voice/Dockerfile`, `docker-compose.yml`
- `apps/voice/app/main.py`, `app/media_session.py`
- `.env`, `.env.example`
- `blueprint/context/findings.md` (F-40 -> `fixed`)

**Deleted**
- `apps/api/app/providers/speech.py`, `mock_speech.py`, `elevenlabs_speech.py` (moved, not
  duplicated)

## Data / contracts

**Internal glossary response** - `{"terms": list[str]}`. Deliberately minimal - see the
`stt_boost_weight` exclusion above.

**`norma_shared.speech`** - identical contract to today's `app.providers.speech`
(`SpeechToTextProvider`, `TextToSpeechProvider`, `TranscriptEvent`, `Voice`, the
`SpeechProviderError` hierarchy) - only its import path changes.

## Testing

The backend gate is live for both services now - `apps/api`'s via its existing `pytest`
convention, `apps/voice`'s via the command item 20a's own build established. Step 1 is a pure
refactor proven by the unchanged existing suite; Steps 2-5 each ship focused new tests for the
new logic they add, matching `coding-standards.md`'s unit/integration split.

## Notes for the AI

- **Verify Docker Compose's `additional_contexts` syntax empirically during Step 1**, the same
  way item 20a verified Pipecat's real API against the installed package rather than assumed
  docs - run the actual `docker compose build`, don't assume the YAML shape compiles correctly
  on the first attempt.
- **Move files with `git mv` where practical**, preserving history, rather than delete-and-
  recreate.
- **Step 1's done-when is strict: zero behavior change.** If anything about `apps/api`'s test
  count or content needs to change to make Step 1 pass, that is a sign the extraction leaked a
  behavior change - stop and reconsider rather than adjusting the tests to fit.
- **`apps/voice` failing open on a glossary-fetch failure must not fail open on STT itself** -
  losing glossary biasing is acceptable degradation; losing transcription is not. Keep these two
  failure modes clearly separate in the implementation.
- **`MockSTT.received_keywords` (Step 4) mirrors `MockEmbeddingProvider.embedded_texts`'s exact
  pattern** - a public, inspectable attribute recording what was actually passed in, not just
  accepted and discarded.
- A push, if any, at the end of this feature still needs your explicit go-ahead per the standing
  project convention - no override was given this time, so ask before both the squash-merge and
  the push, exactly like items 14/15/17.
