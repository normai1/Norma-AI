# CLAUDE.md

# Norma AI — Project Instructions

This file is the primary instruction and context document for AI coding agents working on the Norma AI repository.

Read this file before making code, architecture, schema, dependency, configuration, or infrastructure changes.

---

## 1. Project identity

- Product name: **Norma AI**
- Previous working name: **fonio-clone**
- Product: an **AI phone assistant platform**. Norma answers a business's inbound calls, holds a natural spoken conversation with the caller, completes the requested task, and reports back — 24 hours a day. It also places outbound calls and runs calling campaigns.
- Repository/project directories may still contain legacy `fonio-clone` identifiers. Do not rename files, containers, volumes, service identifiers, package names, or URLs blindly. See section 36.

### Core product principle

> Never miss a call again.

### What "good" means here

Latency, interruption handling, and answer accuracy **are** the product. A feature-complete assistant that replies in three seconds, talks over the caller, or invents a price is a failed product, regardless of how clean the code is.

These are hard requirements, not polish:

```text
Time to first audio after caller stops speaking:  p50 < 700 ms, p95 < 1200 ms
Barge-in (caller speech cancels playback):        < 200 ms
Retrieval inside a call turn:                     < 80 ms
```

Any change that regresses these is a bug even if every test passes.

### Direction change notice

This project was previously planned as a text-based business knowledge workspace with a CRM and a RAG chat interface. **That direction is dead.** Completed items 1–3 (authentication, organizations/invitations, RBAC) carried over unchanged. Everything else was re-planned.

If you find code, docs, comments, or tests referencing companies, opportunities, deals, tasks, notes, or a text AI chat interface, treat them as legacy. Do not extend them. Flag them for removal rather than deleting them opportunistically mid-feature.

---

## 2. Source of truth hierarchy

When making implementation decisions, use this order of authority:

1. **Current repository code and configuration**
2. `CLAUDE.md`
3. `project-plan.md`
4. `build-plan.md`
5. Individual feature/spec documents
6. Existing tests and fixtures
7. Previous conversation context
8. General assumptions

If repository behavior conflicts with an old conversation or assumption, the repository is authoritative.

**Exception:** where repository code reflects the abandoned product direction (section 1), the plans win. Reconcile explicitly rather than silently choosing one.

Do not redesign an existing subsystem merely because a different implementation would be theoretically cleaner. First inspect the current implementation, tests, migrations, dependency graph, and API behavior.

---

## 3. Product scope

Norma AI is a multi-tenant SaaS platform for small and medium-sized businesses whose phone is a revenue channel.

Three distinct people interact with the system:

- **Buyer** — owner or office manager. Judges the product in the first ten minutes.
- **Operator** — configures assistants, reviews calls, corrects mistakes. Daily user.
- **Caller** — the end customer on the phone. Never sees the UI. Only experiences latency, voice quality, and whether the task got done.

Target segments: medical/dental/veterinary practices, tradespeople and field service, hotels/spas/salons/restaurants, property management and real estate, car dealerships and repair shops, solo professionals, and front desks needing overflow handling.

### MVP scope

- Authentication, organizations, workspaces, RBAC
- Assistant configuration and versioning
- Voice and language catalogue
- Prompt templates and versioning
- Glossary and pronunciation
- Knowledge: file upload, website ingestion, manual FAQ
- Chunking, embeddings, pgvector, low-latency retrieval
- Real-time voice session engine
- In-browser test call
- Telephony: number provisioning, inbound handling, forwarding, DTMF, SIP trunk
- Call records, transcripts, recordings
- In-call skills: scheduling, variable extraction, API actions, transfer
- Post-call summaries, extraction, delivery, webhooks
- Contacts and appointments
- Outbound calls and campaigns
- Guardrails, analytics, observability, audit logging
- Usage metering, subscription billing, plan enforcement
- Production readiness

### Out of MVP scope

WhatsApp, web chat, email channels, hosted PBX/softphone, custom voice cloning, SSO/SAML, reseller/white-label, native mobile apps, multi-region active-active media, autonomous agents with unrestricted tools, a separate vector database.

Do not expand the MVP into a general agent platform unless the roadmap or an approved spec explicitly requires it.

---

## 4. Repository structure

Expected high-level structure:

```text
Norma AI/
├── apps/
│   ├── api/                # FastAPI control-plane backend
│   ├── voice/              # Media-plane voice session workers
│   ├── worker/             # Background job workers
│   └── web/                # Next.js frontend
├── packages/               # Shared packages when applicable
├── infra/                  # Infrastructure/configuration
├── scripts/                # Development/utility scripts
├── docs/                   # Project documentation
├── project-plan.md         # Product-level project plan
├── build-plan.md           # Feature roadmap/progress tracker
├── CLAUDE.md               # AI coding-agent instructions
├── docker-compose.yml
├── .env.example
└── .env                    # Local-only configuration; never commit secrets
```

`apps/voice` and `apps/worker` may not exist yet. They are created by build-plan items 20 and 17 respectively. Inspect the repository before changing structure.

Shared models, schemas, and provider abstractions used by both `apps/api` and `apps/voice` must live in a shared location, not be duplicated. The media plane and the API must agree on the schema.

---

# 5. Architecture

## 5.1 Two planes

This is the single most important architectural fact about the project.

```text
Control plane                        Media plane
─────────────                        ───────────
Next.js web app                      Voice session workers
FastAPI REST API                     Long-lived stateful processes
Background workers                   Bidirectional audio streaming
Postgres + pgvector                  STT / LLM / TTS orchestration
Redis                                Sub-second latency budget
Request/response, seconds            Persistent connections, milliseconds
Serverless-deployable                NOT serverless-deployable
```

Do not put real-time audio handling in the FastAPI request path. Do not put control-plane CRUD in the voice workers. When a voice session needs application data, it reads it through shared repositories or a narrow internal API — not by reimplementing business logic.

## 5.2 System diagram

```text
        ┌──────────────┐          ┌────────────────────┐
        │   Browser    │          │  Telephony provider │
        │  Next.js Web │          │  (Twilio / SIP)     │
        └──────┬───────┘          └─────┬──────────┬────┘
               │                        │          │
          HTTP / API            signed webhooks   media stream
               │                        │          │
        ┌──────▼───────────────────────▼──┐   ┌───▼────────────────┐
        │        FastAPI (control)         │   │  Voice workers     │
        │  auth · orgs · assistants ·      │◄─►│  STT → turn → LLM  │
        │  numbers · calls · billing       │   │  → TTS → barge-in  │
        └───┬──────────────┬───────────────┘   └───┬────────────┬───┘
            │              │                       │            │
     ┌──────▼──────┐  ┌────▼─────┐          ┌──────▼─────┐  ┌───▼────┐
     │ PostgreSQL  │  │  Redis   │          │  pgvector  │  │   S3   │
     │ + pgvector  │  │ queue ·  │          │ retrieval  │  │ record │
     │             │  │ concurr. │          │            │  │ -ings  │
     └─────────────┘  └────┬─────┘          └────────────┘  └────────┘
                           │
                    ┌──────▼───────┐
                    │  Background  │
                    │   workers    │
                    │ parse·crawl· │
                    │ embed·summ.· │
                    │ webhook·dial │
                    └──────────────┘
```

## 5.3 Frontend

- Next.js, React, TypeScript, Tailwind CSS
- shadcn/ui or existing repository UI primitives
- WebRTC or WebSocket audio for the in-browser test call

Responsibilities: auth screens, organization/workspace experience, assistant editor, call review, knowledge management, contacts, appointments, numbers, campaigns, integrations, analytics, settings, and clear error/loading/empty states.

Backend authorization is mandatory. Never put business-critical authorization logic only in the frontend.

## 5.4 Backend (control plane)

- Python 3.12+, FastAPI, Pydantic, SQLAlchemy, Alembic, asyncpg, Redis client, pytest, httpx

Expected layering:

```text
API route
   ↓
Schema / validation
   ↓
Service
   ↓
Repository / data access
   ↓
Database
```

Avoid complex business logic inside route functions.

## 5.5 Media plane

- Python voice session workers
- Real-time orchestration framework: **LiveKit Agents** is the primary candidate, **Pipecat** the evaluated alternative. The decision is made in build-plan item 20a and recorded in `build-plan.md`. Until then, do not write code that assumes either.
- Whichever framework is chosen, every pipeline stage sits behind Norma's own interfaces so the framework remains replaceable.

Media-plane rules:

- A worker process may hold live calls. It must never be killed without draining.
- No blocking I/O in the audio path.
- No unbounded queues. Backpressure must be explicit.
- Every stage emits timing so per-turn latency is measurable (section 27).
- A crashed or timed-out session forwards the call or takes a message. **Silence is the worst possible failure.**

---

# 6. Database conventions

## 6.1 PostgreSQL

- PostgreSQL 16, pgvector enabled
- System of record for structured application data

Do not introduce another database without a clear feature requirement and explicit architectural justification.

## 6.2 Migrations

All schema changes use Alembic.

Before creating a migration:

1. Inspect existing models.
2. Inspect the current Alembic head.
3. Check previous migrations for related objects.
4. Confirm the target schema.
5. Avoid duplicate enum/type/index/constraint creation.

After creating a migration:

```bash
alembic upgrade head
```

**Two-plane constraint:** the API and the voice workers deploy separately and will briefly run different code against the same schema. Migrations must therefore be **additive and backwards-compatible within a deploy**. Never ship a migration that breaks the currently-running version of either plane. Column removals and renames are two-step operations across two releases.

Never make manual production schema changes. Never casually delete or rewrite historical migrations.

## 6.3 Tenant isolation

Norma AI is multi-tenant with two scoping levels:

```text
Organization  — billing, membership, subscription
    └── Workspace  — a location, team, brand, or department
            └── Assistants, numbers, calls, contacts, appointments, knowledge
```

Every organization-owned resource must be scoped to the correct organization. Workspace-owned resources must additionally be scoped to the workspace. A request must never retrieve, modify, or delete another tenant's records.

**This applies to the media plane too.** Session establishment, knowledge retrieval, recording writes, and tool calls all carry and enforce tenant scope. A voice worker handling a call for org A must be structurally incapable of reading org B's knowledge.

When adding a new scoped model:

- Include the organization and, where applicable, workspace ownership relationship.
- Add tenant-aware repository access.
- Add authorization tests.
- Add cross-tenant negative tests.

Prefer existing scoping mechanisms over ad-hoc filters in every route.

## 6.4 Vectors

Use pgvector. Do not introduce Pinecone, Chroma, Weaviate, or another external vector store without an explicit architectural decision.

- Embedding model: OpenAI `text-embedding-3-small`
- Dimension: `1536`, column type `vector(1536)`
- Keep the dimension configurable, and ensure the configured value matches actual provider output
- Test dimension compatibility before writing vectors
- Never silently truncate or pad embeddings
- Retrieval filters by organization, workspace, and assistant — always

Do not fix a vector-dimension error by changing the database column. Verify the configured embedding provider first.

## 6.5 High-volume tables

`Call`, `CallLeg`, `TranscriptTurn`, `TurnMetric`, and `ToolInvocation` grow far faster than everything else. Rules:

- Never join them into the hot path that reads assistant configuration during a call.
- Index for the queries the call list and analytics actually run.
- Assume partitioning or archival will be needed; do not design schemas that make it impossible.
- Bulk-insert transcript turns and metrics; do not write one row per statement per turn in the audio path.

---

# 7. Authentication and authorization

Expected capabilities: registration, login, logout, access tokens, refresh/session handling, password hashing, user identity, organization membership, workspace membership.

Organization roles:

```text
Owner
Admin
Member
Viewer
```

Do not assume a role implies every permission. Use the existing permission model.

Every protected endpoint establishes:

1. Who is the user?
2. Which organization and workspace context are they operating in?
3. Are they a member with access to that workspace?
4. Are they authorized for the requested operation?
5. Does the target resource belong to that organization and workspace?

Additional authenticated surfaces that are easy to forget:

- **Media-plane session establishment** — a browser test call must prove workspace access before audio flows.
- **Telephony webhooks** — authenticated by provider signature, not by session (section 10).
- **Public API keys** — hashed at rest, scoped, revocable, rate-limited.
- **Inbound webhooks** — per-webhook secret, verified.

---

# 8. Provider abstractions

Keep provider-specific code behind interfaces. The application must be able to swap any provider without rewriting the application layer.

```text
LLMProvider              — realtime and post-call tiers
EmbeddingProvider        — OpenAI text-embedding-3-small, Mock
SpeechToTextProvider     — ElevenLabs, Mock
TextToSpeechProvider     — ElevenLabs, Mock
TelephonyProvider        — Twilio, Telnyx, SIP trunk, Mock
StorageProvider          — S3, local (dev only)
MessagingProvider        — email, SMS
BillingProvider          — Stripe
Retriever / VectorStore
```

Every abstraction needs a deterministic mock. The test suite must run with zero paid external calls.

## 8.1 Two LLM tiers

```text
Realtime turn model   — latency-critical, small, streaming
                        openai/gpt-oss-120b (served via Groq)
Post-call model       — quality-critical, latency-tolerant
                        summaries, extraction, learning candidates
                        gemini-2.5-flash-lite (served via Google)
```

A frontier model in the per-turn conversation loop is **explicitly rejected**. First-token latency dominates perceived call quality and makes the p95 budget unreachable. If you find a large model configured for the realtime tier, that is a bug.

Never hard-code a model or provider inside a route, service, or voice worker.

---

# 9. Real-time voice engine

The pipeline:

```text
Inbound media
  ↓
Streaming STT (with glossary biasing)  → partial + final transcripts
  ↓
Turn detection (VAD + semantic end-of-turn)
  ↓
Context assembly (assistant version + conversation state + retrieval)
  ↓
Realtime LLM (streaming)
  ↓
Sentence-chunked streaming TTS
  ↓
Outbound media
  ↓
Barge-in: caller speech cancels playback immediately
```

Non-negotiable behaviors:

- **Start speaking before the LLM finishes.** Chunk on sentence boundaries; do not wait for a complete response.
- **Barge-in cancels playback within 200 ms** and discards the abandoned response.
- **Turn detection must not cut the caller off** mid-sentence, and must not leave dead air after they finish. Sensitivity is operator-configurable.
- **Tool calls that take time need backchannel handling** — a filler utterance or hold cue, not silence.
- **Every turn writes a TurnMetric row** with STT finalization, retrieval, LLM first token, LLM complete, TTS first byte, and audio out.
- **Provider failure never produces silence.** Fail over to forwarding or message-taking.

When changing anything in this path, run the voice pipeline replay harness and the latency regression tests (section 28). A change that improves answer quality while adding 400 ms is a regression.

---

# 10. Telephony

## 10.1 Webhook security

Telephony webhooks **must** verify the provider's request signature before any processing. An unauthenticated call webhook is a free-calls exploit and a billing-fraud vector. There is no development shortcut for this; use the provider's test credentials instead of skipping verification.

## 10.2 Number provisioning

- Numbers are searched and claimed by country and area.
- Regulatory document requirements differ by country and must be surfaced to the operator before purchase, not after failure.
- Numbers are assigned to an assistant within a workspace.
- Released numbers must be released with the provider, not just soft-deleted locally.

## 10.3 Regulatory

Number provisioning, automated outbound calling, caller ID, and recording consent are **per-country configuration, not global settings**. Before enabling provisioning or outbound flows in a market, current local requirements must be verified. For India specifically, verify current TRAI/DoT rules before building or enabling those flows. This is a launch blocker, not an implementation detail. Do not implement a country's flow on the assumption that US or EU rules apply.

## 10.4 Call control

- **Concurrency** is limited per plan and enforced at admission. Excess calls forward or take a message; they do not queue indefinitely.
- **Forwarding** supports prioritized targets, business hours, and international destinations.
- **DTMF** sending is supported for traversing external IVR menus.
- **SIP trunk** support allows bring-your-own numbers with credential storage and allowed-IP configuration.
- **Outbound** requires per-organization spend caps, destination allow-lists, consent gating, and rate limits on triggering endpoints.

---

# 11. Knowledge and retrieval

Pipeline:

```text
Knowledge source (file upload / website crawl / manual FAQ)
  ↓
Parser
  ↓
Normalized text
  ↓
Chunker
  ↓
Embedding provider
  ↓
pgvector(1536), scoped by org + workspace + assistant
  ↓
Retriever (< 80 ms budget, small top-k)
  ↓
Context builder
  ↓
Realtime LLM turn
  ↓
Spoken answer + recorded source attribution
```

Supported upload types: PDF, DOCX, Markdown, TXT. Website sources are crawled, extracted, content-hashed, and recrawlable.

Retrieval sits **inside** the latency budget. Techniques that help: pre-warmed connections, small top-k, per-assistant chunk ceilings, caching embeddings for repeated caller phrasings, and preloading the assistant's highest-frequency FAQ content into the prompt instead of retrieving it.

When changing retrieval:

- Keep tenant and workspace filtering mandatory.
- Preserve chunk metadata and source traceability.
- Do not increase context size to improve recall without measuring latency impact.
- Add regression tests.
- Confirm vector dimensions match the configured model.

Debug path for retrieval bugs:

```text
embedding configuration
→ stored vector dimension
→ query embedding dimension
→ database vector column
→ similarity query
→ tenant/workspace/assistant filters
→ returned chunks
→ context builder output
→ retrieval latency
```

**Glossary** entries serve two purposes: STT keyword biasing so the model hears domain terms correctly, and TTS pronunciation overrides so it says them correctly. Both applications must be wired.

---

# 12. Assistant configuration and prompts

The **Assistant** is the central configurable object. `AssistantVersion` is an immutable configuration snapshot. Every call records which version answered it. Never mutate a version that a call references.

Operator-configurable without code: name, voice, language, greeting and whether it is interruptible, persona and behavioral instructions, speech rate, turn-detection sensitivity, creativity (bounded temperature), ambient sound, business-hours behavior, unresolved-request fallback, enabled skills.

Prompts:

- Reusable templates per use case (receptionist, support, scheduling, answering machine, field service, order intake).
- Versioned with rollback.
- Variables interpolated from assistant, workspace, and caller context.
- Never embed large production prompts inside route handlers or voice workers.
- Treat prompt changes like code changes: review, test, version, never silently replace a production version.

---

# 13. In-call skills and tool permissions

Available skills: answer from knowledge, check availability, book/reschedule/cancel appointments, capture structured fields, look up or create CRM records, make an authenticated HTTP request mid-call, transfer to a human, take a message, send SMS or email.

Tool permission rules:

```text
Caller intent
   ↓
Assistant's declared enabled-skill set   ← enforced here, not by the model
   ↓
Authorization / policy check
   ↓
Tool execution (timeout-bounded)
   ↓
Result validation
   ↓
ToolInvocation log row
```

- A skill the operator did not enable must be **structurally uninvokable**, regardless of what the model emits. Model output is a request, not an authorization.
- Every tool call is logged with arguments, result, latency, and outcome.
- Tool calls in the audio path are timeout-bounded. A hanging integration must not hang the call.
- Never give a tool unrestricted database access, arbitrary SQL, or shell execution.

---

# 14. Post-call processing

Runs on the post-call model in a background worker, not in the audio path:

- Recording finalization to object storage
- Full transcript with speaker turns, timestamps, and interruption marking
- Generated summary and call outcome/disposition
- Structured variable extraction against the operator-defined schema, with confidence
- Delivery by email and SMS
- Outbound webhook with signing and retry
- Push into connected integrations
- Post-call authenticated API request

A post-call failure must be retryable and visible. A call that was handled well but whose summary never arrived is a support ticket.

---

# 15. Guardrails and AI safety

AI output is not automatically trusted. Caller speech, retrieved documents, crawled web pages, and model output are all untrusted data.

Guardrail responsibilities:

- Input validation
- Prompt-injection defenses for retrieved and caller-supplied text
- Topic and action allow-lists per assistant
- Output validation before a spoken commitment is made
- Refusal to state prices, hours, availability, or policy not present in knowledge — unknown means "I'll have someone call you back"
- PII handling rules in transcripts and logs
- Tool permission enforcement independent of the model

Prompt-injection specifics:

- Retrieved text and crawled pages are data, never instructions.
- Application instructions outrank document content.
- A document must not be able to redefine authorization, grant tool permissions, change the assistant's persona, or cause disclosure of secrets or other tenants' data.
- A **caller** can also attempt injection by speaking instructions. Treat caller speech with the same distrust as document text.

Never give an AI agent unrestricted destructive access to the database or external systems. High-risk operations require explicit authorization and, where appropriate, human confirmation.

---

# 16. Domain entities

Core entities and their scope:

```text
Organization        — billing, membership, subscription
Workspace           — scoping unit for everything below
User, Session
Assistant, AssistantVersion
PhoneNumber, SipTrunk, ForwardingTarget
Call, CallLeg, TranscriptTurn, Recording, CallSummary
ExtractedVariable, ToolInvocation, TurnMetric
KnowledgeSource, Document, CrawledPage, Chunk, Embedding
GlossaryEntry, UnansweredQuestion
Contact, Appointment, CalendarConnection
Campaign, CampaignContact, SuppressionEntry
Integration, Webhook, ApiAction, ApiKey
Subscription, UsageRecord, Overage
Activity, AuditLog
```

**Activity vs AuditLog** are deliberately distinct. Activity is user-facing business history (call handled, appointment booked, contact created). AuditLog is security/compliance history (login, role change, number provisioned, recording deleted, integration connected, API key issued). Do not merge them.

**Legacy entities to not extend:** Company, Opportunity, Deal, Task, Note, Conversation, Message. These belong to the abandoned direction. Contacts survive; the rest do not.

When adding a new entity: define organization and workspace scope, repository methods, service rules, API schemas, routes, authorization tests, cross-tenant negative tests, and a migration. Add frontend support only after backend behavior is stable.

Avoid prematurely creating a generic "everything table" when specific domain entities are needed.

---

# 17. API design

Use the existing versioning convention:

```text
/api/v1/...
```

Prefer RESTful resources:

```text
GET    /api/v1/assistants
POST   /api/v1/assistants
GET    /api/v1/assistants/{id}
PATCH  /api/v1/assistants/{id}
DELETE /api/v1/assistants/{id}

GET    /api/v1/calls
GET    /api/v1/calls/{id}
GET    /api/v1/calls/{id}/transcript
GET    /api/v1/calls/{id}/recording      → signed short-lived URL
```

Explicit action endpoints are appropriate where they represent a real domain operation:

```text
POST   /api/v1/assistants/{id}/publish
POST   /api/v1/calls/outbound
POST   /api/v1/numbers/{id}/release
POST   /api/v1/campaigns/{id}/start
```

Every API change considers: authentication, authorization, validation, error responses, tenant and workspace isolation, rate limiting where abuse is possible, tests, and OpenAPI impact.

Telephony webhook endpoints are a separate category: signature-verified, no user session, idempotent, and fast — they must return promptly and hand work off rather than blocking.

---

# 18. Health endpoints

```text
GET /api/v1/health
GET /api/v1/health/database
GET /api/v1/health/redis
GET /api/v1/health/providers
GET /api/v1/health/all
```

The media plane exposes its own health endpoint reporting **active session count and remaining capacity**, which the deployment platform uses for scaling and drain decisions.

Do not remove or change health-endpoint semantics without updating deployment and monitoring expectations. Keep them lightweight and safe to call.

---

# 19. Redis

Used for:

- Session/refresh state where applicable
- Background job queues
- **Concurrency accounting and call admission control**
- Rate limiting
- Caching
- Temporary coordination between planes

Do not use Redis as the primary source of truth for durable business records. Concurrency counters are the exception in spirit but must reconcile against the database; a leaked counter must not permanently block a customer's calls. Build in expiry and reconciliation.

Cache invalidation must be considered when changing persistent data that is cached. Assistant configuration is read on every call — cache it, and invalidate on publish.

---

# 20. Object storage, recordings, and retention

Recordings and uploaded documents live in S3-compatible object storage. Local filesystem is a development-only adapter. Production must never depend on container-local disk.

Recordings are written **from the media plane**, so the voice workers need their own storage credentials.

Rules:

- Recording is **off by default** and configurable per assistant.
- Retention is configurable per organization, with automatic deletion by a background job.
- A "no retention" mode discards audio and transcript text after post-call delivery, keeping only metadata and metrics.
- Recordings are served only through **signed, short-lived URLs**. Never public objects. Never a permanent URL.
- Consent announcements are configurable per assistant and required where the destination jurisdiction demands them.
- Deletion must actually delete the object, and must be audit-logged.

Document processing exposes:

```text
pending → processing → completed → failed
```

or the exact statuses already implemented. Failures must be inspectable and retryable.

Do not store large binaries in PostgreSQL.

---

# 21. Billing, metering, and abuse

The product is minute-metered. Metering is not a post-launch concern — it is also the abuse control.

- **Minutes** are the primary metered unit. Every call records billable seconds and provider cost.
- Secondary billable quantities: phone numbers, workspace connections, contacts.
- Plans: Solo, Team, Scale — monthly and annual.
- **Limits are enforced in the application, not only on the invoice.** Concurrency gates admission. Number, workspace, and contact allowances are checked at creation. Minute exhaustion triggers an overage package or a configured degraded behavior.
- **Never fail silently mid-call** because of a limit. A caller must not experience a billing event.
- Capture provider cost per call from day one. Gross margin per minute determines whether this business works.
- Outbound calling requires spend caps, destination allow-lists, and rate limits. An account with a stolen API key and no cap is an unbounded loss.

---

# 22. Docker and local development

Expected development services:

```text
norma-postgres
norma-redis
norma-api
norma-web
norma-voice     (media plane)
norma-worker    (background jobs)
```

The repository may still use legacy names (`fonio-postgres`, `fonio-api`, etc.). Do not rename them merely because of the brand change if the repository relies on them. Rename deliberately as a scoped migration (section 36).

### Networking

Inside Docker Compose:

```text
API   → postgres:5432
API   → redis:6379
Voice → postgres:5432
Voice → redis:6379
```

From the host machine:

```text
Browser → localhost:3000   (web)
Browser → localhost:8000   (api)
Browser → localhost:8080   (voice test-call signalling)
Host    → localhost:5432   (postgres)
Host    → localhost:6379   (redis)
```

Do not use `localhost` for inter-container communication.

### Public tunnel requirement

Inbound telephony webhooks and media streams **cannot reach a local machine directly**. Development requires a public tunnel (ngrok or equivalent), and the tunnel URL must be configured with the telephony provider. This is a required setup step, not an optional convenience. Document it and keep the tunnel URL in `.env`, never committed.

The in-browser test call (build-plan item 21) works without a tunnel and should be the default local development loop.

---

# 23. Environment variables

Local secrets belong in `.env`. Provide `.env.example` with placeholders and safe development defaults.

Never commit: API keys, production passwords, JWT secrets, cloud credentials, production database credentials, private tokens, tunnel URLs tied to a provisioned number.

Configuration categories:

```text
# Core
APP_NAME
APP_VERSION
ENVIRONMENT
DEBUG
DATABASE_URL
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_HOST
POSTGRES_PORT
REDIS_URL
SECRET_KEY
CORS_ORIGINS
NEXT_PUBLIC_API_URL
NEXT_PUBLIC_VOICE_WS_URL

# LLM
LLM_PROVIDER
LLM_REALTIME_MODEL
LLM_POSTCALL_MODEL
ANTHROPIC_API_KEY
ANTHROPIC_BASE_URL

# Embeddings
OPENAI_API_KEY
EMBEDDING_MODEL
EMBEDDING_DIMENSION

# Speech
STT_PROVIDER
TTS_PROVIDER
ELEVENLABS_API_KEY

# Telephony
TELEPHONY_PROVIDER
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_WEBHOOK_SIGNING_SECRET

# Storage
AWS_REGION
AWS_S3_BUCKET
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY

# Billing
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
```

Use the exact variables already defined by the repository before adding duplicates. Production secrets come from the deployment platform's secret management.

---

# 24. Frontend conventions

Prefer: reusable components, typed API contracts, clear loading/empty/error states, accessible forms, server-side authorization where required, consistent UI primitives, i18n-ready copy in the component layer.

Avoid: monolithic components, repeated API-fetching logic, hard-coded production URLs, client-only security checks, silent error swallowing.

Voice-specific frontend concerns:

- The test call needs explicit microphone-permission handling, connection state, and a visible speaking/listening indicator.
- Audio players need keyboard controls and a transcript alternative.
- Live call state (ringing, in progress, transferred) needs real-time updates, not polling on a long interval.

---

# 25. UI/UX direction

Norma AI should feel like professional telephony infrastructure that a non-technical office manager can operate. Confident, calm, fast. Not a chatbot toy, not an enterprise console. Avoid an "AI demo" aesthetic.

Core navigation:

```text
Overview
Calls
Assistants
Knowledge
Contacts
Appointments
Campaigns
Numbers
Integrations
Analytics
Settings
```

Do not add navigation items for incomplete features.

Two screens carry disproportionate weight:

- **Onboarding** — pick a use-case template, crawl the business website, choose a voice, hear a test call, then claim a number. The operator should hear their own assistant talk before being asked for payment details.
- **Call detail** — audio synchronized to transcript, tool-call log, knowledge sources per answer, extracted fields, latency behind a disclosure. This is the screen that rebuilds trust after the assistant's first mistake. An operator must be able to see exactly why the assistant said what it said.

The **assistant editor** is a split view: configuration left, live test call right. Changes apply to the next test call without a save-and-reload cycle.

---

# 26. Error handling

User-facing errors must be understandable. Never expose `SQLAlchemy IntegrityError`, `asyncpg.exceptions...`, or tracebacks.

Distinguish clearly between:

- A configuration problem the operator can fix
- A provider outage they cannot
- A plan limit they can lift by upgrading

> "This number couldn't be provisioned — Germany requires a local address on file. Add one in Settings to continue."

Keep full technical details in logs. Never swallow exceptions silently.

**In-call errors are a separate category.** The caller is not a user of the UI and cannot read an error message. In-call failure handling means a spoken fallback, a transfer, or a message taken — never a stack trace, never silence, never a dropped call.

---

# 27. Logging and observability

Every log line in a call context carries a **call ID and turn ID**. Without correlation IDs, a latency problem across the two planes is undebuggable.

Important signals:

- Per-turn latency across every leg (STT finalization, retrieval, LLM first token, LLM complete, TTS first byte, audio out)
- Time to first audio, p50 and p95
- Barge-in responsiveness
- Provider errors and timeouts by provider
- Call outcomes, transfer rate, drop rate
- Retrieval failures and empty results
- Document and crawl processing failures
- Token usage and provider cost per call
- Concurrency utilization against plan limits
- Webhook delivery failures
- Database and Redis failures
- Authorization failures and security events

Never log: passwords, API keys, tokens, **transcript text**, caller PII without a justified operational need, full document contents, or recording bytes.

Transcript text in application logs is a data-protection incident waiting to happen. Log the call ID and look the transcript up through authorized access instead.

---

# 28. Testing rules

Every meaningful feature includes tests at the appropriate layer.

### Unit tests

Pure business logic, parsers, chunkers, provider adapters, guardrail logic, turn-detection logic, utilities.

### Integration tests

Database operations, repositories, retrieval, service/database interaction, authentication flows.

### API tests

Success paths, validation failures, unauthorized, forbidden, not-found, **cross-tenant access attempts**, rate limits, webhook signature rejection.

### Voice pipeline tests — first-class tier

This is the tier that does not exist in most projects and is mandatory here:

- **Fixture-audio replay** of full conversations through the real pipeline with mock STT/TTS/LLM/telephony providers.
- **Latency regression tests** that fail the build when p95 time-to-first-audio exceeds budget.
- **Barge-in tests** proving playback cancels within budget and the abandoned response is discarded.
- **Turn-detection tests** across trailing pauses, mid-sentence pauses, and overlapping speech.
- **Tool-permission tests** proving a disabled skill cannot be invoked by any model output.
- **Failure-mode tests**: provider timeout, provider error, mid-call disconnect, exhausted minutes — each producing the correct fallback rather than silence.

### AI tests

Deterministic providers and mocks. The suite must not depend on paid external APIs. Test prompt construction, retrieval, provider abstraction, streaming, guardrails, injection resistance, and failure handling.

### Regression principle

Whenever a bug is fixed: reproduce it with a test, fix the implementation, keep the test permanently.

---

# 29. Code quality

Prefer small focused functions, explicit types, clear naming, dependency injection where appropriate, reusable services and repositories, consistent error handling, minimal duplication.

Avoid premature abstraction. A new abstraction should solve a real repeated concern or protect an important architectural boundary — provider swappability and tenant isolation are the two boundaries worth defending unconditionally.

Do not add dependencies because they are fashionable.

---

# 30. Git and change discipline

Before making a change:

1. Inspect the current implementation.
2. Identify affected files.
3. Check related tests.
4. Check migrations and configuration.
5. Make the smallest coherent change.
6. Run targeted tests.
7. Run broader tests where appropriate — including voice pipeline tests if anything in the audio path changed.
8. Review the diff.

Do not silently change unrelated code. Do not rewrite working architecture to match a preferred style. Do not remove tests to make a feature pass.

---

# 31. Feature implementation workflow

```text
project-plan.md
       ↓
build-plan.md
       ↓
feature specification
       ↓
implementation
       ↓
tests
       ↓
review
       ↓
mark feature complete
```

The build plan is a living tracker. Do not renumber completed features.

When a feature materially changes product direction, users, data model, technology stack, monetization, UI/UX, or deployment, update `project-plan.md` and `build-plan.md` before implementing.

When implementing from a spec:

1. Read the spec completely.
2. Inspect the current codebase.
3. Identify existing reusable components.
4. Do not duplicate existing services, models, or repositories.
5. Implement the smallest complete slice.
6. Add migrations where needed.
7. Add tests.
8. Run existing tests to detect regressions.
9. Verify API, UI, and — where relevant — actual call behavior.
10. Update documentation if the architecture changed.

If a spec conflicts with the current implementation, stop and reconcile against the plans and repository state rather than silently choosing one.

---

# 32. Database safety

Never run destructive commands against the development database unless explicitly required:

```bash
docker compose down -v
DROP DATABASE
DROP TABLE
TRUNCATE
alembic downgrade base
```

Do not recommend deleting Docker volumes as a first troubleshooting step. If persistence is working, preserve it. For migration problems, inspect first.

---

# 33. Troubleshooting methodology

Evidence-driven debugging:

```text
1. Reproduce
2. Read the exact error
3. Identify the failing boundary
4. Inspect current configuration
5. Inspect the relevant implementation
6. Test the smallest hypothesis
7. Apply minimal fix
8. Re-run the failing test
9. Run regression tests
```

Do not make multiple unrelated configuration changes at once.

Docker:

```bash
docker compose config
docker compose ps
docker compose logs <service>
docker inspect <container>
```

Database:

```bash
alembic current
alembic heads
alembic history
```

### Voice debugging

Latency and audio problems have their own boundary sequence. Isolate before theorizing:

```text
telephony media arrival
→ STT partial/final timing
→ turn detection decision
→ retrieval duration
→ LLM first token
→ TTS first byte
→ outbound audio timestamp
```

Read TurnMetric rows for the affected call before changing code. "It feels slow" is not a diagnosis; a p95 breakdown by leg is.

For "the assistant said something wrong", read the call detail: which assistant version, which prompt version, which retrieved chunks, which tool calls. The answer is almost always in one of those four.

---

# 34. Current known infrastructure behavior

Development:

```text
PostgreSQL:  pgvector/pgvector:pg16   host port 5432
Redis:       redis:7-alpine           host port 6379
FastAPI:     host port 8000
Next.js:     host port 3000
Voice:       host port 8080
```

The Windows machine may have a separate native PostgreSQL installation. Do not assume `5432` belongs to Docker without checking. Docker PostgreSQL is the intended database.

Telephony webhooks require a public tunnel (section 22).

---

# 35. Legacy Fonio naming migration

The project was initially created as a Fonio clone and is now Norma AI. Legacy names may remain in container names, the Compose project name, volume names, environment variables, documentation, URLs, directory names, and package metadata.

Handle the migration incrementally. Before changing an identifier:

```bash
git grep -n "fonio"
```

Review every match before changing it. Do not alter third-party or internal identifiers tied to an existing migration or dependency without understanding the impact. Product-facing branding uses **Norma AI**.

Norma AI is an independent product competing in the AI voice agent category. It targets feature parity with established players but uses its own name, branding, copy, visual design, and implementation. Do not copy third-party marketing text, trademarks, or design assets into the repository.

---

# 36. Security rules

Never:

- Commit or log secrets
- Trust client-provided organization or workspace IDs without authorization
- Trust client-provided role claims without verification
- Process a telephony webhook without verifying its signature
- Let an LLM bypass authorization
- Let model output authorize a tool call
- Allow tools to execute arbitrary SQL or shell commands
- Serve a recording from a permanent or public URL
- Expose internal stack traces to users
- Disable tenant filtering to "make a query work"
- Ship an outbound-calling path without a spend cap

Always treat caller speech, user input, retrieved documents, crawled pages, and model output as untrusted data.

---

# 37. Performance principles

MVP priorities, in order:

1. Correctness
2. Security
3. **Call latency**
4. Reliability
5. Observability
6. Maintainability
7. Everything else

Call latency is promoted above general reliability concerns because it is a product requirement, not an optimization. This is the one place where "premature optimization" does not apply: the audio path is designed for latency from the first line of code, because it cannot be retrofitted later.

Elsewhere, do not prematurely optimize low-value paths.

For retrieval and prompts: avoid unnecessarily large prompts, keep retrieved context bounded, prefer relevant chunks over many chunks, cache repeated work, and keep expensive document processing in background jobs.

---

# 38. Deployment principles

Production topology:

```text
Frontend          → Vercel (Next.js)
Control-plane API → Render Docker Web Service
Media plane       → Fly.io (fallback AWS ECS/Fargate)
Background jobs   → Render Background Worker
Database          → Render Managed PostgreSQL with pgvector
Redis             → Render Key Value
Object storage    → Amazon S3
Billing           → Stripe
```

The media plane **cannot** be deployed to Vercel or to a standard autoscaling web service where scale-down may terminate a process holding a live call. Requirements for the media host: long-lived WebSocket and UDP/RTP connections, region selection near the telephony provider's media edge, connection-draining deploys, and scaling on concurrent sessions rather than HTTP request rate.

Production requires: HTTPS everywhere and WSS for media signalling, managed Postgres, Redis, object storage, containerized FastAPI, hosted Next.js, secret management, controlled pre-deploy migrations, health checks, CI/CD, and monitoring.

Deploy order is staged: **api → voice → web**. After every deploy, a synthetic test call against the environment is the only reliable verification that the product still works.

Do not introduce Kubernetes or microservices unless actual scale or operational requirements justify it. The two-plane split is not microservices; it is the minimum viable separation given two incompatible runtime models.

---

# 39. Definition of done

A feature is not complete because the code exists. It should satisfy:

- Requirements implemented
- Existing architecture respected
- Tenant and workspace isolation verified
- Authorization verified
- Input validation implemented
- Error paths considered
- Tests added or updated
- Migration added if required
- API and UI behavior verified
- No unrelated regressions
- Documentation updated when appropriate

For AI and voice features, additionally verify:

- Provider failure and timeout behavior
- Empty retrieval results
- Malformed model output
- Guardrail failures
- Prompt-injection scenarios, including caller-spoken injection
- Streaming interruption and barge-in
- Latency within budget, measured not assumed
- Token, usage, and cost capture
- **Behavior on a real call**, for anything in the audio path

---

# 40. What not to do

Do not:

- Rewrite the project from scratch
- Build features from the abandoned product direction (companies, opportunities, deals, tasks, notes, text AI chat)
- Put real-time audio handling in the FastAPI request path
- Deploy the media plane as serverless functions
- Put a frontier model in the per-turn conversation loop
- Skip telephony webhook signature verification, even in development
- Replace PostgreSQL or pgvector without a requirement
- Introduce a new framework when existing dependencies solve the problem
- Introduce microservices prematurely
- Put business logic or authorization only in the frontend
- Bypass migrations, or ship a migration that breaks the other plane
- Delete the database to solve application bugs
- Remove failing tests instead of fixing the implementation
- Log transcript text
- Trade latency for answer quality without measuring the cost
- Guess about current code behavior without inspecting it

---

# 41. Preferred implementation style

Favor boring, reliable engineering over clever engineering.

Good:

```text
clear service
→ clear repository
→ clear schema
→ clear tests
```

Avoid:

```text
one giant route
→ hidden database calls
→ implicit authorization
→ provider-specific code in business logic
→ no tests
```

Use explicit boundaries that make provider changes and testing straightforward. In the audio path, additionally favor: explicit backpressure, bounded queues, timeout on every external call, and a defined fallback for every failure.

---

# 42. Expected next development direction

Continue according to `build-plan.md`. The first unchecked item is the next target unless a specific item is selected by number or name.

**Build-plan item 20 (the real-time voice session engine) is the highest-risk item in the project** and the one most likely to invalidate assumptions recorded elsewhere. If it has not been started, consider a throwaway spike of 20a–20e before committing to surrounding items, and expect the spike to change recorded decisions in both plans.

After completing an item:

1. Run tests, including voice pipeline tests if relevant.
2. Verify the implementation.
3. Mark the item checked in `build-plan.md`.
4. Preserve historical numbering.
5. Continue with the next unchecked item.

---

# 43. Final instruction to AI coding agents

Before changing code, ask:

- What existing implementation handles this already?
- Is this code from the abandoned product direction?
- What organization and workspace boundary applies?
- What authorization rules apply?
- Does this touch the audio path? What does it cost in milliseconds?
- What happens on this path when a provider times out?
- What database or schema changes are required, and are they safe for both planes?
- What APIs change?
- What frontend behavior changes?
- Which provider abstractions are involved, and does this leak provider specifics upward?
- What tests will prove it works, including on a real call?
- What failure modes could break production, and what does the caller hear?
- Does this change affect `project-plan.md` or `build-plan.md`?

Prefer the smallest production-quality change that fits the existing Norma AI architecture.

**Repository code is the source of truth. Security and tenant isolation are non-negotiable. Latency is a product requirement, and silence on a live call is the worst possible failure.**
