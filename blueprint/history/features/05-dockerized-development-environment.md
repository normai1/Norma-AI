# Feature: Dockerized development environment

**From build-plan:** feature 5

## Goal

Bring the full six-service development stack up under Docker Compose - `norma-postgres`,
`norma-redis`, `norma-api`, `norma-web`, and two services that don't exist in the repository yet,
`norma-voice` (media plane) and `norma-worker` (background jobs) - and document the public-tunnel
setup telephony webhooks will need. `apps/voice` and `apps/worker` get minimal, genuinely runnable
skeletons here so `docker compose up` brings up the whole stack consistently; their real logic
belongs to later features (20 for voice, whichever knowledge/post-call feature first needs a job
belongs to worker).

## In scope

- A minimal `apps/voice` FastAPI skeleton: one health endpoint matching the contract CLAUDE.md
  already documents (active session count and capacity), a Dockerfile, and its Compose service.
- A minimal `apps/worker` skeleton: connects to Redis and logs a heartbeat on an interval, proving
  the container starts and can reach its dependencies, with no job queue library chosen yet.
- `NEXT_PUBLIC_VOICE_WS_URL` added to `.env.example` - documented now, matching how
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are already present before anything consumes them.
- Documenting the public-tunnel (ngrok or equivalent) setup in `AGENTS.md`, for when telephony
  webhooks need one.

## Out of scope

- **Any real voice-session logic** - STT, TTS, LLM turn orchestration, barge-in. That's item 20, the
  highest-risk item in the project; this feature only makes the container exist and boot.
- **Choosing ARQ vs. Celery, or writing any real background job.** The plans name a Redis-backed
  queue as the eventual choice but defer picking one until a feature actually needs it (most likely
  the knowledge/document-processing items, 14-19). `apps/worker` here only proves connectivity.
- **Actually running a tunnel.** This feature documents the setup; there is nothing to tunnel to yet
  since no telephony provider integration exists (items 23+). Running `ngrok` is a manual step the
  developer does when they need it, not something this feature executes or tests.
- **Wiring `NEXT_PUBLIC_VOICE_WS_URL` into any frontend code.** Nothing consumes it until the
  in-browser test call (item 21). It is provisioned now so that feature doesn't have to touch
  `.env.example` for a variable the plans already named.
- **Production Docker configuration.** This is the local development stack; production deployment
  (Render, Fly.io) is item 60.
- **Meaningful session/capacity numbers.** The health endpoint's shape is real and locked, but its
  values are placeholders until item 20f (latency instrumentation) or item 31 (concurrency and call
  admission control) give them something real to report.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `apps/voice` skeleton** - add `apps/voice/requirements.txt` (`fastapi`,
  `uvicorn[standard]`), `apps/voice/app/main.py` (a FastAPI app with `GET /health` returning
  `{"status": "ok", "active_sessions": 0, "capacity": <placeholder int>}` - the shape CLAUDE.md
  section 18 already documents for the media plane's health/capacity endpoint), and
  `apps/voice/Dockerfile` (mirrors `apps/api/Dockerfile`'s pattern: `python:3.12-slim`, installs
  `curl` for the healthcheck, `EXPOSE 8080`). Add a `voice` service to `docker-compose.yml`:
  `container_name: norma-voice`, `restart: unless-stopped` (matching the other four services),
  port `8080:8080`, `env_file: .env`, `depends_on` postgres and redis (`condition:
  service_healthy`), a bind-mounted volume and `--reload` command matching the `api` service's
  dev-loop pattern, and a `curl`-based healthcheck against `/health`. Add
  `NEXT_PUBLIC_VOICE_WS_URL: ws://localhost:8080` to the `web` service's existing `environment:`
  block, alongside `NEXT_PUBLIC_API_URL` - unused until item 21, but Next.js needs `NEXT_PUBLIC_*`
  vars present at dev/build time either way. Add a "Media plane (`apps/voice`)" entry to
  `AGENTS.md`'s Commands section (`docker compose logs -f voice`, and a note that there's no
  standalone local dev command yet - it only runs via Compose).
  *Done when:* `docker compose up -d voice` reports the container healthy in `docker compose ps`,
  and `curl http://localhost:8080/health` returns the documented JSON shape.

- [x] **Step 2 - `apps/worker` skeleton** - add `apps/worker/requirements.txt` (`redis[hiredis]`,
  `python-dotenv`), `apps/worker/main.py` (reads `REDIS_URL`, loops on a short interval logging a
  heartbeat with the result of a Redis `PING` - no job queue library yet, see Out of scope; each
  iteration catches and logs a Redis connection error rather than crashing the process, since
  `depends_on: service_healthy` only guarantees Redis is up at container start, not that it stays
  reachable), and `apps/worker/Dockerfile` (minimal: `python:3.12-slim`, install requirements, `CMD
  ["python", "main.py"]`). Add a `worker` service to `docker-compose.yml`: `container_name:
  norma-worker`, `restart: unless-stopped`, `env_file: .env`, `depends_on` postgres and redis
  (`condition: service_healthy` - `DATABASE_URL` is provisioned for the first real job feature to
  use, even though this skeleton only touches Redis), no healthcheck (nothing depends on it yet). Add a matching Commands entry to `AGENTS.md`
  (`docker compose logs -f worker`). *Done when:* `docker compose up -d worker` stays `Up` in
  `docker compose ps` for at least 15 seconds (not crash-looping), and its logs show at least one
  heartbeat line with a successful Redis ping.

- [x] **Step 3 - Document the public tunnel setup** - add a "Public tunnel (telephony webhooks)"
  section to `AGENTS.md`: install ngrok (or an equivalent tunnel tool), run it against the API port
  (`ngrok http 8000`) once a telephony provider is configured, copy the forwarding URL into the
  developer's own `.env` (never committed - `.gitignore` already excludes `.env`), and a note that
  the in-browser test call (item 21) works without a tunnel and is the default local loop until
  telephony (items 23+) lands. *Done when:* the section exists and accurately states that no
  telephony provider is wired up yet, so there is nothing to point the tunnel at today.

## Files / areas

| Path | Change |
| --- | --- |
| `apps/voice/requirements.txt` | new |
| `apps/voice/app/main.py` | new |
| `apps/voice/Dockerfile` | new |
| `apps/worker/requirements.txt` | new |
| `apps/worker/main.py` | new |
| `apps/worker/Dockerfile` | new |
| `docker-compose.yml` | edit - `voice`, `worker` services |
| `.env.example` | edit - `NEXT_PUBLIC_VOICE_WS_URL` |
| `AGENTS.md` | edit - Commands entries, public-tunnel section |

## Data / contracts

- **The media-plane health endpoint's shape is locked**, even though its values are placeholders:
  `GET /health` -> `{"status": "ok", "active_sessions": int, "capacity": int}`. This is the contract
  CLAUDE.md already documents for deployment scaling/drain decisions (section 18) and for the
  eventual concurrency/admission-control work (item 31) - later features fill in real numbers, they
  don't change the shape.
- No database schema, API route, or stored field changes.

## Testing

Infrastructure and skeleton code, not application logic - `coding-standards.md`'s scope rule places
this under "what not to test" (integration/infra surfaces verified by running the real thing, not
unit tests). Each step's "done when" is the verification: real `docker compose` commands and a real
`curl` request, not a mocked assertion.

## Notes for the AI

- **Keep both skeletons genuinely minimal.** No session-handling, no queue library, no speculative
  abstraction for capabilities that don't exist yet - the point of this feature is a container that
  boots and proves connectivity, not a head start on item 20's real logic.
- **CLAUDE.md section 4 has a stale cross-reference** ("`apps/voice` and `apps/worker`... created by
  build-plan items 20 and 17 respectively") - item 17 in the current numbering is "Document
  processing pipeline," not a background-worker item. This feature is what actually creates both
  directories; flag the stale reference to the user rather than silently editing CLAUDE.md.
- **Match `apps/api`'s existing Dockerfile conventions** (`python:3.12-slim`, `PYTHONDONTWRITEBYTECODE`,
  `PYTHONUNBUFFERED`, no `--reload` implied outside the dev command) rather than inventing a new
  pattern.
- **No em dashes**, two blank lines between top-level Python definitions, short docstrings on public
  functions - same conventions as `apps/api`.
