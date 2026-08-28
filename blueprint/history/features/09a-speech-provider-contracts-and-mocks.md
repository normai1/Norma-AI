# Feature: Speech provider contracts and mocks

**From build-plan:** feature 9a
**Status:** not started

## Goal

Define the two speech interfaces the entire voice pipeline will be built against, and ship
deterministic mocks good enough to drive real pipeline tests. This is the contract feature
for items 10, 13, 20b, 20e, 20g, and 22 — every one of them codes against these shapes, so
getting them right here is worth more than any code this feature actually executes.

No network calls, no new dependencies, no API key. The real ElevenLabs adapters are 9b.

## Design reference

None. No UI ships in this feature.

## In scope

- `apps/api/app/providers/` — new package: `speech.py` (interfaces, value types, error
  hierarchy, canonical audio format) and `mock_speech.py` (`MockSTT`, `MockTTS`).
- Config: `stt_provider` / `tts_provider` settings, both defaulting to `"mock"`.
- A small factory resolving the configured name to an instance, with a clear error on an
  unknown name.
- `.env.example`: `STT_PROVIDER`, `TTS_PROVIDER`.
- pytest coverage for mock determinism, event sequencing, cancellation, failure injection,
  and factory resolution.

## Out of scope

- **`ElevenLabsSTT` / `ElevenLabsTTS`.** That is 9b. If a step in this feature reaches for
  `httpx`, a websocket library, or an API key, it has drifted out of scope — stop.
- **Moving providers into a shared `packages/` location.** CLAUDE.md §4 requires provider
  abstractions *used by both* `apps/api` and `apps/voice` to live somewhere shared. Nothing
  in `apps/voice` consumes these yet, and nothing is duplicated, so §4 is not yet violated.
  Doing the move now would mean changing both Docker build contexts from `./apps/api` and
  `./apps/voice` to the repo root, rewriting both Dockerfiles' `COPY` paths, adding bind
  mounts, and re-validating `pytest.ini`'s `pythonpath` — dev-environment surgery that would
  dominate this feature and risk the working stack from item 5. **Item 20a must do that
  extraction** when `apps/voice` becomes a real second consumer and the change can be
  validated by actual use. Recorded here so it is not forgotten.
- **Real glossary/keyword data.** The STT interface accepts keyword hints; populating them
  from `GlossaryEntry` rows is item 13.
- **Telephony audio transcoding.** Providers speak the canonical internal format below;
  converting to and from the carrier's μ-law/8 kHz is item 23's edge concern.
- **Turn detection, barge-in orchestration, latency instrumentation.** Items 20c, 20e, 20f.
  This feature only ensures the interfaces make them *possible* (cancellable streams,
  partial transcripts, injectable delays).

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 — STT contract and `MockSTT`** — create `app/providers/speech.py` with the
  canonical audio format constants, `TranscriptEvent`, the `SpeechProviderError` hierarchy,
  and the `SpeechToTextProvider` protocol; create `app/providers/mock_speech.py` with
  `MockSTT`, driven by a caller-supplied script of transcript events and supporting an
  injected failure and an injected per-event delay.
  *Done when:* `pytest` passes with tests proving: a scripted `MockSTT` yields exactly the
  configured partial-then-final sequence in order; an empty audio stream still terminates
  cleanly rather than hanging; closing the returned async generator early stops it without
  raising; an injected `SpeechProviderError` surfaces to the caller rather than being
  swallowed; and no test asserts on logged transcript text (CLAUDE.md §27 forbids logging it).

- [x] **Step 2 — TTS contract and `MockTTS`** — add `Voice` and the `TextToSpeechProvider`
  protocol to `speech.py`; add `MockTTS` to `mock_speech.py`, emitting deterministic silent
  PCM whose byte length is proportional to the input text, with a configurable
  time-to-first-byte delay, a configurable voice list, an injected failure, and a record of
  whether the stream was cancelled.
  *Done when:* `pytest` passes with tests proving: synthesizing the same text twice yields
  byte-identical output; a longer text yields proportionally more audio; empty text yields an
  empty stream rather than an error; `list_voices()` returns the configured catalogue;
  abandoning the generator mid-stream marks the mock cancelled (the property item 20e's
  barge-in tests will assert on); and an injected failure surfaces as `SpeechProviderError`.

- [x] **Step 3 — Provider selection and configuration** — add `stt_provider` / `tts_provider`
  to `Settings` (both defaulting to `"mock"`), a factory resolving each name to an instance,
  and the two variables to `.env.example`.
  *Done when:* `pytest` passes with tests proving the factory returns `MockSTT`/`MockTTS` for
  `"mock"`, raises a clear, named error (not a `KeyError`) for an unrecognized provider name,
  and that the default settings resolve to the mocks with no environment configuration at all
  — so the test suite can never accidentally reach a paid provider; `ruff check apps/api`
  clean.

## Files / areas

**New**
- `apps/api/app/providers/__init__.py`
- `apps/api/app/providers/speech.py`
- `apps/api/app/providers/mock_speech.py`
- `apps/api/tests/test_speech_providers.py`

**Modified**
- `apps/api/app/core/config.py` — adds `stt_provider`, `tts_provider`.
- `.env.example` — adds `STT_PROVIDER`, `TTS_PROVIDER`.

**Unchanged**
- Everything under `apps/voice/`, `apps/worker/`, `apps/web/`.
- `docker-compose.yml` and both Dockerfiles — see Out of scope.
- No database model, schema, migration, or route.

## Data / contracts

Everything here is load-bearing. Items 10, 13, 20b, 20e, 20g, and 22 code against these.

**1. Canonical internal audio format, locked.**

```text
16 kHz, 16-bit signed little-endian PCM, mono
```

Every provider in this codebase speaks this format at the interface boundary. Carrier audio
(commonly 8 kHz μ-law) is transcoded at the telephony edge in item 23, never inside a speech
provider. Expressed as named constants, not magic numbers scattered across adapters.

**2. STT is streaming and yields partials.**

```python
@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    is_final: bool
    confidence: float | None = None

class SpeechToTextProvider(Protocol):
    def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keywords: Sequence[str] = (),
    ) -> AsyncIterator[TranscriptEvent]: ...
```

Partial events are not optional decoration — item 20c's turn detection and item 20e's
barge-in both need transcripts *before* the caller stops speaking. `keywords` is the glossary
biasing hook item 13 fills.

**3. TTS is streaming, cancellable, and lists voices.**

```python
@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str
    gender: str | None = None

class TextToSpeechProvider(Protocol):
    def synthesize(
        self, text: str, *, voice_id: str, speed: float = 1.0
    ) -> AsyncIterator[bytes]: ...

    async def list_voices(self) -> Sequence[Voice]: ...
```

Streaming chunks is what lets playback start before synthesis finishes (CLAUDE.md §9).
**Cancellation is the barge-in mechanism**: closing the async generator must stop synthesis
promptly, and the 200 ms barge-in budget depends on it. `list_voices()` exists for item 10's
voice catalogue.

**4. Error hierarchy.**

```python
class SpeechProviderError(Exception): ...          # base
class SpeechProviderTimeout(SpeechProviderError): ...
class SpeechProviderUnavailable(SpeechProviderError): ...
```

Callers distinguish "the provider failed" (→ fall back to forwarding or message-taking, per
the single-vendor decision in `project-overview.md` § Speech) from a programming bug. A bare
`Exception` from a provider is a defect.

**5. Mocks are test instruments, not stubs.** Both mocks accept injected failures and injected
delays. Item 20g must be able to test provider-timeout failover and item 20f must be able to
test the latency budget — neither is possible against a mock that only ever succeeds
instantly. This is the main reason 9a is worth doing before 9b.

**6. Default configuration resolves to mocks.** `stt_provider` and `tts_provider` both default
to `"mock"`, so a test run with no environment configuration cannot reach a paid API
(CLAUDE.md §28).

## Testing

The backend gate is live — every step ships its tests in the same diff.

**In-scope logic needing tests:** mock determinism and event sequencing, empty-input handling,
cancellation, failure injection, and factory resolution. All pure async logic with assertable
inputs and outputs — no network, no database, no fixtures beyond what the tests construct.

**Test file:** `apps/api/tests/test_speech_providers.py`, covering both mocks and the factory,
matching the existing one-file-per-concern convention (`test_auth_profile.py` similarly covers
profile and password together).

**Not tested here:** any real vendor behavior — that is 9b, against a stubbed transport.

**Manual path:** none meaningful; this feature ships no route and no UI. Verification is the
test suite.

## Notes for the AI

- **No network. At all.** If a step imports `httpx`, `websockets`, or reads an API key, it has
  drifted into 9b. Stop and re-read the scope.
- **`Protocol`, not ABC.** Match the structural-typing style; providers are duck-typed and
  swapped by configuration, and `Protocol` keeps the mocks from needing to inherit anything.
  Mark them `@runtime_checkable` only if a test actually needs `isinstance`.
- **`stream()` and `synthesize()` return async iterators; they are not `async def` coroutines
  returning one.** Getting this wrong makes cancellation awkward and barge-in unreliable. The
  mock implementations should be `async def ... yield` generators.
- **Never log transcript text**, even in a mock (CLAUDE.md §27). Log a call id or event count
  if anything at all.
- **Timeouts belong to the adapter, not the protocol body.** The protocol expresses the shape;
  9b's real adapters bound every external call. Do not bake an `asyncio.wait_for` into the
  interface definition.
- **Do not invent a `SpeechProvider` super-interface** unifying STT and TTS. They share error
  types and audio format, nothing else; a common base would be abstraction for its own sake
  (CLAUDE.md §29).
- No model, migration, route, or frontend change. If a step seems to need one, it has drifted.
