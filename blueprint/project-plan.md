# Project Plan

> One of the two planning docs for **Norma AI**. This document defines the product problem, target users, MVP scope, data model, technology choices, monetization, UI/UX direction, and deployment requirements. It is the product-level source of truth for feature planning.

## 1. Problem - What problem are we solving?

**Norma AI** is an AI-powered business productivity and knowledge workspace that helps teams understand and work with their business data through a natural-language interface.

Business information is commonly fragmented across customer records, opportunities, tasks, notes, internal documents, policies, and other knowledge sources. Users spend time searching multiple systems, manually assembling context, and repeating routine work.

Norma AI provides a single workspace where an organization can:

- Manage core business information.
- Upload and organize internal knowledge.
- Search business records and documents.
- Ask natural-language questions about organizational data.
- Receive AI answers grounded in retrieved organization-owned information.
- See the sources behind knowledge-based answers.
- Maintain conversation history and context.
- Progress toward AI-assisted business actions without bypassing authorization.

### Core product idea

> **Talk to your business data and get work done.**

### Primary MVP goal

The MVP must prove the complete secure workflow:

```text
User
  ↓
Authentication
  ↓
Organization
  ↓
Business data + documents
  ↓
Knowledge indexing
  ↓
AI chat
  ↓
Tenant-scoped retrieval
  ↓
Grounded response + sources
```

The initial release prioritizes correctness, security, tenant isolation, retrieval quality, observability, and a maintainable provider-agnostic AI architecture.

### Product constraints

- Organization data must be isolated by tenant.
- Backend authorization is mandatory; frontend checks are not security boundaries.
- AI-generated responses are untrusted until validated by application policy.
- Retrieved document text is data, not instructions.
- AI tools/actions must never bypass user permissions.
- LLM and embedding providers must remain replaceable through application abstractions.
- PostgreSQL + pgvector is the MVP system of record and vector store.
- Production document storage uses S3-compatible object storage from the first production deployment.
- The MVP should remain simple enough to operate without premature microservices or Kubernetes.

### Explicit MVP exclusions

The MVP does not include:

- Fully autonomous unrestricted agents.
- Arbitrary SQL/tool execution by AI.
- Hundreds of third-party integrations.
- Enterprise SSO/SAML.
- Complex enterprise billing infrastructure.
- Native mobile applications.
- Multi-region active-active deployment.
- Real-time collaborative document editing.
- Unrestricted destructive AI actions.
- A separate dedicated vector database.

---

## 2. Users - Who is this for?

### Primary users

Norma AI targets small and medium-sized businesses and their teams.

### Target user groups

#### Business owners and founders

Need quick answers about customers, sales, operations, documents, and business performance.

Example:

> "Which open opportunities are worth more than ₹5 lakh?"

#### Sales teams

Need customer context, account research, opportunity summaries, and follow-up support.

Example:

> "Summarize everything we know about Acme Corp before my meeting."

#### Operations teams

Need quick access to processes, internal documentation, tasks, and operational records.

Example:

> "What is our enterprise onboarding process?"

#### Customer-success and support teams

Need customer context and fast retrieval of internal knowledge.

Example:

> "What problems has this customer reported recently?"

### Organization model

The initial authorization model is:

```text
Organization
├── Owner
├── Admin
├── Member
└── Viewer
```

Users may eventually belong to multiple organizations.

---

## 3. Features - What does the MVP need?

### Authentication and accounts

- User registration and login.
- Secure password hashing.
- Access/refresh session management.
- Logout and session invalidation.
- User profile management.

### Organizations

- Organization creation.
- Organization membership.
- Organization invitations.
- Organization switching.
- Organization settings.
- Tenant isolation.

### RBAC

- Owner, Admin, Member, and Viewer roles.
- Permission checks on protected resources.
- Organization-scoped authorization.
- Cross-tenant access prevention.

### Core business data

- Companies.
- Contacts.
- Opportunities.
- Tasks.
- Notes.
- User-facing activity timelines.

### Knowledge base

- PDF, DOCX, Markdown, and TXT uploads.
- Document metadata.
- Document processing states.
- Parsing and normalization.
- Chunking.
- Embedding generation.
- Vector indexing.
- Semantic retrieval.
- Source traceability.

### AI

- Streaming AI chat.
- Persistent conversations.
- Grounded RAG responses.
- Sources/citations.
- Configurable LLM provider.
- Configurable embedding provider.
- Prompt templates and versions.
- AI guardrails.
- AI/retrieval observability.

### Search

Norma AI will provide **one global search experience** that can search across the core CRM/business entities and the organization knowledge base. It is not a replacement for per-entity filters; entity-specific filtering remains part of each domain UI.

The global search must remain tenant-scoped and return typed results so users can identify whether a match is a company, contact, opportunity, task, note, document, or knowledge chunk/source.

### Auditability

- Security/administrative audit logs.
- User-facing activity timelines.
- These are deliberately distinct:
  - **Activity** = business/user-facing timeline of meaningful CRM events.
  - **AuditLog** = security/compliance-oriented record of important system actions.

### Platform

- REST API.
- API versioning.
- Health checks.
- Structured logging.
- Automated tests.
- Dockerized development.
- Alembic migrations.
- Production configuration and deployment.

---

## 4. Data - What are we storing?

### Identity

- Users.
- Email addresses.
- Password hashes.
- Profile information.
- Account status.
- Authentication/session metadata.

### Organizations

- Organizations.
- Organization settings.
- Organization members.
- Roles.
- Invitations.

### CRM/business records

- Companies.
- Contacts.
- Opportunities.
- Opportunity stages and values.
- Tasks.
- Notes.
- User-facing activities.

### Conversations

- Conversations.
- Conversation titles.
- Organization ownership.
- Messages.
- Message roles.
- Timestamps.
- Model/provider metadata where appropriate.
- Usage metadata where available.

### Knowledge

- Knowledge documents.
- Filenames.
- MIME/type metadata.
- Organization ownership.
- Processing status.
- Processing errors.
- Source/file references.
- Document chunks.
- Chunk ordering/metadata.
- Embeddings.

### Vector configuration

The MVP embedding provider is **OpenAI `text-embedding-3-small`** through the `EmbeddingProvider` abstraction.

The initial production vector dimension is:

```text
1536
```

The PostgreSQL column for document embeddings will therefore use:

```text
vector(1536)
```

The chosen model/provider and dimension are part of the RAG contract. Changing either later requires an explicit migration/reindex strategy; embeddings from incompatible dimensions must not be mixed in the same vector column.

A `MockEmbeddingProvider` will be used for deterministic tests and local logic tests where external embedding calls are undesirable.

### AI/prompt data

- Prompt templates.
- Prompt versions.
- Prompt metadata.
- AI configuration metadata where needed.

Provider secrets/API keys are configuration secrets, not ordinary application records.

### Audit/observability data

- Audit events.
- Authentication/security events.
- Retrieval traces.
- Retrieved chunk references.
- Model/provider metadata.
- AI latency/usage metrics where available.
- Document-processing events.
- Operational errors.

Sensitive content should only be retained when required for product behavior, debugging, or auditability.

### Object storage

**Production document binaries use S3-compatible object storage from the first production deployment.**

The MVP stores document metadata/references in PostgreSQL and document binaries in object storage.

The initial target is **Amazon S3**. The application should use an abstraction so another S3-compatible provider can be adopted later.

Development may continue to use a local filesystem adapter where already implemented, but production must not depend on container-local disk because production app filesystems may be ephemeral.

---

## 5. Tech - What stack are we using?

### Frontend

- Next.js.
- React.
- TypeScript.
- Tailwind CSS.
- shadcn/ui or the repository's existing UI primitives.

### Backend

- Python 3.12+.
- FastAPI.
- Pydantic.
- SQLAlchemy.
- asyncpg.
- Alembic.
- Redis client.
- pytest.
- httpx.

### Database

- PostgreSQL 16.
- pgvector.

The MVP uses PostgreSQL + pgvector instead of a separate vector database.

### Cache/state

- Redis 7.

### LLM

The LLM layer is provider-agnostic.

The application uses an `LLMProvider` abstraction and may initially use an Anthropic-compatible hosted provider selected by configuration. The exact chat model is configuration, not an architectural dependency.

### Embeddings

The production MVP embedding implementation is:

```text
EmbeddingProvider
    ├── OpenAIEmbeddingProvider
    │      └── text-embedding-3-small
    │            └── 1536 dimensions
    └── MockEmbeddingProvider
           └── deterministic tests/local test scenarios
```

Anthropic is **not** treated as an embedding provider.

Environment/configuration for the OpenAI embedding provider uses conventional variables such as:

```text
OPENAI_API_KEY
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
```

The implementation should follow the repository's existing settings/configuration system rather than duplicating environment parsing.

### AI orchestration

- Service-based AI orchestration for the MVP.
- LangChain may be used where it provides clear value for model/retrieval abstractions.
- LangGraph is reserved for explicit stateful agent/workflow use cases rather than every chat request.

### RAG

```text
Upload
 ↓
Document
 ↓
Parser
 ↓
Normalized text
 ↓
Chunker
 ↓
OpenAI embedding
 ↓
pgvector(1536)
 ↓
Tenant-scoped retrieval
 ↓
Context builder
 ↓
LLM
 ↓
Grounded answer + sources
```

### AI guardrails

Use a dedicated guardrail layer for:

- Input checks.
- Prompt-injection defenses.
- Output validation.
- Sensitive-data protections.
- Tool permission checks.
- Policy enforcement.

### Frontend testing

The frontend testing strategy is:

- **Vitest** for unit tests and fast component-level tests.
- **React Testing Library** for React component behavior and accessibility-oriented assertions.
- **Playwright Test** for end-to-end browser flows.

Playwright is the required E2E runner. Vitest is the primary fast JavaScript/TypeScript test runner. The project should not introduce Jest unless an existing dependency requires it.

### Backend testing

- pytest.
- httpx.
- Database integration tests.
- Deterministic AI provider mocks.
- Authorization and tenant-isolation regression tests.

### Infrastructure

- Docker.
- Docker Compose for local development.
- GitHub Actions for CI/CD.

### Development container services

Current project container naming is **Norma AI** terminology:

```text
norma-postgres
norma-redis
norma-api
norma-web
```

Legacy `fonio-*` identifiers should be removed only when safe to do so and should not be reintroduced into new configuration.

### Local networking

Inside Docker:

```text
API → postgres:5432
API → redis:6379
```

From the host/browser:

```text
API → localhost:8000
Web → localhost:3000
```

---

## 6. Monetize - How will this make money?

Norma AI will use a SaaS subscription model.

### Free

- Limited AI usage.
- Limited document storage.
- Limited organization/member capacity.

### Pro

- Higher AI limits.
- More documents/storage.
- Larger usage allowance.
- More advanced AI capabilities.

### Team

- Shared organization workspace.
- More members.
- Higher usage.
- Team-oriented controls.
- Usage analytics.

### Enterprise

Future:

- SSO/SAML.
- Advanced RBAC.
- Compliance/security controls.
- Dedicated infrastructure.
- Custom integrations.
- Custom pricing.

The MVP does not require complete payment processing. Usage and AI cost metadata should still be captured so billing can be added later.

---

## 7. UI/UX - How should this look and feel?

Norma AI should feel like a clean, professional AI workspace rather than a complex legacy CRM.

### Design principles

- Simple.
- Fast.
- Trustworthy.
- AI-native.
- Minimal visual clutter.
- Strong hierarchy.
- Clear feedback.
- Accessible.

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

### AI Chat

Chat is the central product experience.

The user should be able to:

- Start conversations.
- Ask business questions.
- See streaming responses.
- See sources used by RAG.
- Inspect relevant records/documents.
- Continue previous conversations.

### Knowledge

Documents should clearly show:

- Filename.
- Type.
- Processing status.
- Upload date.
- Owner.
- Organization.
- Failure state/reason when applicable.

### CRM

CRM screens should support:

- Search.
- Filters.
- Tables.
- Detail views.
- Create/edit forms.
- Activity timelines.

### Global search

Global search should present a unified search experience with result-type labels, for example:

```text
Acme Corp             Company
John Smith            Contact
Enterprise Renewal    Opportunity
Onboarding Policy     Document
```

Knowledge matches should show source/document context rather than exposing raw chunk internals.

### Error UX

Never show internal exception names or stack traces to users.

Use actionable messages such as:

> "We couldn't process this document. Try again."

### Responsive behavior

Desktop and laptop are first-class MVP targets. Tablet should remain usable. Mobile support should focus on core workflows rather than a separate native app.

### Accessibility

Use:

- Semantic HTML.
- Keyboard navigation.
- Visible focus states.
- Accessible labels.
- Appropriate contrast.
- Screen-reader-friendly controls.

---

## 8. Deployment - Where and how will this ship?

### Development

Docker Compose runs:

```text
norma-postgres
norma-redis
norma-api
norma-web
```

Local endpoints:

```text
Web:
http://localhost:3000

API:
http://localhost:8000

API docs:
http://localhost:8000/docs

Health:
http://localhost:8000/api/v1/health
```

### Production deployment target

**Frontend: Vercel**

**Backend: Render Docker Web Service**

**Database: Render Managed PostgreSQL with pgvector**

**Redis: Render Key Value**

**Document storage: Amazon S3**

This is the selected MVP production target rather than a list of competing providers. Render supports Docker-based web services and managed Postgres/Redis-compatible infrastructure, and Render Postgres supports pgvector. citeturn750913search1turn750913search3turn648910search0

### Frontend deployment

Host:

```text
Vercel
```

Application:

```text
Next.js
```

Production build:

```text
npm run build
```

Production start:

```text
npm run start
```

Exact deployment behavior should follow Vercel's standard Next.js deployment path.

Required frontend configuration includes:

```text
NEXT_PUBLIC_API_URL
```

### Backend deployment

Host:

```text
Render
```

Runtime:

```text
Docker
```

The backend should use the existing `apps/api/Dockerfile`.

Production command:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The production service must not use `--reload`.

Render web services support Docker-based deployments and require the application to listen on an externally reachable port; Render also supports configurable health checks and environment variables. citeturn750913search1turn750913search6

### Database

Host:

```text
Render Postgres
```

Required:

- PostgreSQL 16 or a compatible supported version.
- pgvector enabled.
- Automated backups on the selected production plan.
- Secure connection.
- Alembic migrations.

Render Postgres supports `pgvector` and enables it with `CREATE EXTENSION vector;`. citeturn648910search0turn648910search1

### Redis

Host:

```text
Render Key Value
```

Use:

```text
REDIS_URL
```

for application connectivity.

### Object storage

Production document storage is **S3 from day one**.

Required configuration:

```text
AWS_REGION
AWS_S3_BUCKET
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

Prefer workload identity/IAM roles where the production environment supports them rather than long-lived keys.

The application must not depend on local container disk for durable uploaded documents.

### Environment variables

Core:

```text
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
```

AI:

```text
OPENAI_API_KEY
EMBEDDING_MODEL
EMBEDDING_DIMENSION
```

Storage:

```text
AWS_REGION
AWS_S3_BUCKET
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

Use the exact settings implementation already present in the repository before adding new variables.

### Migrations

Database migrations use:

```text
Alembic
```

Production migration execution should be a controlled deploy/pre-deploy step rather than an uncontrolled schema mutation during every application startup. Render supports pre-deploy commands suitable for tasks such as database migrations on eligible paid services. citeturn750913search5

### Health checks

The deployment should use:

```text
GET /api/v1/health
```

for basic service health.

Additional dependency checks remain:

```text
GET /api/v1/health/database
GET /api/v1/health/redis
GET /api/v1/health/all
```

### Background workers

The MVP may begin with synchronous operations where safe.

As document processing grows, add a Render background worker backed by Redis for:

- Document parsing.
- Chunking.
- Embeddings.
- Large file processing.
- Long-running AI workflows.

This should be added when workload characteristics justify it, not before.

### Domain

Target production domains:

```text
norma.ai
api.norma.ai
```

Exact domain availability is not assumed.

### Production security

Required:

- HTTPS.
- Secure secret handling.
- Strong `SECRET_KEY`.
- Restrictive production CORS.
- Rate limiting.
- Secure authentication.
- Tenant isolation.
- Authorization.
- Audit logging.
- Object-storage access controls.

### CI/CD

Target pipeline:

```text
Push
 ↓
Lint
 ↓
Type check
 ↓
Backend tests
 ↓
Frontend tests
 ↓
E2E tests
 ↓
Build
 ↓
Deploy
 ↓
Health verification
```

### MVP production decision

The first production deployment **does not** use local filesystem document persistence. S3-compatible object storage is part of the first production release.

Therefore the old roadmap item "Object storage migration" is no longer a migration from local storage to production storage. It has been converted into a later hardening/optimization item instead.
