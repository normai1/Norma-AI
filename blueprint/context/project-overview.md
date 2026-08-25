# Norma AI - Project Overview

> A secure, multi-tenant AI business workspace: talk to your business data and get work done.

## Problem

Business information is fragmented across customer records, opportunities, tasks, notes, internal
documents, and policies. Users spend their time searching several systems, assembling context by
hand, and repeating routine work.

Norma AI provides one workspace where an organization manages its core business information, uploads
internal knowledge, asks natural-language questions about it, and gets answers grounded in
tenant-scoped retrieval with the sources shown.

The MVP prioritizes correctness, security, tenant isolation, retrieval quality, observability, and a
maintainable provider-agnostic AI architecture.

### Product constraints

- Organization data is isolated by tenant.
- Backend authorization is mandatory; frontend checks are not security boundaries.
- AI responses are untrusted until validated by application policy.
- Retrieved document text is data, never instructions.
- AI tools and actions never bypass user permissions.
- LLM and embedding providers stay replaceable behind application abstractions.
- PostgreSQL + pgvector is the MVP system of record and vector store.
- Production document storage uses S3-compatible object storage from the first production release.
- Stay simple enough to operate - no premature microservices or Kubernetes.

## Users

Small and medium-sized businesses and their teams, working inside an organization/workspace.

| User type | What they need |
| --- | --- |
| **Business owners / founders** | Quick answers about customers, sales, operations, documents, performance |
| **Sales teams** | Customer context, account research, opportunity summaries, follow-up support |
| **Operations teams** | Fast access to processes, internal documentation, tasks, operational records |
| **Customer success / support** | Customer context and fast retrieval of internal knowledge |

### Access tiers

Access is organization-scoped. Users may eventually belong to multiple organizations.

```text
Organization
├── Owner
├── Admin
├── Member
└── Viewer
```

- **Anonymous** - auth routes only (register, log in).
- **Authenticated, no organization** - can create an organization or accept an invitation.
- **Organization member** - limited to that organization's data, gated further by role.

> A role does not imply every permission. Use the explicit permission model from feature 3.

## Features

MVP scope in `build-plan.md` order. One line of purpose each; detailed requirements belong in the
per-feature specs produced by `/feature`.

1. **User authentication** - register, log in, log out, and maintain secure authenticated sessions.
2. **Organization workspaces and invitations** - create organizations, manage memberships, invite users, enforce tenant isolation.
3. **Role-based access control** - define and enforce organization roles and permissions.
4. **User and organization settings** - profiles, organization configuration, basic preferences.
5. **Core application shell** - authenticated dashboard, navigation, workspace switching, global states.
6. **Company management** - create, view, search, update, and organize company records.
7. **Contact management** - create, view, search, update, and associate contacts with companies.
8. **Opportunity management** - deals, stages, values, owners, and status.
9. **Task management** - create, assign, track, and complete business tasks.
10. **Notes and activity timeline** - capture notes and display user-facing business activity history.
11. **Document upload and storage** - upload documents and manage metadata plus production object-storage references.
12. **Document processing pipeline** - parse, normalize, chunk, and track document processing status.
13. **Vector knowledge indexing** - generate OpenAI `text-embedding-3-small` embeddings at 1536 dimensions and store them in pgvector.
14. **Semantic knowledge retrieval** - retrieve relevant tenant-scoped organizational knowledge for user questions.
15. **AI chat interface** - persistent conversational chat with streaming responses.
16. **Grounded RAG responses** - **the headline feature**: answer using retrieved organizational knowledge and show sources.
17. **Conversation management** - create, rename, browse, persist, and delete conversations.
18. **AI provider abstraction** - configurable LLM and embedding providers behind stable interfaces.
19. **Prompt management** - reusable prompts and prompt versions for AI workflows.
20. **AI guardrails** - validate inputs and outputs; prevent unsafe or unauthorized AI behavior.
21. **AI and retrieval observability** - AI, retrieval, latency, usage, and error telemetry.
22. **Audit logging** - authentication, authorization, data, security, and administrative events.
23. **Global search** - search core business entities and the knowledge base with typed, tenant-scoped results.
24. **API and service health monitoring** - application, database, Redis, and dependency health checks.
25. **Automated testing foundation** - backend pytest coverage plus frontend Vitest/React Testing Library and Playwright.
26. **Dockerized development environment** - run the full stack consistently with Docker Compose.
27. **Production configuration** - separate dev and production configuration; secure environment handling.
28. **Initial CI/CD pipeline** - GitHub Actions running lint, type checks, backend tests, frontend tests, E2E tests, builds, and deployment checks.
29. **Production deployment readiness** - configure Vercel, Render, Render Postgres/pgvector, Render Key Value, and Amazon S3; verify production builds and health checks.
30. **MVP release validation** - validate the full journey from onboarding to AI-powered business questions.

### MVP success path

```text
User -> Authentication -> Organization -> Business data + documents
  -> Knowledge indexing -> AI chat -> Tenant-scoped retrieval
  -> Grounded response + sources
```

### Post-MVP (31-47)

Out of scope now, but the MVP data model and abstractions should not block them: team
collaboration (31), AI business actions (32), background AI workflows (33), advanced knowledge
management (34), external integrations (35), advanced analytics (36), usage metering (37),
subscription billing (38), enterprise security incl. SSO/SAML (39), advanced AI agents (40),
production observability (41), scalability improvements (42), advanced document storage lifecycle
(43), advanced deployment operations (44), mobile/responsive improvements (45), enterprise
integrations (46), and public launch readiness (47).

> Item 43 is **not** a migration from local storage to object storage - production ships on S3 from
> release one. It is later lifecycle hardening: retention, archival, replication, optimization.
> Likewise item 44 *improves* CI/CD; the initial pipeline is MVP item 28.

### Explicitly out of MVP scope

Fully autonomous unrestricted agents, arbitrary SQL/tool execution by AI, large integration
catalogs, SSO/SAML, enterprise billing infrastructure, native mobile apps, multi-region active-active
deployment, real-time collaborative editing, unrestricted destructive AI actions, and a separate
dedicated vector database.

## Data model

PostgreSQL 16 + pgvector is the system of record. Every organization-owned table below carries
`organization_id` and must be filtered by it server-side, in the repository layer - never only in a
route or the frontend.

### Shared conventions

- `id` (UUID, PK) on every table.
- `created_at`, `updated_at` (timestamptz) on every table.
- `organization_id` (UUID, FK -> Organization, indexed, NOT NULL) on every organization-owned table.
- Soft-delete is not assumed; use hard deletes unless a feature spec requires otherwise.

### Identity

#### User

- `email` (citext/text, unique, NOT NULL)
- `password_hash` (text, NOT NULL) - never plaintext
- `full_name` (text)
- `avatar_url` (text, nullable)
- `is_active` (bool, default true)
- `last_login_at` (timestamptz, nullable)
- Has many `OrganizationMember`

#### Session / RefreshToken

- `user_id` (UUID, FK -> User)
- `token_hash` (text) - store a hash, not the token
- `expires_at` (timestamptz)
- `revoked_at` (timestamptz, nullable) - supports logout and session invalidation
- `user_agent`, `ip_address` (text, nullable) - authentication metadata

> Redis may hold live session/refresh state; durable session records stay in PostgreSQL.
> Lifetimes are already configured (`access_token_expire_minutes`, `refresh_token_expire_days`).

### Organizations

#### Organization

- `name` (text, NOT NULL)
- `slug` (text, unique)
- `settings` (JSONB, default `{}`)
- `status` (enum: `active`, `suspended`)
- Has many `OrganizationMember`, and owns every organization-scoped record

#### OrganizationMember

- `organization_id` (UUID, FK -> Organization)
- `user_id` (UUID, FK -> User)
- `role` (enum: `owner`, `admin`, `member`, `viewer`)
- Unique on (`organization_id`, `user_id`)

#### Invitation

- `organization_id` (UUID, FK -> Organization)
- `email` (text, NOT NULL)
- `role` (enum, as above)
- `token_hash` (text), `expires_at` (timestamptz)
- `status` (enum: `pending`, `accepted`, `revoked`, `expired`)
- `invited_by_user_id` (UUID, FK -> User)

> **Locked shape.** Features 6-23 all depend on `organization_id` plus `OrganizationMember.role`.
> Changing that pair is a breaking change across the whole app.

### CRM / business data

#### Company

- `organization_id`, `name` (text, NOT NULL)
- `domain` (text, nullable), `industry` (text, nullable)
- `size` (text, nullable), `website` (text, nullable)
- `owner_user_id` (UUID, FK -> User, nullable)
- Has many `Contact`, `Opportunity`, `Note`, `Activity`

#### Contact

- `organization_id`, `company_id` (UUID, FK -> Company, nullable)
- `first_name`, `last_name` (text)
- `email` (text, nullable), `phone` (text, nullable), `title` (text, nullable)
- `owner_user_id` (UUID, FK -> User, nullable)

#### Opportunity

- `organization_id`, `company_id` (UUID, FK -> Company)
- `primary_contact_id` (UUID, FK -> Contact, nullable)
- `name` (text, NOT NULL)
- `stage` (enum - the stage set is defined in the feature 8 spec)
- `value_amount` (numeric), `currency` (text, default from organization settings)
- `status` (enum: `open`, `won`, `lost`)
- `expected_close_date` (date, nullable)
- `owner_user_id` (UUID, FK -> User, nullable)

#### Task

- `organization_id`, `title` (text, NOT NULL), `description` (text, nullable)
- `status` (enum: `todo`, `in_progress`, `done`)
- `priority` (enum: `low`, `medium`, `high`)
- `due_date` (timestamptz, nullable)
- `assignee_user_id` (UUID, FK -> User, nullable)
- Link to a related record: `related_type` (enum: `company`, `contact`, `opportunity`), `related_id` (UUID, nullable)

#### Note

- `organization_id`, `body` (text, NOT NULL)
- `author_user_id` (UUID, FK -> User)
- `related_type` / `related_id` - as on Task

#### Activity

- `organization_id`, `actor_user_id` (UUID, FK -> User, nullable)
- `type` (text) - e.g. `company.created`, `opportunity.stage_changed`, `note.added`
- `related_type` / `related_id`
- `payload` (JSONB) - before/after summary for the timeline

> **Activity is not AuditLog.** Activity is the user-facing business timeline of meaningful CRM
> events (feature 10). `AuditLog` is the security/compliance record (feature 22). The plans call
> these deliberately distinct; do not merge them.

### Conversations

#### Conversation

- `organization_id`, `user_id` (UUID, FK -> User)
- `title` (text) - generated, renameable by the user
- `last_message_at` (timestamptz)
- Has many `Message` (delete cascades)

#### Message

- `conversation_id` (UUID, FK -> Conversation, indexed)
- `role` (enum: `user`, `assistant`, `system`)
- `content` (text)
- `model` (text, nullable), `provider` (text, nullable)
- `prompt_tokens`, `completion_tokens` (int, nullable)
- `sources` (JSONB, nullable) - the record/document citations shown with the answer
- `created_at` (timestamptz)

### Knowledge base

#### Document

- `organization_id`, `uploaded_by_user_id` (UUID, FK -> User)
- `filename` (text) - original filename
- `content_type` (text) / `doc_type` (enum: `pdf`, `docx`, `markdown`, `txt`)
- `size_bytes` (bigint)
- `storage_key` (text) - S3 object key in production, local adapter path in development
- `status` (enum: `pending`, `processing`, `completed`, `failed`)
- `error_message` (text, nullable) - the visible reason a document failed
- `metadata` (JSONB)
- Has many `DocumentChunk` (delete cascades)

#### DocumentChunk

- `organization_id` (denormalized, NOT NULL) - retrieval filters on this directly
- `document_id` (UUID, FK -> Document, indexed)
- `chunk_index` (int) - position in the document
- `content` (text)
- `metadata` (JSONB) - page/section/offset for citation
- `embedding` (`vector(1536)`) - pgvector column

> **The RAG contract is locked.** OpenAI `text-embedding-3-small` at **1536** dimensions, matching
> the embedding-dimension setting and the `vector(1536)` column. Embeddings of different dimensions
> must never be mixed in one column. Changing the model or dimension later requires an explicit
> migration **and** reindex strategy - never "fix" a mismatch by editing the column.

### AI / prompt data

#### PromptTemplate

- `organization_id` (nullable for system-owned templates)
- `key` (text) - stable identifier, e.g. `chat.grounded_answer`
- `name`, `description` (text)

#### PromptVersion

- `prompt_template_id` (UUID, FK -> PromptTemplate)
- `version` (int)
- `body` (text) - the template content
- `model_config` (JSONB, nullable)
- `status` (enum: `draft`, `active`, `archived`)
- Unique on (`prompt_template_id`, `version`); production versions are never silently replaced.

### Audit and telemetry

#### AuditLog

- `organization_id` (nullable for pre-organization auth events)
- `actor_user_id` (UUID, FK -> User, nullable)
- `event_type` (text) - auth/security event, membership change, important CRUD, document processing, AI security event, admin action
- `resource_type`, `resource_id`
- `ip_address`, `user_agent` (text, nullable)
- `metadata` (JSONB) - enough context to investigate, without retaining sensitive content unnecessarily

#### AIRequestLog

- `organization_id`, `user_id`, `conversation_id` (nullable)
- `provider`, `model` (text)
- `latency_ms` (int), `prompt_tokens`, `completion_tokens` (int)
- `retrieval_chunk_ids` (JSONB) - retrieval trace and retrieved chunk references
- `status` (enum: `success`, `provider_error`, `guardrail_blocked`, `timeout`)
- `error_message` (text, nullable)

> Feeds feature 21 today and the usage metering in item 37 later, so billing can be added without a
> rewrite.

### Not stored in PostgreSQL

- **Secrets / API keys** - configuration secrets via environment and platform secret management, never application records.
- **Document binaries** - Amazon S3 in production, local filesystem adapter in development. PostgreSQL keeps metadata and `storage_key`. Production must never depend on container-local disk, which is ephemeral.
- **Redis** - refresh/session state, cache, rate limiting, temporary coordination, future job queues. Never the system of record.

## Tech stack

| Layer | Choice | Role |
| --- | --- | --- |
| Frontend | **Next.js** + React + TypeScript | Web app, auth UI, dashboard, chat, CRM, knowledge |
| Styling | **Tailwind CSS** + **shadcn/ui** (or existing repo primitives) | Consistent UI primitives |
| Backend | **FastAPI** on **Python 3.12+** | REST API, AI orchestration, RAG pipeline, health checks |
| Validation | **Pydantic** | Request/response schemas and settings |
| ORM / DB access | **SQLAlchemy** + **asyncpg** | Async data access and repositories |
| Migrations | **Alembic** | Every schema change, no exceptions |
| Database | **PostgreSQL 16** + **pgvector** | Primary store and vector store; no separate vector DB |
| Cache / state | **Redis 7** | Sessions, cache, rate limiting, future job queues |
| AI abstraction | `LLMProvider`, `EmbeddingProvider`, `Retriever`, `VectorStore` | Provider-agnostic AI layer |
| LLM | **Anthropic**, chat model **Opus 5** | Chat generation behind `LLMProvider` |
| Embeddings | `OpenAIEmbeddingProvider` (`text-embedding-3-small`, 1536) + `MockEmbeddingProvider` | Indexing and query embeddings; the mock keeps tests deterministic |
| AI orchestration | Service-based for the MVP | **LangChain** only where it clearly earns its place; **LangGraph** reserved for stateful agent workflows, not every chat request |
| Backend testing | **pytest**, **httpx** | Unit, DB integration, authorization and tenant-isolation regression tests |
| Frontend testing | **Vitest** + **React Testing Library**; **Playwright Test** for E2E | Vitest is the primary fast JS/TS runner; Playwright is the required E2E runner |
| Containers | **Docker** + **Docker Compose** | Full local stack |
| CI/CD | **GitHub Actions** (item 28) | Lint, type check, tests, E2E, build, deploy, health verification |

### The two AI providers

The MVP deliberately uses **two different vendors** behind one abstraction layer - this is a
provider split, not a redundancy:

| Role | Vendor | Model | Credential |
| --- | --- | --- | --- |
| Chat / generation | Anthropic | Opus 5 (API model string `claude-opus-5`) | `ANTHROPIC_API_KEY` |
| Embeddings | OpenAI | `text-embedding-3-small` (1536 dims) | `OPENAI_API_KEY` |
| Embeddings in tests | Mock | `MockEmbeddingProvider` (deterministic) | none |

### Provider rules

- No provider-specific code in routes or business services - always behind an interface.
- Providers stay stateless where possible.
- Anthropic is the chat provider only - **not** an embedding provider in this architecture.
- `MockEmbeddingProvider` must exist so the test suite never depends on a paid API.
- Do not introduce Jest unless an existing dependency requires it.

## Monetization

SaaS subscription. **Not in the MVP** - build plan items 37 (usage metering) and 38 (subscription
billing) are post-MVP, and the MVP requires no payment processing.

| Plan | Direction |
| --- | --- |
| **Free** | Limited AI usage, document storage, and organization/member capacity |
| **Pro** | Higher AI limits, more documents and storage, larger usage allowance, more advanced AI |
| **Team** | Shared organization workspace, more members, higher usage, team controls, usage analytics |
| **Enterprise** (future) | SSO/SAML, advanced RBAC, compliance controls, dedicated infrastructure, custom integrations and pricing |

Usage and AI cost metadata is still captured from the start via `AIRequestLog`, so billing can be
layered on later.

## UI/UX

A clean, professional AI workspace rather than a complex legacy CRM. Design principles: simple,
fast, trustworthy, AI-native, minimal clutter, strong hierarchy, clear feedback, accessible.

### Core navigation

```text
Dashboard
AI Chat
Knowledge
Companies
Contacts
Opportunities
Tasks
Settings
```

Navigation items appear only for features that are actually built.

### Routes

| Route | What's there |
| --- | --- |
| `/login`, `/register` | Authentication (feature 1) |
| `/dashboard` | Authenticated home and overview (feature 5) |
| `/chat` | Conversation list and the AI chat experience (features 15-17) |
| `/chat/[conversationId]` | A single persistent conversation |
| `/knowledge` | Document list, upload, processing status (features 11-12) |
| `/companies`, `/companies/[id]` | Company list and detail with timeline (feature 6) |
| `/contacts`, `/contacts/[id]` | Contact list and detail (feature 7) |
| `/opportunities`, `/opportunities/[id]` | Deal list and detail (feature 8) |
| `/tasks` | Task list, assignment, completion (feature 9) |
| `/settings` | Profile, organization config, members, invitations, preferences (features 2-4) |

### AI Chat

Chat is the central product experience. The user can start conversations, ask business questions,
watch responses stream, see the sources RAG used, inspect the underlying records and documents, and
continue previous conversations.

### Knowledge

Documents show filename, type, processing status, upload date, owner, organization, and the failure
state and reason when applicable.

### Global search

One unified, tenant-scoped search across business entities and knowledge, with result-type labels:

```text
Acme Corp             Company
John Smith            Contact
Enterprise Renewal    Opportunity
Onboarding Policy     Document
```

Knowledge matches show source/document context rather than exposing raw chunk internals. Global
search does not replace per-entity filters - entity-specific filtering stays in each domain UI.

### State feedback

Every asynchronous operation says what it is doing - uploading, processing, searching knowledge,
generating an answer.

### Errors

Never show internal exception names or stack traces. Use actionable messages ("We couldn't process
this document. Try again."); full technical detail goes to structured logs.

### Responsive and accessible

Desktop and laptop are first-class MVP targets; tablet stays usable; mobile focuses on core
workflows, with no separate native app (item 45 expands this). Accessibility is a baseline: semantic
HTML, keyboard navigation, visible focus states, accessible labels, adequate contrast,
screen-reader-friendly controls.

## Deployment

### Development

Docker Compose runs the whole stack; container names are `norma-*`. Legacy `fonio-*` identifiers are
removed only when safe and must not be reintroduced into new configuration.

| Service | Image / build | Host port |
| --- | --- | --- |
| `norma-postgres` | `pgvector/pgvector:pg16` | 5432 |
| `norma-redis` | `redis:7-alpine` | 6379 |
| `norma-api` | `./apps/api` | 8000 |
| `norma-web` | `./apps/web` | 3000 |

```text
Web:    http://localhost:3000
API:    http://localhost:8000
Docs:   http://localhost:8000/docs
Health: http://localhost:8000/api/v1/health
```

Inside Compose, services reach each other by service name (`postgres:5432`, `redis:6379`) - never
`localhost`. The Windows host may also run a native PostgreSQL; do not assume port 5432 belongs to
Docker without checking.

### Production target

This is the selected target, not a shortlist.

| Concern | Target |
| --- | --- |
| Frontend | **Vercel** - Next.js; build `npm run build`, start `npm run start`, `NEXT_PUBLIC_API_URL` required |
| Backend | **Render Docker Web Service** using the existing `apps/api/Dockerfile` |
| Production API command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` - never `--reload` |
| Database | **Render Managed PostgreSQL** - v16 or compatible, pgvector enabled via `CREATE EXTENSION vector;`, automated backups, secure connection |
| Redis | **Render Key Value**, via `REDIS_URL` |
| Document storage | **Amazon S3** from day one, behind an abstraction so another S3-compatible provider can replace it |
| Migrations | **Alembic** as a controlled pre-deploy step - never uncontrolled schema mutation on app startup |
| Health check | `GET /api/v1/health` |
| Domain | `norma.ai` / `api.norma.ai`, availability not assumed |

### Environment variables

Core (already in `.env.example`):

```text
APP_NAME  APP_VERSION  ENVIRONMENT  DEBUG
DATABASE_URL
POSTGRES_DB  POSTGRES_USER  POSTGRES_PASSWORD  POSTGRES_HOST  POSTGRES_PORT
REDIS_URL
SECRET_KEY
CORS_ORIGINS
NEXT_PUBLIC_API_URL
```

Also configured in code today: `API_V1_PREFIX`, `ACCESS_TOKEN_EXPIRE_MINUTES`,
`REFRESH_TOKEN_EXPIRE_DAYS`.

LLM - Anthropic chat (feature 18; `ANTHROPIC_API_KEY` already stubbed in `.env.example`):

```text
LLM_PROVIDER
LLM_MODEL
ANTHROPIC_API_KEY
ANTHROPIC_BASE_URL   (configurable)
```

Embeddings - OpenAI (feature 13; `OPENAI_API_KEY` already stubbed in `.env.example`):

```text
OPENAI_API_KEY
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
```

> ⚠️ `.env.example` currently spells the dimension variable `EMBEDDING_MODEL_DIMENSIONS`, while both
> plans say `EMBEDDING_DIMENSION`. One name has to win before feature 13. See Open questions.

Storage (feature 11):

```text
AWS_REGION  AWS_S3_BUCKET  AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY
```

Prefer workload identity / IAM roles over long-lived keys where the environment supports them.
Extend the repository's existing settings system rather than duplicating environment parsing, and
use variables the repo already defines before adding new ones. Secrets are never committed.

### Health checks

```text
GET /api/v1/health           basic liveness, used by the Render health check
GET /api/v1/health/database  PostgreSQL
GET /api/v1/health/redis     Redis
GET /api/v1/health/all       combined
```

These already exist. Keep them lightweight and safe to call; do not change their semantics without
updating deployment and monitoring expectations.

### Background workers

The MVP may begin with synchronous operations where safe. As document processing grows, add a Render
background worker backed by Redis for parsing, chunking, embeddings, large-file processing, and
long-running AI workflows (item 33) - when workload characteristics justify it, not before.

### CI/CD pipeline (item 28)

```text
Push -> Lint -> Type check -> Backend tests -> Frontend tests -> E2E tests
     -> Build -> Deploy -> Health verification
```

### Production security

HTTPS, secure secret handling, a strong `SECRET_KEY`, restrictive production CORS, rate limiting,
secure authentication, tenant isolation, authorization, audit logging, and object-storage access
controls.

## Open questions

> Resolve these in `project-plan.md` / `build-plan.md`, then re-run `/overview`.

1. **The two plans disagree about the LLM provider.** `build-plan.md` -> Planning decisions pins
   **Anthropic / Opus 5** with `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_API_KEY`, and a configurable
   `ANTHROPIC_BASE_URL`. But `project-plan.md` §5 -> LLM still reads "may initially use an
   **OpenAI-compatible** hosted provider selected by configuration," and §8's AI environment block
   lists only `OPENAI_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` - none of the four LLM
   variables. This overview follows the build plan as the newer, explicit decision. Update
   `project-plan.md` §5 and §8 to match, so the two plans stop contradicting each other.
2. **Embedding-dimension variable has two names.** `.env.example` defines
   `EMBEDDING_MODEL_DIMENSIONS`; both plans say `EMBEDDING_DIMENSION`. Pick one before feature 13
   writes the migration - a mismatch here is exactly the class of bug that produces a wrong
   `vector(N)` column.
3. **The chat model needs an exact API string.** "Opus 5" is the product name; the Anthropic API
   model string is `claude-opus-5`. Confirm that is what `LLM_MODEL` should default to, and record
   it in the plan so feature 18 does not have to guess.
4. **Citation artifacts in `project-plan.md` §8.** Several lines end in leftover web-citation markup
   such as `citeturn750913search1turn750913search3`. Cosmetic, but they render as noise; worth
   stripping on the next edit.

### Resolved since the last overview

- CI/CD now has an owning MVP item (28), so the MVP no longer depends on out-of-loop setup.
- The LLM environment variables are named in the build plan's Planning decisions.
- `.env.example` has picked up `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and the embedding variables.
- Inserting item 28 renumbered everything after it (deployment readiness 28 -> 29, release
  validation 29 -> 30, post-MVP 30-46 -> 31-47). Nothing was checked off yet, so no completed
  numbering was disturbed.

### Known plan-to-repo drift (expected, no action needed yet)

`.env.example` does not yet contain `LLM_PROVIDER`, `LLM_MODEL`, `ANTHROPIC_BASE_URL`, or the
`AWS_*` variables, and its AI values are all `replace-this-in-production` placeholders rather than
the decided defaults (`text-embedding-3-small`, `1536`). `docker-compose.yml` has no S3 or
local-storage adapter configuration. Features 11, 13, 18, and 27 close this gap.
