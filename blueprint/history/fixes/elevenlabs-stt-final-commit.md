# Fix: ElevenLabs realtime STT never commits a transcript

**Type:** Fix

## The problem

`ElevenLabsSTT.stream()` (`apps/api/app/providers/elevenlabs_speech.py`) cannot produce a
final transcript on a real call. `_send_audio_chunks` sends every `input_audio_chunk`
message with `"commit": false`, then calls `await connection.close()` immediately once the
caller's audio iterator is exhausted. The connection is opened with `commit_strategy=vad`,
which relies entirely on ElevenLabs' server-side VAD (1.5s of trailing silence) to decide
when to commit and emit `committed_transcript`. Closing the socket immediately after the
last real audio chunk races that VAD window every time - the server never gets 1.5s of
post-speech silence to observe before the connection is torn down.

Confirmed live against the real ElevenLabs API (`ELEVENLABS_API_KEY` in `.env`), reproduced
three times: the adapter's exact send-then-close pattern (`commit:false` on every chunk,
`close()` right after) yields zero events - only `session_started` ever arrives. Replaying
the identical audio through the raw protocol with `"commit": true` on the final
`input_audio_chunk` message (no other change) reliably produced
`{"message_type":"committed_transcript","text":"Testing Norma AI."}` immediately.

This is not a mock/test gap - unit tests pass today because `_FakeConnection` (feature 9b's
test double) delivers scripted messages regardless of what was sent, so nothing in the
existing suite exercises real commit-triggering behavior. The bug is entirely in
`_send_audio_chunks`'s message content, not in the concurrency or error-mapping logic
feature 9b's tests already cover.

## The fix

`_send_audio_chunks` marks the **final** `input_audio_chunk` message's `commit` field
`true` instead of always `false`, before calling `connection.close()`. Every prior chunk
keeps `commit: false` exactly as today. This must not change: base64 encoding, message
ordering, the concurrent send/receive design, the empty-audio-stream-terminates-cleanly
behavior (an empty audio stream sends zero chunks and goes straight to `close()`, unchanged
since there is no "final chunk" to mark), or any of 9b's existing error-mapping tests.

`_FakeConnection` in `test_elevenlabs_speech.py` needs a way to assert on this - it already
records everything sent (`connection.sent`), so no test-double changes are needed, only a
new assertion.

## Build steps

- [x] **Mark the final audio chunk's `commit` field `true`** - in `_send_audio_chunks`,
  track whether the current chunk is the last one from the `audio` iterator (a one-item
  lookahead, since the async iterator doesn't expose length) and set `"commit"` to `True`
  only on that message; every earlier chunk keeps `"commit": False`. Add a test asserting
  the last sent message has `"commit": True` and every earlier one has `"commit": False`,
  using a multi-chunk audio stream.
  *Done when:* `pytest apps/api/tests/test_elevenlabs_speech.py` passes, including the new
  assertion and every existing test in the file (the empty-audio-stream test must still
  pass unchanged, since zero chunks means no "final chunk" to mark). `ruff check apps/api`
  clean.

  **Correction found during live verification:** marking the final chunk `commit: true`
  was necessary but not sufficient. `_send_audio_chunks` was still calling
  `connection.close()` immediately after sending, which races the server's response to
  that final commit and discards it - confirmed live, the server only closes the
  connection itself once it has finished processing and has sent its response. Fixed by
  no longer closing in `_send_audio_chunks` when any real audio was sent (the caller's
  `async with` on the connection closes it once the receive loop ends, whether via the
  server's own close or the caller abandoning the stream); an empty audio stream still
  closes immediately, since nothing was ever sent and there is no response to wait for.
  Verified end-to-end against the real API through the actual adapter (not a diagnostic
  script): `stt.stream()` on ElevenLabs-synthesized audio for "Testing Norma AI." yielded
  exactly one final `TranscriptEvent` with matching text.

- [x] **Isolate the default-provider factory test from the local `.env`** -
  `test_factory_falls_back_to_configured_settings_by_default` in `test_speech_providers.py`
  asserts `settings.stt_provider == "mock"` / `settings.tts_provider == "mock"` directly
  against the live `settings` singleton, which is loaded from the real `.env`. This fails
  whenever `.env` is pointed at a real provider (as it now is, for this fix's live
  verification), which is a test-isolation flaw, not a code defect: the test should prove
  "no explicit name given falls back to whatever is configured," not hardcode a literal.
  Rewrite it with `monkeypatch.setattr(settings, "stt_provider", "mock")` /
  `tts_provider`, matching the pattern the new `elevenlabs` factory tests already use in
  the same file.
  *Done when:* `pytest` (full suite) passes regardless of what `.env`'s `STT_PROVIDER`/
  `TTS_PROVIDER` are set to. `ruff check apps/api` clean.

- [x] **Repair F-39 - map connection-level transport failures in both ElevenLabs
  adapters, not just timeouts.** Both adapters currently catch only the timeout branch
  of their transport (`httpx.TimeoutException`, `TimeoutError` from `open_timeout`).
  `httpx.ConnectError`/`NetworkError` (connection refused, DNS failure) sit under
  `httpx.TransportError` as a sibling of `TimeoutException`, not beneath it, and
  `websockets.exceptions.ConnectionClosedError`/`InvalidStatus` both derive from
  `WebSocketException`, never from `TimeoutError` - none of these are caught anywhere,
  so they escape as raw vendor exceptions instead of `SpeechProviderUnavailable`,
  contradicting that class's own docstring ("the connection could not be established")
  and this feature's locked error-mapping table. Widen `ElevenLabsTTS.synthesize`/
  `list_voices` to catch `httpx.TransportError` (keeping the existing
  `SpeechProviderTimeout` branch specific to `isinstance(exc, httpx.TimeoutException)`,
  mapping everything else in that except to `SpeechProviderUnavailable`), and widen
  `ElevenLabsSTT.stream` to also catch `websockets.exceptions.WebSocketException`
  alongside the existing `TimeoutError`, mapping to `SpeechProviderUnavailable`.
  *Done when:* `pytest` passes with new tests proving a non-timeout transport failure -
  `httpx.ConnectError` for both TTS methods, `websockets.exceptions.ConnectionClosedError`
  and `InvalidStatus` for STT - each raises `SpeechProviderUnavailable`, not the raw
  vendor exception; every existing test in `test_elevenlabs_speech.py` still passes
  unchanged. `ruff check apps/api` clean.

## Verify

Automated: the new pytest assertion above.

Manual (optional, costs money, never a gate - same convention as 9b's own optional smoke
test): with the real `ELEVENLABS_API_KEY` already in `.env` and `STT_PROVIDER=elevenlabs`,
synthesize a short phrase with `ElevenLabsTTS` and feed the resulting audio into
`ElevenLabsSTT.stream()`; a `committed_transcript` matching the synthesized text should now
arrive, matching the live verification already done in this session.

## Findings

### fix/elevenlabs-stt-final-commit/F-39 [P1] closed - ElevenLabs adapters only map timeout failures; connection-level failures escape as raw vendor-library exceptions, violating the feature's own error-mapping contract

**File:** apps/api/app/providers/elevenlabs_speech.py:162-185, 202-212, 322-348
**Found:** 2026-08-28 by /audit (scope: build-plan item 9; lens: all)
**Why it matters:** Both adapters catch only the timeout branch of their transport's
exception hierarchy - `httpx.TimeoutException` in `ElevenLabsTTS.synthesize`/`list_voices`,
`TimeoutError` (from `open_timeout`) in `ElevenLabsSTT.stream`. Confirmed by inspecting the
actually-installed dependency versions (httpx, websockets 17.0.1) that the other failure
modes each transport documents are separate, unrelated exception branches, not timeout
subclasses: `httpx.ConnectError`/`NetworkError` (DNS failure, connection refused) sit under
`httpx.TransportError` as a sibling of `TimeoutException`, not beneath it, and
`websockets.exceptions.ConnectionClosedError` (peer drops mid-stream) and `InvalidStatus`
(handshake rejected, e.g. a bad API key returning 401 before any `auth_error` JSON message
is ever sent) both derive from `WebSocketException`, never from `TimeoutError`. None of
these are caught anywhere in `elevenlabs_speech.py`, so they propagate as raw vendor
exceptions instead of `SpeechProviderUnavailable` - directly contradicting
`SpeechProviderUnavailable`'s own docstring in `speech.py` ("the connection could not be
established") and the 9b spec's locked "Error mapping is fixed" table, which claims
exhaustive coverage of exactly these cases. This also means `_send_audio_chunks`'s
background task can raise an unmapped `ConnectionClosedError` from `connection.send()`,
which then surfaces (unmapped) when `ElevenLabsSTT.stream`'s `finally` block awaits it. Any
caller written against the documented contract - catch `SpeechProviderError` and fall back
to forwarding or message-taking (CLAUDE.md section 9, "provider failure never produces
silence") - will not catch a dropped connection or a rejected handshake, and an uncaught
exception on a live call is exactly the silence CLAUDE.md forbids. No test in
`test_elevenlabs_speech.py` exercises a transport failure of any kind (the `_FakeConnection`
test double never raises), so nothing currently catches this class of defect.
**Suggested fix:** Wrap `ElevenLabsTTS`'s two request paths in `except httpx.TransportError`
(a superclass of both `TimeoutException` and `ConnectError`) instead of only
`TimeoutException`, keeping the existing `SpeechProviderTimeout` branch for
`isinstance(exc, httpx.TimeoutException)` and mapping everything else in that branch to
`SpeechProviderUnavailable`. Do the equivalent in `ElevenLabsSTT.stream`: catch
`websockets.exceptions.WebSocketException` (covers `InvalidStatus` and `ConnectionClosed`)
alongside the existing `TimeoutError`, mapping to `SpeechProviderUnavailable`. Add a test per
adapter proving a dropped/rejected connection raises `SpeechProviderUnavailable`, not the raw
vendor exception - `_FakeConnection` needs a way to raise from `__anext__`/`send` to exercise
this.
**Resolution:** Fixed, during the fix/elevenlabs-stt-final-commit /implement session.
`ElevenLabsTTS.synthesize`/`list_voices` now also catch `httpx.TransportError` (mapped to
`SpeechProviderUnavailable`, with the existing `httpx.TimeoutException` branch kept first and
still specific to `SpeechProviderTimeout`), and `ElevenLabsSTT.stream` now also catches
`websockets.exceptions.WebSocketException` alongside the existing `TimeoutError`. Four new tests
added: `httpx.ConnectError` for both TTS methods, a rejected-handshake `WebSocketException` from
the `connect` callable, and a mid-stream `ConnectionClosedError` from `_FakeConnection` (extended
with a `raise_after` parameter to make this and future transport-failure tests possible) - all four
assert `SpeechProviderUnavailable`. Full test file (36/36) and full backend suite (293/293) pass;
`ruff check apps/api` clean. Re-reviewed 2026-08-28 (scope: current, fix/elevenlabs-stt-final-commit;
lens: all): both new except clauses correctly widen to the transport's actual base failure class
(`httpx.TransportError`, `websockets.exceptions.WebSocketException`) with the existing timeout
branches kept first and specific, matching Python's except-clause ordering rules; the four new
tests each independently exercise a distinct real failure mode (`ConnectError` x2,
rejected handshake, mid-stream `ConnectionClosedError`) and all pass. The repair introduces no
new defect of its own - the receive-loop hang risk recorded as F-41 comes from this branch's
separate close-timing fix (F-39's sibling bug), not from this exception-mapping repair. Closed.

### fix/elevenlabs-stt-final-commit/F-41 [P1] accepted - `ElevenLabsSTT.stream()`'s receive loop has no timeout; a vendor connection that never closes now hangs the call indefinitely

**File:** apps/api/app/providers/elevenlabs_speech.py:329-345 (the `async for raw_message in
connection:` loop inside `stream()`)
**Found:** 2026-08-28 by /audit (scope: current, fix/elevenlabs-stt-final-commit; lens: all)
**Why it matters:** Fixing F-39's sibling bug (the never-committing transcript, fixed on this
branch) required `_send_audio_chunks` to stop force-closing the connection after real audio -
closing was what raced and discarded the server's response. The connection is now closed only
by the server itself (once it finishes processing the final commit), by an error, or by the
caller abandoning the generator. That is correct for the common case, but the receive loop -
`async for raw_message in connection:` - has no timeout anywhere: if the server never sends
another message and never closes (a vendor bug, a network partition after the final chunk was
sent, a stalled session), the loop, and therefore the call, hangs forever. This is a real
behavior change on this branch, not a pre-existing inert gap: before this fix, `_send_audio_chunks`
force-closed the connection immediately after sending regardless of server behavior, which
(as a side effect of the bug F-39's sibling fixed) also happened to bound the receive loop's
lifetime. Removing that close to fix the transcript bug removes that accidental bound too.
This directly contradicts an explicit written requirement: CLAUDE.md section 41 ("Timeout
every external call ... a hung vendor must not hang the call") and this feature's own 9b spec
Notes for the AI ("Both an HTTP timeout and a websocket receive bound; a hung vendor must not
hang the call") - neither `ElevenLabsTTS` nor `ElevenLabsSTT` has ever had a receive-side bound;
`open_timeout_seconds` only bounds the initial handshake. No test exercises a connection that
simply stops sending without closing, so nothing currently catches this.
**Suggested fix:** Give `ElevenLabsSTT.stream()` a bounded receive - e.g. a
`receive_timeout_seconds` parameter (mirroring `open_timeout_seconds`) wrapping each
`connection.__anext__()` in `asyncio.wait_for`, resetting per received message (an idle
timeout, not a call-duration timeout, since a real call can legitimately run long), mapping
the timeout to `SpeechProviderTimeout`. Worth its own reviewed fix rather than folding into
this branch - it is a real design decision (idle-timeout duration, whether it resets on
partial vs. only committed messages), not a one-line change.
**Resolution:** Accepted by the user 2026-08-28: deferred to its own follow-up `/fix` rather
than folding into fix/elevenlabs-stt-final-commit, matching the audit's own recommendation.
Not fixed on this branch.
