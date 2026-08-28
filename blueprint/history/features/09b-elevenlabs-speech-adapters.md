# Feature: ElevenLabs speech adapters

**From build-plan:** feature 9b
**Status:** not started

## Goal

Implement 9a's two contracts against the real vendor: `ElevenLabsSTT` on the Scribe v2
Realtime WebSocket API, and `ElevenLabsTTS` on the streaming HTTP synthesis and voice
catalogue endpoints. Completing this checks off build-plan item 9 entirely.

Every test runs against a stubbed transport — no network, no API key, no spend.

## Design reference

None. No UI ships in this feature.

## Verified vendor API shapes

Confirmed against ElevenLabs' current documentation while writing this spec, not from
memory. These are the facts the adapters are built on:

**Realtime STT** — `wss://api.elevenlabs.io/v1/speech-to-text/realtime`
- Auth: `xi-api-key` header (server-side; the single-use `token` query param is for
  browser clients and is not what this adapter uses).
- `audio_format=pcm_16000` is both the default and **exactly 9a's canonical format** —
  no transcoding in the adapter.
- `keyterms` query parameter is "a list of keyterms the model is biased towards" — this
  is 9a's `keywords` hook, and therefore item 13's glossary biasing.
- `commit_strategy=vad` with VAD tuning params, or `manual`.
- Client sends JSON: `{"message_type": "input_audio_chunk", "audio_base_64": "...",
  "commit": bool}` — **base64 inside JSON, not raw binary frames.**
- Server sends `{"message_type": "partial_transcript", "text": ...}` and
  `{"message_type": "committed_transcript", "text": ...}`, plus error types including
  `auth_error`, `quota_exceeded`, `rate_limited`, `transcriber_error`.
- No confidence score on plain partial/committed messages — only per-word `logprob` on
  the timestamps variant, which this feature does not request. 9a typed
  `TranscriptEvent.confidence` as `float | None`; ElevenLabs supplies `None`.

**Streaming TTS** — `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream`
- Auth: `xi-api-key` header. Body: `text`, `model_id`, `voice_settings` (which carries
  `speed`, mapping to 9a's `speed` parameter). Query: `output_format=pcm_16000`.
- Response is a continuous stream of raw audio bytes.

**Voice catalogue** — `GET https://api.elevenlabs.io/v2/voices` (note **v2**)
- Returns `voices[]` with `voice_id`, `name`, and an optional free-form `labels` object
  where gender/language live, plus a structured `verified_languages[]`. Paginated with
  `has_more` / `next_page_token`.

> **Deployment caveat, not a code problem:** ElevenLabs gates PCM output formats to Pro
> tier and above. On a lower tier `output_format=pcm_16000` fails, and the only formats
> available are MP3/Opus, which would need decoding to reach 9a's canonical PCM. Worth
> confirming the account tier before the first live call; nothing in this feature can
> work around it.

## In scope

- `elevenlabs_api_key` setting and `.env.example` entry.
- `websockets` added to `apps/api/requirements.txt` (the one new dependency).
- `app/providers/elevenlabs_speech.py`: `ElevenLabsTTS` and `ElevenLabsSTT`.
- A pure message-mapping function translating realtime STT JSON to `TranscriptEvent`
  or a domain error, unit-tested independently of any socket.
- Error mapping from HTTP status codes and ElevenLabs error message types onto 9a's
  `SpeechProviderError` / `SpeechProviderTimeout` / `SpeechProviderUnavailable`.
- Factory `"elevenlabs"` branches, with a clear failure when the API key is unset.
- pytest coverage for all of it against stubbed transports.

## Out of scope

- **Live calls against the real ElevenLabs API as a done-when.** Tests stub the
  transport. A manual smoke test needs a real Pro-tier key and is noted below as
  optional, never as a gate — CLAUDE.md §28 requires the suite run with zero paid
  external calls.
- **Moving providers to a shared `packages/` location.** Still item 20a's job, exactly
  as 9a recorded. `websockets` lands in `apps/api/requirements.txt` now and moves with
  the package then.
- **Word-level timestamps, entity/PII detection, `no_verbatim`, secondary languages.**
  Real ElevenLabs features, none of them needed by 9a's contract. Adding them now would
  be building for item 20 before item 20 asks.
- **`enable_logging=false` (vendor-side zero retention).** A real hook for the
  no-retention mode, which is post-MVP item 70. Noted so item 70 knows it exists.
- **The WebSocket text-input TTS API** (`/stream-input`). It exists for feeding text
  incrementally as LLM tokens arrive. 9a's `synthesize(text: str, ...)` takes complete
  text, and item 20e chunks on sentence boundaries and calls it per sentence, so HTTP
  streaming is the correct fit. Revisit only if item 20f measurements demand it.
- **Retry and failover policy.** The adapters raise typed errors; deciding to retry,
  forward, or take a message is item 20g's.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 — Config and `ElevenLabsTTS.synthesize()`** — add `elevenlabs_api_key` to
  `Settings` (default `""`) and `.env.example`; create
  `app/providers/elevenlabs_speech.py` with `ElevenLabsTTS`, synthesizing via
  `POST /v1/text-to-speech/{voice_id}/stream` with `output_format=pcm_16000`, an
  injectable `httpx.AsyncClient` for testing, an explicit request timeout, and HTTP
  status mapped onto 9a's error types.
  *Done when:* `pytest` passes with tests using `httpx.MockTransport` proving: a 200
  streams the stubbed audio bytes through unchanged; `speed` is sent inside
  `voice_settings` and `output_format=pcm_16000` on the query; empty text yields an empty
  stream without issuing a request at all; 401 raises `SpeechProviderUnavailable`; 429
  raises `SpeechProviderUnavailable`; 500 raises `SpeechProviderUnavailable`; a transport
  timeout raises `SpeechProviderTimeout`; and abandoning the generator mid-stream closes
  the response without raising.

- [x] **Step 2 — `ElevenLabsTTS.list_voices()`** — fetch `GET /v2/voices`, follow
  `next_page_token` while `has_more`, and map each voice onto 9a's `Voice`.
  *Done when:* `pytest` passes with `MockTransport` tests proving: `voice_id`/`name` map
  across; `gender` comes from `labels.gender` and is `None` when the label is absent;
  `language` resolves from `labels.language`, falling back to the first
  `verified_languages` locale, and to `"en"` when neither exists; a two-page response is
  fully collected in order; **pagination stops at a hard page cap and logs that it
  truncated** rather than following pages forever; and an error status maps to
  `SpeechProviderUnavailable`.

- [x] **Step 3 — Realtime transcript message mapping** — add `websockets` to
  `requirements.txt`, and a pure function turning one decoded ElevenLabs realtime
  message into `TranscriptEvent | None` or raising a mapped domain error. No socket
  code in this step.
  *Done when:* `pytest` passes with tests proving: `partial_transcript` maps to
  `is_final=False`; `committed_transcript` maps to `is_final=True`; `confidence` is
  `None` in both cases; `session_started` returns `None` (ignored, not an error);
  `auth_error`, `quota_exceeded`, and `rate_limited` each raise
  `SpeechProviderUnavailable`; `transcriber_error` raises `SpeechProviderError`; an
  unrecognized `message_type` returns `None` rather than crashing the call; and a message
  missing `message_type` entirely returns `None`.

- [x] **Step 4 — `ElevenLabsSTT.stream()` websocket lifecycle** — connect with
  `audio_format=pcm_16000`, `language_code`, `commit_strategy=vad`, and `keyterms` from
  9a's `keywords`; run concurrent send and receive so audio uploads while transcripts
  arrive; base64-encode each chunk into an `input_audio_chunk` message; feed every
  received message through Step 3's mapper; close the socket cleanly when the caller
  abandons the iterator.
  *Done when:* `pytest` passes against an injected fake connection (no real socket)
  proving: scripted partial-then-committed messages surface as ordered
  `TranscriptEvent`s; audio chunks are sent base64-encoded inside
  `input_audio_chunk` messages; `keywords` reach the connection URL as `keyterms`; an
  empty audio stream terminates cleanly rather than hanging; abandoning the iterator
  early closes the fake connection; a server error message raises the mapped domain
  error; and **no test asserts on logged transcript text** (CLAUDE.md §27).

- [x] **Step 5 — Factory wiring** — add `"elevenlabs"` branches to `get_stt_provider`
  and `get_tts_provider`, raising a clear, named error when `ELEVENLABS_API_KEY` is unset
  so a misconfigured deploy fails at construction rather than mid-call.
  *Done when:* `pytest` passes proving both factories return the ElevenLabs adapters for
  `"elevenlabs"` when a key is configured, raise a named configuration error when the key
  is empty, still return the mocks for `"mock"`, and still reject an unknown name; the
  default configuration continues to resolve to mocks; `ruff check apps/api` clean.

## Files / areas

**New**
- `apps/api/app/providers/elevenlabs_speech.py`
- `apps/api/tests/test_elevenlabs_speech.py`

**Modified**
- `apps/api/app/core/config.py` — adds `elevenlabs_api_key`.
- `apps/api/app/providers/factory.py` — adds the `"elevenlabs"` branches.
- `apps/api/requirements.txt` — adds `websockets`.
- `.env.example` — adds `ELEVENLABS_API_KEY`.

**Unchanged**
- `app/providers/speech.py` — 9a's contracts are the target, not something to edit. If a
  step wants to change a protocol, the adapter is wrong, not the contract.
- `app/providers/mock_speech.py`, and every existing test for it.
- Everything under `apps/voice/`, `apps/worker/`, `apps/web/`; `docker-compose.yml`; both
  Dockerfiles; no database model, migration, or route.

## Data / contracts

**No new public contract.** This feature implements 9a's protocols; `TranscriptEvent`,
`Voice`, the error hierarchy, and the canonical audio format are all already locked. Three
implementation-level seams are worth stating because the tests depend on them:

**1. Both adapters take an injectable transport.** `ElevenLabsTTS` accepts an optional
`httpx.AsyncClient`; `ElevenLabsSTT` accepts an optional `connect` callable defaulting to
`websockets.connect`. This is the entire testing strategy — it is what lets every test run
with zero network and zero spend. Do not replace it with module-level patching.

**2. Error mapping is fixed.**

| Condition | Raised |
|---|---|
| HTTP 401 / 403, `auth_error` | `SpeechProviderUnavailable` |
| HTTP 429, `rate_limited`, `quota_exceeded` | `SpeechProviderUnavailable` |
| HTTP 5xx | `SpeechProviderUnavailable` |
| Connect or read timeout | `SpeechProviderTimeout` |
| `transcriber_error`, other vendor `error` | `SpeechProviderError` |
| Unrecognized `message_type` | ignored (returns `None`) |

An unrecognized message type is deliberately **not** an error: ElevenLabs adding a new
message type must not drop a live call.

**3. A missing API key fails at construction, not mid-call.** Step 5's factory raises when
`ELEVENLABS_API_KEY` is empty. Discovering a missing key while a caller is on the line is
exactly the silent-failure mode CLAUDE.md §9 forbids.

## Testing

The backend gate is live — every step ships its tests in the same diff.

**In-scope logic needing tests:** the realtime message mapper (pure, Step 3), voice
mapping and pagination (Step 2), HTTP and vendor error mapping, adapter streaming
behavior against stubs, and factory resolution. All assertable without a network.

**Test file:** `apps/api/tests/test_elevenlabs_speech.py`, alongside 9a's
`test_speech_providers.py`, matching the one-file-per-concern convention.

**Stubbing approach:** `httpx.MockTransport` for both HTTP endpoints; a small
locally-defined fake connection object (async context manager, `send`, `__aiter__`,
`close`) for the WebSocket. No new test dependency.

**Optional live smoke test, never a gate:** with a real Pro-tier `ELEVENLABS_API_KEY` set
and `TTS_PROVIDER=elevenlabs`, constructing `ElevenLabsTTS` and synthesizing a short
phrase should stream PCM bytes. This is manual, costs money, and is explicitly not part
of any done-when.

## Notes for the AI

- **The vendor API shapes above were verified from live documentation.** Follow them
  rather than recalling a different ElevenLabs API. Two details are easy to get wrong:
  audio goes as **base64 inside a JSON message**, not as binary frames, and voices are on
  **`/v2/voices`** while everything else is `/v1`.
- **One detail to confirm at build time:** how `keyterms` is encoded in the WebSocket
  connection URL when there are several — repeated query parameters versus one delimited
  value. Check the docs when writing Step 4; do not guess and do not silently drop the
  parameter if it is awkward, since it is item 13's entire glossary-biasing mechanism.
- **STT needs genuine concurrency.** Sending audio and receiving transcripts must run at
  the same time; a send-all-then-receive-all implementation would defeat the streaming
  contract and the latency budget. Use a task pair (e.g. `asyncio.TaskGroup`) and make
  sure abandoning the iterator cancels both cleanly.
- **Never log transcript text** (CLAUDE.md §27), and never log the API key. Log a message
  count or an error type if anything at all.
- **Timeout every external call** (CLAUDE.md §41). Both an HTTP timeout and a websocket
  receive bound; a hung vendor must not hang the call.
- **Do not edit 9a's protocols.** They are the specification this feature is measured
  against. If an adapter cannot satisfy one, stop and raise it rather than loosening the
  contract.
- No database, migration, route, or frontend change. If a step seems to need one, it has
  drifted.
