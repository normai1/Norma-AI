# Feature: Framework selection and media transport

**From build-plan:** feature 20a

**Status:** not started

## Goal

Item 20 ("the real-time voice session engine") is already split into sub-items 20a-20g in
`build-plan.md`, and CLAUDE.md section 42 flags it as "the highest-risk item in the project,"
explicitly prescribing a spike of 20a first since every later sub-item (20b streaming STT, 20c
turn detection, 20d LLM turn loop, 20e streaming TTS/barge-in, 20f latency instrumentation, 20g
session resilience) depends on which realtime framework this one decides. This feature does
exactly what its own build-plan line says: **evaluate LiveKit Agents against Pipecat, record the
decision, and establish bidirectional streaming audio behind Norma's own interfaces** - nothing
from 20b onward.

## Design reference

None. Backend/infrastructure-only.

## Framework decision: Pipecat

Researched against the actual current state of both projects (not guessed):

- **No extra infrastructure tier for a phone-only product.** LiveKit Agents requires a
  self-hosted LiveKit Server (an SFU) as a separate service - its own ECS/Fly service, host
  networking for RTP ports, a TURN server, its own domain+TLS - even though Norma's callers
  arrive via Twilio, not WebRTC browser clients. Pipecat runs as a plain Python process; a
  Twilio media-stream WebSocket connects straight into the pipeline. For "long-lived Python
  worker, not serverless," Pipecat is the leaner footprint.
- **Native Twilio fit, not a bridge.** LiveKit's telephony story is "SIP trunk -> LiveKit room ->
  agent" - a call is bridged into its WebRTC/room abstraction. Pipecat treats Twilio as a
  first-class transport directly (a real, maintained Twilio quickstart exists) - closer to
  Norma's actual shape, and confirmed in the installed package itself: `pipecat.serializers.twilio`
  ships as a real, first-class module, alongside Telnyx/Plivo/Vonage/Genesys serializers -
  telephony providers this project may reach for later (item 23 names Telnyx as the EU
  alternative).
- **Weaker vendor lock-in, matching "Norma's own interfaces."** Pipecat is explicitly
  composable/vendor-neutral - swapping STT/LLM/TTS providers is a small, local change - and
  already supports arbitrary transports (Daily, WebSockets, Twilio, a custom phone system).
  LiveKit's plugin model still assumes LiveKit's own room/track concepts underneath.
- **License and maturity are both fine, not differentiators.** Pipecat is BSD-2, LiveKit Agents
  is Apache-2.0 - both permissive. GitHub activity is close between the two (treat maturity as a
  wash, not a deciding factor).

**Biggest risk of this choice:** Pipecat hands Norma more of the operational burden itself - no
managed SFU means the voice worker's own code must handle reconnects, scaling, and resilience
that LiveKit would have partly absorbed. Mitigation: this is exactly what build-plan items 20f
(latency instrumentation) and 20g (session resilience) already exist to build - not assumed away
by the framework choice.

**Verified installable and current**: `pipecat-ai` 1.8.1 on PyPI, real `pipecat.transports.
websocket.fastapi.FastAPIWebsocketTransport` (FastAPI-native, no bridge), a real `pipecat.
pipeline.Pipeline`/`pipecat.pipeline.runner.PipelineRunner`. Confirmed by downloading and
inspecting the actual wheel, not assumed from memory or older docs (its own `pipeline/task.py`
is a deprecated shim as of 1.3.0, pointing at `pipecat.pipeline.worker` instead - the kind of
drift that would have produced wrong code if guessed rather than checked).

## In scope

- **Record the decision.** `blueprint/project-plan.md` §5's Media plane line currently says the
  decision is "selected during item 17" - a stale reference from before this project's
  renumbering (already flagged in `project-overview.md`'s own Open Questions #1). Fix the
  reference and record Pipecat as the chosen framework with the reasoning above.
  `project-overview.md`'s "Realtime framework" row and its Open Questions #1 entry get the same
  update, closing that open question.
- **`apps/voice` gets its first test infrastructure.** Confirmed empty today - no `tests/`
  directory, no `pytest`/`httpx` in its `requirements.txt`. This is exactly the trigger condition
  `findings.md`'s F-28 already named ("When `apps/voice` gets its first piece of real logic...
  set up `pytest` + `httpx` for it... and add a smoke test for `/health` at the same time") -
  resolve F-28 as part of this build. `AGENTS.md`'s "Media plane (`apps/voice`)" section (which
  currently says "No standalone local dev command yet") gets a real documented test command.
- **`pipecat-ai[websocket]` added to `apps/voice/requirements.txt`.**
- **A Norma-side media session wrapper** (exact shape decided during `/implement`, this is
  explicitly spike work per CLAUDE.md section 42 - expect it to be refined once 20b-20g add
  real pipeline stages) - a thin module encapsulating Pipecat's transport/pipeline construction,
  so application code calls Norma's own function/class rather than scattering raw Pipecat calls
  through `app/main.py`. This is the concrete meaning of "behind Norma's own interfaces" for
  this step: if the framework were ever swapped, this module is what would change.
- **A real, provable bidirectional audio stream** - a WebSocket endpoint that accepts an inbound
  connection, wires it into an actual Pipecat `Pipeline` via the FastAPI websocket transport, and
  streams audio back out through it (an echo pipeline - input frames flow out unchanged). This
  is deliberately the simplest pipeline that proves the plumbing genuinely works end to end;
  it is not yet connected to STT, an LLM, or TTS.
- **An automated test proving it**, using Starlette's `TestClient.websocket_connect()` (the
  correct tool for testing a FastAPI WebSocket route - `httpx` alone does not perform the
  WebSocket upgrade) - send audio bytes in, assert they arrive back out, having actually passed
  through the real Pipecat pipeline object, not a hand-rolled bypass.

## Out of scope

- **Streaming STT, turn detection, the LLM turn loop, streaming TTS, barge-in.** Items
  20b/20c/20d/20e - this feature's pipeline is an echo, not a conversation.
- **Latency instrumentation and `TurnMetric` writes.** Item 20f - there is no per-turn timing to
  measure yet without a real conversational pipeline.
- **Session resilience (provider timeouts/retries/failover, crash-to-forwarding).** Item 20g.
- **Real telephony ingress (Twilio/SIP media).** Item 23+ - no telephony provider exists yet.
  Today's proof is a raw WebSocket test client, not a phone call.
- **The in-browser test call UI.** Item 21 - a *tested* WebSocket pipeline exists after this
  feature, but no browser-facing page or audio-capture UI is built here.
- **CI wiring for the new `apps/voice` test command.** No `Verify` command or GitHub Actions
  workflow exists yet anywhere in the repo (item 59); this feature only makes the command real
  and documented, matching how `apps/api`'s own test command predates its own CI wiring.
- **Draining, backpressure, and unbounded-queue concerns for a live call load.** CLAUDE.md
  section 5.5's media-plane rules matter once 20b-20g add real throughput; an echo pipeline
  handling one test connection at a time does not yet exercise them meaningfully.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - record the decision** - `blueprint/project-plan.md` §5 (fix the stale item-17
  reference, record Pipecat + the reasoning above), `blueprint/context/project-overview.md`
  (update the "Realtime framework" row, resolve Open Questions #1). No code.
  *Done when:* both files read correctly and consistently; no other line in either file still
  says "selected during item 17" or presents the choice as undecided.

- [x] **Step 2 - `apps/voice` test infrastructure** - `apps/voice/requirements.txt` gets `pytest`
  and `httpx`; `apps/voice/tests/test_health.py` (new) smoke-tests the existing `/health`
  endpoint; `AGENTS.md`'s "Media plane (`apps/voice`)" section documents the real command; mark
  `findings.md`'s F-28 `fixed`.
  *Done when:* the new test passes, run the way `AGENTS.md` now documents. `ruff check
  apps/voice` clean (confirm `ruff` is available/configured for `apps/voice` the same way it
  already is for `apps/api`; if it is not yet, add the minimal config needed, matching
  `apps/api`'s existing `ruff` setup).

- [x] **Step 3 - Pipecat dependency, media session wrapper, echo pipeline, and its test** -
  `apps/voice/requirements.txt` gets `pipecat-ai[websocket]`; a new module under `apps/voice/app/`
  (exact name decided during `/implement`) wraps Pipecat's `Pipeline`/`PipelineRunner`/
  `FastAPIWebsocketTransport` construction behind a small Norma-side function or class;
  `apps/voice/app/main.py` gets a new WebSocket route running that pipeline; a new test uses
  `TestClient.websocket_connect()` to send audio bytes and assert they come back out through the
  real pipeline.
  *Done when:* the new test passes and demonstrably exercises the real installed `pipecat-ai`
  package (not a stub) - confirm by an assertion or comment tying the test to the actual
  `Pipeline`/`PipelineRunner` objects involved. `ruff check apps/voice` clean. `docker compose
  build voice && docker compose up -d voice` succeeds and `curl http://localhost:8080/health`
  still returns `200`.

## Files / areas

**New**
- `apps/voice/tests/test_health.py`
- `apps/voice/tests/test_media_session.py` (or equivalent name, decided during `/implement`)
- A new `apps/voice/app/` module for the Norma-side media session wrapper.
- Possibly `apps/voice/pytest.ini` or `apps/voice/pyproject.toml` `[tool.pytest.ini_options]`, if
  needed for test discovery independent of the root `pytest.ini` (which is scoped to `apps/api`).
- Possibly `apps/voice/ruff.toml` / a `[tool.ruff]` section, if `apps/voice` does not already
  inherit config the way `apps/api` does - check before assuming.

**Modified**
- `apps/voice/requirements.txt` (`pytest`, `httpx`, `pipecat-ai[websocket]`)
- `apps/voice/app/main.py` (new WebSocket route)
- `blueprint/project-plan.md`, `blueprint/context/project-overview.md` (decision recorded)
- `AGENTS.md` (Media plane test command documented)
- `blueprint/context/findings.md` (F-28 -> `fixed`)

**Unchanged**
- `apps/api`, `apps/web`, `apps/worker` - untouched. No telephony, no STT/LLM/TTS wiring, no
  database or Redis involvement in `apps/voice` yet.

## Data / contracts

None locked as permanent. The media session wrapper's exact shape is explicitly provisional -
see the Notes for the AI below - and is expected to be revisited once 20b-20g add real pipeline
stages, not treated as a load-bearing contract the way `Chunk`/`RetrievedChunk` were in items
17-19.

## Testing

`apps/voice` has no test command today - this feature creates one. Both new tests are
appropriate for the unit/integration tier `coding-standards.md` describes: a health-endpoint
smoke test (matching every other health endpoint in the repo) and a real, non-mocked exercise of
the Pipecat pipeline object (not a network-dependent test - Pipecat itself makes no external
calls for a plain echo pipeline, so no provider mocking is needed here, unlike `apps/api`'s
provider-abstraction tests).

## Notes for the AI

- **This is spike work, by CLAUDE.md's own explicit instruction** (section 42): "expect the
  spike to change recorded decisions in both plans." Don't over-engineer the media session
  wrapper's interface into a permanent-feeling abstraction; keep it small and honest about being
  provisional, and say so in its own docstring.
- **Verify Pipecat's actual current API empirically during `/implement`**, the same way this
  spec's own research did (download/inspect the installed package, don't assume from older
  tutorials or memory) - `pipecat.pipeline.task.PipelineTask` is a deprecated shim as of 1.3.0;
  `pipecat.pipeline.worker` is current. Expect other specifics to need the same real-time check.
- **`apps/voice` is a separate Python environment from `apps/api`** - its own
  `requirements.txt`, its own container, its own dependency install. A new dependency needs
  `docker compose build voice` (not `api`) to reach the running container. **Unlike `apps/api`,
  install `apps/voice`'s dependencies into a local `.venv` scoped to `apps/voice`, never the
  shared host Python.** Discovered mid-build: installing `pipecat-ai[websocket]` into the
  pre-existing repo-level `D:\NormaAI\.venv` (what `python`/`pip` resolve to by default in this
  environment - not literally the OS-wide global Python, but a shared venv also used for
  unrelated tooling) downgraded packages that other, unrelated tools depend on (`crewai`, `mcp`,
  `sagemaker-core`, `openrouter`) and collaterally uninstalled `apps/api`'s own `pgvector`/
  `pypdf`/`python-docx`, breaking `apps/api`'s test suite until reinstalled. Its dependency tree
  is much heavier than `apps/api`'s own additions that made the shared-venv install convention
  seem safe. `AGENTS.md`'s Media plane section documents `apps/voice`'s own isolated venv setup
  and test command - a second, narrower venv specifically for `apps/voice`, not a replacement
  for the existing shared one `apps/api` continues to use.
- **Confirm `ruff` reaches `apps/voice` before assuming it already does.** `apps/api`'s `ruff`
  invocations in this session were always scoped to `apps/api` paths; check whether `apps/voice`
  is covered by the same root config or needs its own before treating a clean `ruff check
  apps/voice` as meaningful.
- **F-28 resolution belongs in Step 2**, the step that actually adds the test infrastructure and
  the `/health` smoke test it named - not a separate action.
- A push, if any, at the end of this feature still needs your explicit go-ahead per the
  standing project convention - **except this run, where the user has explicitly waived that
  and asked for the recommendation to be carried out directly.**
