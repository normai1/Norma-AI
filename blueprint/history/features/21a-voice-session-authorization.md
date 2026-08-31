# Feature: Voice session authorization

**From build-plan:** feature 21a

**Status:** complete

## Goal

Close a real, currently-live security gap before any browser client is allowed to reach the
voice pipeline: `apps/voice`'s `/media/session` WebSocket route accepts any caller supplying a
bare `assistant_id` query parameter, with no authentication or workspace-membership check at
all - directly violating CLAUDE.md section 7's explicit requirement: "Media-plane session
establishment - a browser test call must prove workspace access before audio flows." This
feature makes that proof happen: `apps/api` issues a short-lived, single-purpose ticket only to
a signed-in user with real access to the assistant's workspace, and `apps/voice` accepts a
connection only with a valid ticket, deriving the assistant identity from the ticket itself -
never from anything the client claims separately. This is backend-only; 21b builds the browser
UI that actually requests and uses this ticket.

## Design reference

None. Backend-only.

## Architecture decisions (read before building)

- **The ticket, not a bare `assistant_id`, is the sole source of truth for which assistant a
  session is for.** `/media/session` currently takes `assistant_id` as a client-supplied query
  parameter, trusted outright - exactly the CLAUDE.md section 36 violation ("never trust
  client-provided organization or workspace IDs without authorization") this feature exists to
  close, extended to assistant identity specifically. The new route takes `ticket` instead;
  `assistant_id` is removed from the query string entirely and read only from the ticket's own
  claim, so a client cannot request a ticket for assistant A and then connect claiming assistant
  B.
- **The ticket is a short-lived JWT, verified by `apps/voice` itself - not a per-connection
  round-trip to `apps/api`.** `SECRET_KEY` is already a shared, cross-plane secret (CLAUDE.md's
  own env var table lists it under "Core," not API-specific; `docker-compose.yml`'s `voice`
  service already loads the full `.env` via `env_file`, so no compose change is even needed).
  Verifying locally avoids adding a network hop - and its latency - to every session's own
  establishment, consistent with CLAUDE.md section 37 ranking call latency above general
  reliability concerns. A network round-trip per connection was considered and rejected for this
  reason.
- **The ticket's encode/decode logic lives in `packages/shared`, not duplicated per plane.**
  Unlike `TTSConfig`/`LLMConfig`'s deliberate small duplication (each plane wants the same two
  fields independently, with no risk if they drift slightly), the exact claim shape and algorithm
  here *must* match byte-for-byte between issuer (`apps/api`) and verifier (`apps/voice`) for the
  ticket to ever validate - a genuine cross-plane contract, matching item 20f's `norma_shared.
  latency.percentile` precedent for the same reason.
- **The issuing endpoint reuses the existing workspace-access dependency chain unchanged,
  nested under the same URL shape every other assistant route already uses**
  (`/organizations/{organization_id}/workspaces/{workspace_id}/assistants/{assistant_id}/...`).
  `CurrentWorkspace` already resolves and checks org/workspace access exactly as CLAUDE.md
  section 7's five authorization questions require; this feature needs no new authorization
  primitive, only a new route and service function built on the existing one.
- **Rejection happens before `websocket.accept()`, not after.** A connection with a missing,
  expired, malformed, or wrong-type ticket is closed immediately (`websocket.close(code=4401,
  ...)`) without ever being accepted - cheaper than accepting and then closing, and consistent
  with `workspace_deps.py`'s own established pattern of the same information-hiding shape for
  every rejection reason (an expired ticket and a tampered one look identical to the caller).
- **This is genuinely load-bearing for 21b**: the ticket-issuing response shape
  (`{"ticket": str, "expires_in": int}`) and the WebSocket's new `?ticket=...` query contract are
  locked here and consumed as-is by 21b's browser client - not renegotiated there.
- **Every existing `apps/voice` end-to-end test currently connects with a bare `assistant_id`
  query string and will break** once the route requires a ticket instead. A new shared test
  helper generates a valid ticket for a given assistant id (reusing the same
  `packages/shared` encode function with a fixed test secret), so updating every existing test is
  a small, mechanical query-string swap, not a rewrite. New tests specifically cover the
  rejection paths a bare-assistant-id world could never express: missing ticket, expired ticket,
  wrong-type token, tampered signature.

## In scope

- **`packages/shared/norma_shared/voice_session_ticket.py`** (new) - `VOICE_SESSION_TICKET_TYPE`
  constant, `InvalidVoiceSessionTicket` exception, `create_voice_session_ticket(*, secret_key:
  str, algorithm: str, assistant_id: str, ttl_seconds: float) -> str`, `decode_voice_session_
  ticket(ticket: str, *, secret_key: str, algorithm: str) -> str` (returns the assistant id,
  raises `InvalidVoiceSessionTicket` for any invalid, expired, or wrong-type token - never a raw
  `jwt` exception, so neither caller needs to import `PyJWT` itself just to catch failures).
- **`packages/shared/pyproject.toml`** - adds `PyJWT` as a dependency (the module above imports
  it directly).
- **`apps/api/app/schemas/voice_session.py`** (new) - `VoiceSessionTicketResponse` (`ticket: str`,
  `expires_in: int`).
- **`apps/api/app/services/voice_session.py`** (new) - `issue_test_call_ticket(db, *,
  organization_id, workspace_id, assistant_id) -> tuple[str, int]`: confirms the assistant exists
  in this workspace (reusing `assistant_service.get_assistant`, propagating `AssistantNotFound`
  unchanged), then calls `create_voice_session_ticket` with `settings.secret_key`/`jwt_algorithm`
  and a short, fixed TTL.
- **`apps/api/app/api/v1/assistants.py`** - new route: `POST /organizations/{organization_id}/
  workspaces/{workspace_id}/assistants/{assistant_id}/test-call-token`, using the existing
  `CurrentWorkspace` dependency exactly like `get_assistant` above it.
- **`apps/voice/app/config.py`** - `SECRET_KEY`, `JWT_ALGORITHM` (default `"HS256"`, matching
  `apps/api`'s own default), `VOICE_SESSION_TICKET_TTL_SECONDS` is not read here - the TTL is
  `apps/api`'s own concern at issuance time, not something the verifier needs to know.
- **`apps/voice/app/main.py`** - `/media/session` takes `ticket: str` instead of `assistant_id:
  uuid.UUID`; decodes it before `websocket.accept()`, closing with a 4401 code and never
  accepting on any `InvalidVoiceSessionTicket`; the resulting assistant id feeds the rest of the
  existing setup unchanged.
- **Tests**: `packages/shared`'s ticket module gets direct unit coverage from `apps/api/tests`
  (round-trip, expired, wrong type, tampered signature, wrong secret, missing claim). `apps/api`
  gets route-level tests for the new endpoint (success for a real member; 404 for an unknown
  assistant; 401 with no auth; 404 for a cross-tenant/sibling-workspace attempt, mirroring the
  existing assistant-route test shapes exactly). A new shared test helper in
  `apps/voice/tests/conftest.py` generates a valid test ticket; every existing end-to-end test
  is updated to use it; new tests cover each rejection path end to end (missing ticket, expired,
  wrong type, tampered) proving the connection is refused without ever being accepted.

## Out of scope

- **The browser client itself** - requesting a ticket, opening the WebSocket with it, capturing
  or playing audio. Item 21b by name.
- **Rate-limiting ticket issuance or the WebSocket connection attempt itself.** CLAUDE.md section
  17 flags rate limiting "where abuse is possible" as a per-change consideration, but a
  workspace-scoped, already-authenticated, short-lived ticket for a test call is a low-abuse
  surface compared to, say, outbound calling; deferred rather than speculatively built now.
  Revisit if usage data ever suggests otherwise.
- **Refreshing or renewing a ticket mid-session.** The ticket is only ever checked once, at
  connection time; a test call's own natural duration is short enough that a fixed, short TTL
  (long enough to cover issuance-to-connection latency, not the call itself) is sufficient. If a
  future need for very long-lived sessions ever arises, that is a separate decision.
- **Real telephony call authorization** (item 23+). This is specifically the *browser test call*
  path; a real inbound call is authorized by telephony webhook signature verification instead
  (CLAUDE.md section 10.1), an entirely different mechanism already covered by that section's own
  rules.

## Build steps

- [x] **Step 1 - pure logic: the shared voice-session-ticket module**
  - `packages/shared/norma_shared/voice_session_ticket.py` (new).
  - `packages/shared/pyproject.toml`: added `PyJWT`.
  Unit tests live in `apps/api/tests/test_voice_session_ticket.py` (round-trip, wrong secret,
  expired, wrong type, tampered signature, missing assistant_id claim), mirroring
  `test_latency.py`'s existing precedent of testing `norma_shared` modules directly from
  `apps/api/tests` rather than a new `packages/shared/tests/` tree. All pass; `ruff` clean.

- [x] **Step 2 - the issuing endpoint**
  - `apps/api/app/schemas/voice_session.py` (new).
  - `apps/api/app/services/voice_session.py` (new).
  - `apps/api/app/api/v1/assistants.py`: new `POST .../test-call-token` route.
  Route-level tests added to `apps/api/tests/test_assistants.py` (success for a member, 401 with
  no auth, 404 for an unknown assistant, 404 for a sibling-workspace attempt), matching every
  other route's test shape in that file exactly. Full `apps/api` suite green; `ruff` clean.

- [x] **Step 3 - `apps/voice` verifies the ticket before accepting**
  - `apps/voice/app/config.py`: `SECRET_KEY`, `JWT_ALGORITHM`.
  - `apps/voice/app/main.py`: `/media/session` takes `ticket`, verifies before `accept()` and
    closes with code 4401 on any `InvalidVoiceSessionTicket` or a malformed UUID claim.
  Confirmed the module imports cleanly in the real `apps/voice` venv before wiring tests in Step
  4. `ruff check apps/voice` clean.

- [x] **Step 4 - update every existing end-to-end test, add rejection-path tests**
  - `apps/voice/tests/conftest.py`: `_test_ticket`/`_media_session_url` helpers, plus
    monkeypatching `app.main.SECRET_KEY`/`JWT_ALGORITHM` to fixed test values in
    `_patch_session_setup` so every test's ticket verifies without a real `SECRET_KEY` in the
    test environment.
  - `apps/voice/tests/test_media_session.py`, `test_latency_regression.py`: every
    `websocket_connect` call updated to use a valid test ticket.
  - Four new rejection tests in `test_media_session.py`: no ticket (FastAPI's own required-param
    validation rejects it before app code runs, close code 1008), expired ticket, tampered
    signature, and a well-formed token of the wrong `type` claim (all three reach `main.py`'s own
    check and close with code 4401).
  Full `apps/voice` suite (104/104) and `apps/api` suite green; `ruff check` clean on both.
  `docker compose build voice` (retried once after the first attempt was killed by the
  environment - a known infra flake, not a code issue, matching 20g's own precedent) then
  `docker compose up -d --force-recreate --no-deps voice` succeeded; `/health` returned
  `{"status":"ok","active_sessions":0,"capacity":10}` (200) with clean startup logs.

## Files / areas

**New**
- `packages/shared/norma_shared/voice_session_ticket.py`
- `apps/api/app/schemas/voice_session.py`, `app/services/voice_session.py`
- `apps/api/tests/test_voice_session_ticket.py`

**Modified**
- `packages/shared/pyproject.toml` (`PyJWT` dependency)
- `apps/api/app/api/v1/assistants.py` (new route), `tests/test_assistants.py` (new route tests)
- `apps/voice/app/config.py` (`SECRET_KEY`, `JWT_ALGORITHM`)
- `apps/voice/app/main.py` (`ticket` replaces `assistant_id` on `/media/session`)
- `apps/voice/tests/conftest.py`, `test_media_session.py`, `test_latency_regression.py`

**Unchanged**
- No frontend file - 21b's job.
- `apps/api/app/api/deps.py`, `workspace_deps.py`, `org_deps.py` - reused as-is.
- `apps/voice/requirements.txt` - the transitive `PyJWT` pull through `norma-shared`'s updated
  dependencies resolved cleanly on `pip install -e /packages/shared` in both the local venv and
  the Docker build; no explicit line needed.

## Data / contracts

**`POST .../assistants/{assistant_id}/test-call-token` response** - `{"ticket": str,
"expires_in": int}` (seconds, fixed at 60). Same auth as every other assistant-scoped route
(`CurrentWorkspace` - any real workspace member, matching `get_assistant`'s own "any workspace
member may see it" precedent).

**`/media/session` WebSocket query contract** - `?ticket=<jwt>` (was `?assistant_id=<uuid>`,
`language` unchanged). A missing ticket is rejected by FastAPI's own parameter validation before
the route body runs (close code 1008); an expired, malformed, wrong-secret, or wrong-`type`
ticket is rejected by the route itself with close code `4401` before `.accept()`.

**Ticket claims** - `{"assistant_id": str, "type": "voice_session", "iat": float, "exp": float}`.
Internal to the shared module; neither plane's application code constructs this dict directly
outside `voice_session_ticket.py`.

## Testing

Pure logic (Step 1) has full unit coverage in `apps/api/tests`. The issuing endpoint (Step 2)
mirrors the existing assistant-route test shapes exactly, success and every failure path.
`apps/voice`'s own verification (Steps 3-4) is proven end-to-end through the real WebSocket
route: the now-mandatory happy path (all 100 pre-existing tests, updated to use a ticket) plus
four new rejection-path tests. Verified empirically (not assumed) that `websocket.close()`
called before `.accept()` actually prevents the handshake from completing, surfacing to the test
client as `WebSocketDisconnect` with the given close code.

## Notes for the AI

- The tamper tests (both in `apps/api/tests/test_voice_session_ticket.py` and
  `apps/voice/tests/test_media_session.py`) flip a character five positions from the end of the
  ticket, not the very last character - flipping the last base64url character of a JWT
  occasionally leaves it still valid (redundant bits depending on the token's length), which was
  caught as a real, rare flake during implementation and fixed before merge.
- `apps/voice`'s own `ruff` must be run with `apps/voice` as the working directory (or otherwise
  scoped so `app` resolves as first-party) - running it from the repo root against the
  `apps/voice` path picks up different import-sorting rules and reports spurious errors unrelated
  to any real change.
- Docker Desktop in this environment occasionally kills a long-running `docker compose build`/
  `up` command with no error; retrying the exact same command succeeds. Not a code issue.

## Findings

None recorded against this feature; the ledger's outstanding entries all predate it and are
unrelated.
