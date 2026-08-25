# CLAUDE.md

# Norma AI — Project Instructions

This file is the primary instruction and context document for AI coding agents working on the Norma AI repository.

Read this file before making code, architecture, schema, dependency, configuration, or infrastructure changes.

---

## 1. Project identity

- Product name: **Norma AI**
- Previous working name: **fonio-clone**
- Repository/project directory may still contain legacy `fonio-clone` names during the migration; do not rename files, containers, Docker volumes, service identifiers, package names, or URLs blindly.
- Product goal: build a production-grade AI business workspace that lets organizations manage business data, upload internal knowledge, and interact with that information through grounded AI conversations.

### Core product principle

> Talk to your business data and get work done.

Norma AI should provide a secure, multi-tenant conversational AI layer over business and organizational knowledge.

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

Do not redesign an existing subsystem merely because a different implementation would be theoretically cleaner. First inspect the current implementation, tests, migrations, dependency graph, and API behavior.

---

## 3. Product scope

Norma AI is initially a multi-tenant SaaS application for small and medium-sized teams.

Primary user groups include:

- Founders and business owners
- Sales teams
- Operations teams
- Customer-success/support teams
- Small teams that want AI-assisted access to internal business knowledge

The MVP focuses on:

- Authentication
- Organizations/workspaces
- RBAC
- Core CRM/business data
- Document ingestion
- Semantic search/RAG
- AI chat
- Sources/citations
- Prompt management
- AI guardrails
- Auditability
- Observability
- Production readiness

Do not expand the MVP into a general autonomous-agent platform unless the current roadmap/spec explicitly requires it.

---

## 4. Repository structure

Expected high-level structure:

```text
Norma AI/
├── apps/
│   ├── api/                # FastAPI backend
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

Do not assume every directory currently exists exactly as shown. Inspect the repository before changing structure.

---

# 5. Architecture

## 5.1 High-level system

```text
                    ┌─────────────────────┐
                    │      Browser        │
                    │     Next.js Web     │
                    └──────────┬──────────┘
                               │
                         HTTP / API
                               │
                    ┌──────────▼──────────┐
                    │      FastAPI        │
                    │       API           │
                    └───────┬────┬────────┘
                            │    │
                  ┌─────────┘    └──────────┐
                  ▼                         ▼
        ┌─────────────────┐       ┌─────────────────┐
        │ PostgreSQL      │       │      Redis       │
        │ + pgvector      │       │ cache/state/jobs │
        └────────┬────────┘       └─────────────────┘
                 │
                 ▼
        ┌─────────────────┐
        │ Knowledge / RAG  │
        │ parsing → chunks │
        │ → embeddings →   │
        │ retrieval → LLM  │
        └─────────────────┘
```

The architecture should remain modular, testable, and provider-agnostic.

---

## 5.2 Frontend

Primary frontend stack:

- Next.js
- React
- TypeScript
- Tailwind CSS
- shadcn/ui or existing repository UI primitives

Frontend responsibilities:

- Authentication screens
- Organization/workspace experience
- CRM screens
- Knowledge/document management
- AI chat
- Settings
- Error/loading/empty states

Do not put business-critical authorization logic only in the frontend. Backend authorization is mandatory.

---

## 5.3 Backend

Primary backend stack:

- Python 3.12+
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic
- asyncpg
- Redis client
- pytest
- httpx

Expected backend layering:

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

AI-related flows may additionally use:

```text
API
 ↓
AI orchestration/service
 ↓
retrieval / prompt / guardrail / tool layers
 ↓
LLM provider
```

Avoid putting complex business logic directly inside route functions.

---

# 6. Database conventions

## 6.1 PostgreSQL

Primary database:

- PostgreSQL 16
- pgvector enabled

The database is the system of record for structured application data.

Do not introduce another database without a clear feature requirement and explicit architectural justification.

---

## 6.2 Migrations

All schema changes must use Alembic.

Never make manual production schema changes that are not represented by a migration.

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

Verify the migration on a clean database when practical.

Never casually delete or rewrite historical migrations that may already be referenced by environments.

---

## 6.3 Tenant isolation

Norma AI is multi-tenant.

Every organization-owned resource must be scoped to the correct organization/tenant.

A request must never be able to retrieve, modify, or delete records from another organization.

Tenant isolation must be enforced server-side.

Prefer existing repository/service tenant-scoping mechanisms instead of implementing ad-hoc filters in every route.

When adding a new organization-owned model:

- Include the organization/tenant ownership relationship required by the existing architecture.
- Add tenant-aware repository access.
- Add authorization tests.
- Add cross-tenant negative tests.

---

## 6.4 PostgreSQL vectors

Use pgvector for MVP semantic retrieval.

Do not introduce Pinecone, Chroma, Weaviate, or another external vector database unless a later architectural decision explicitly requires it.

When working with embeddings:

- Keep vector dimensions configurable.
- Ensure the configured vector dimension matches the actual provider output.
- Test dimension compatibility before writing vectors.
- Do not silently truncate or pad embeddings.

---

# 7. Authentication and authorization

Authentication must be secure and centralized.

Expected capabilities:

- Registration
- Login
- Logout
- Access token handling
- Refresh/session handling
- Password hashing
- User identity
- Organization membership

Authorization should be explicit.

Typical organization roles:

```text
Owner
Admin
Member
Viewer
```

Do not assume a role implies every permission. Use the existing permission model when present.

Every protected endpoint should establish:

1. Who is the user?
2. Which organization/context are they operating in?
3. Are they a member of that organization?
4. Are they authorized for the requested operation?
5. Does the target resource belong to that organization?

---

# 8. AI architecture

## 8.1 Provider abstraction

Keep provider-specific code behind interfaces/abstractions.

The application should be able to change LLM or embedding providers without rewriting the application layer.

Preferred conceptual interfaces:

```text
LLMProvider
EmbeddingProvider
Retriever
VectorStore
```

Providers should remain stateless where possible.

Do not hard-code a model/provider inside a route or business service.

---

## 8.2 RAG pipeline

Norma AI's knowledge pipeline should follow:

```text
Upload
  ↓
Document record
  ↓
Parser
  ↓
Normalized text
  ↓
Chunker
  ↓
Embedding provider
  ↓
pgvector
  ↓
Retriever
  ↓
Context builder
  ↓
Prompt
  ↓
LLM
  ↓
Grounded response + sources
```

Supported document types in the MVP include:

- PDF
- DOCX
- Markdown
- TXT

When changing parsing/chunking behavior, preserve document-processing status and failure visibility.

---

## 8.3 Retrieval quality

Retrieval is a product-critical path.

When changing retrieval:

- Keep tenant filtering mandatory.
- Preserve document/chunk metadata.
- Measure retrieval quality where practical.
- Avoid unnecessarily increasing context size.
- Preserve source traceability.
- Add regression tests for retrieval behavior.
- Ensure vector dimensions match the configured embedding model.

If a retrieval bug appears, inspect the full path:

```text
embedding configuration
→ stored vector dimension
→ query embedding dimension
→ database vector column
→ similarity query
→ filters
→ returned chunks
```

Do not fix vector-dimension errors by randomly changing the database column dimension. Verify the configured embedding provider first.

---

# 9. AI chat

The chat system should support:

- Persistent conversations
- Persistent messages
- Streaming responses
- Conversation context
- RAG retrieval when relevant
- Sources/citations
- Usage metadata when available
- Provider abstraction
- Guardrails

The AI response should not claim to know information that was not provided by the available context when the feature is intended to be grounded.

Streaming must remain compatible with the existing API contract.

---

# 10. Prompt management

Prompt templates should be versioned when the repository supports prompt management.

Do not embed large production prompts directly inside route handlers.

Prompt changes can materially change product behavior. Treat prompt changes like code changes:

- Review them.
- Test them.
- Preserve versions when the architecture requires it.
- Avoid silently replacing production prompt versions.

---

# 11. AI guardrails and safety

AI output is not automatically trusted.

Use a dedicated guardrail layer for applicable workflows.

Guardrail responsibilities may include:

- Input validation
- Prompt-injection defenses
- Output validation
- Sensitive information protection
- Action authorization
- Tool permission checks
- Unsafe operation blocking

For future agents/tools:

```text
User request
   ↓
Authorization
   ↓
Policy / guardrail check
   ↓
Tool selection
   ↓
Tool execution
   ↓
Result validation
```

Never give an AI agent unrestricted destructive access to the database or external systems.

High-risk operations should require explicit authorization and, where appropriate, human confirmation.

---

# 12. CRM/business entities

The MVP includes:

- Companies
- Contacts
- Opportunities
- Tasks
- Notes
- Activity history

Follow existing naming and relationship conventions.

When adding a new CRM entity:

- Define ownership/organization scope.
- Define repository methods.
- Define service rules.
- Define API schemas.
- Define routes.
- Add authorization tests.
- Add migration.
- Add frontend support only after backend behavior is stable.

Avoid prematurely creating a generic "everything table" when specific domain entities are needed.

---

# 13. API design

Use the existing API versioning convention, currently expected to be:

```text
/api/v1/...
```

Prefer RESTful resources with clear names.

Examples:

```text
GET    /api/v1/companies
POST   /api/v1/companies
GET    /api/v1/companies/{id}
PATCH  /api/v1/companies/{id}
DELETE /api/v1/companies/{id}
```

For specialized operations, use explicit action endpoints only when they provide a clear domain boundary.

Every API change should consider:

- Authentication
- Authorization
- Validation
- Error responses
- Tenant isolation
- Tests
- Documentation/OpenAPI impact

---

# 14. API health endpoints

Existing health architecture includes:

```text
GET /api/v1/health
GET /api/v1/health/database
GET /api/v1/health/redis
GET /api/v1/health/all
```

Do not remove or change the semantics of health endpoints without updating deployment/monitoring expectations.

Health endpoints should remain lightweight and safe to call.

---

# 15. Redis

Redis is used for:

- Session/refresh state where applicable
- Caching
- Rate limiting
- Temporary coordination
- Future background job infrastructure

Do not use Redis as the primary source of truth for durable business records.

Cache invalidation must be considered when changing persistent data that is cached.

---

# 16. File and document storage

Development can use local storage where already supported.

Production should use object storage such as S3-compatible storage.

Store metadata and references in PostgreSQL.

Do not store large binary documents directly in PostgreSQL unless explicitly required by the current architecture.

Document processing should expose:

```text
pending
processing
completed/ready
failed
```

or the exact statuses already implemented by the repository.

Failures must be inspectable and retryable where the feature requires it.

---

# 17. Docker and local development

Expected development services:

```text
norma-postgres
norma-redis
norma-api
norma-web
```

The current repository may still use legacy service/container names such as:

```text
fonio-postgres
fonio-redis
fonio-api
fonio-web
```

Do not rename these merely because of the brand change if the repository currently relies on them. Rename them deliberately as part of a scoped project-identity migration.

### Docker networking rule

Inside Docker Compose:

```text
API → postgres:5432
API → redis:6379
```

From the host machine:

```text
API → localhost:5432
API → localhost:6379
Browser → localhost:8000
Browser → localhost:3000
```

Do not use `localhost` for inter-container communication.

---

# 18. Environment variables

Local secrets belong in `.env`.

The repository should provide `.env.example` containing placeholders and safe development defaults where appropriate.

Never commit:

- API keys
- Passwords intended for production
- JWT secrets
- Cloud credentials
- Database credentials for production
- Private tokens

Production secrets must come from the deployment platform's secret management.

Important configuration categories include:

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

AI provider credentials
```

Use the exact variables already defined by the repository before adding duplicates.

---

# 19. Frontend conventions

Prefer:

- Reusable components
- Typed API contracts
- Clear loading states
- Clear empty states
- Clear error states
- Accessible forms
- Server-side authorization where required
- Consistent UI primitives

Avoid:

- Large monolithic components
- Repeated API-fetching logic
- Hard-coded production URLs
- Client-only security checks
- Silent error swallowing

AI chat UX should make the state obvious:

```text
Searching knowledge...
Generating response...
```

and should expose sources when a response uses retrieval.

---

# 20. UI/UX direction

Norma AI should feel:

- Clean
- Professional
- Modern
- Calm
- Trustworthy
- AI-native
- Fast

Avoid an overly flashy "AI demo" aesthetic.

The primary experience should make the user's work feel simpler, not more complicated.

Core navigation is expected to include:

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

Do not add navigation items for incomplete features.

---

# 21. Error handling

User-facing errors must be understandable.

Do not expose internal exceptions such as:

```text
SQLAlchemy IntegrityError
asyncpg.exceptions...
Traceback...
```

Instead, return appropriate API errors and display user-friendly messages.

Keep full technical details in logs.

Never swallow exceptions silently.

Use structured logging where the existing application supports it.

---

# 22. Logging and observability

Important production signals include:

- Request latency
- API errors
- AI latency
- Model/provider failures
- Retrieval failures
- Document-processing failures
- Token usage where available
- Database failures
- Redis failures
- Authorization failures
- Important security events

Avoid logging:

- Passwords
- API keys
- Authentication tokens
- Full sensitive document contents
- Sensitive customer data without a justified operational need

---

# 23. Testing rules

Every meaningful feature should include tests at the appropriate layer.

Minimum expectations:

### Unit tests

For:

- Pure business logic
- Parsers
- Chunkers
- Provider adapters
- Guardrail logic
- Utility functions

### Integration tests

For:

- Database operations
- Repositories
- RAG retrieval
- Service/database interaction
- Authentication flows where applicable

### API tests

For:

- Success paths
- Validation failures
- Unauthorized access
- Forbidden access
- Not-found behavior
- Cross-tenant access attempts

### AI tests

Prefer deterministic providers/mocks for tests.

Do not make the entire test suite depend on paid external model APIs.

Test:

- Prompt construction
- Retrieval
- Provider abstraction
- Streaming behavior
- Guardrails
- Failure handling

### Regression principle

Whenever a bug is fixed:

1. Reproduce it with a test.
2. Fix the implementation.
3. Keep the regression test permanently.

---

# 24. Code quality

Prefer:

- Small focused functions
- Explicit types
- Clear naming
- Dependency injection where appropriate
- Reusable services/repositories
- Consistent error handling
- Minimal duplication

Avoid premature abstraction.

A new abstraction should solve a real repeated concern or protect an important architectural boundary.

Do not add dependencies merely because they are fashionable.

---

# 25. Git and change discipline

Before making a change:

1. Inspect the current implementation.
2. Identify affected files.
3. Check related tests.
4. Check migrations/configuration if applicable.
5. Make the smallest coherent change.
6. Run targeted tests.
7. Run broader tests where appropriate.
8. Review the diff.

Do not silently change unrelated code.

Do not rewrite working architecture merely to match a preferred style.

Do not remove tests to make a feature pass.

---

# 26. Feature implementation workflow

Use the project's planning workflow:

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

The build plan is a living progress tracker.

Do not renumber completed features.

When a new feature materially changes:

- Product direction
- Users
- Data model
- Technology stack
- Monetization
- UI/UX
- Deployment

update `project-plan.md` and `build-plan.md` accordingly before implementing the feature.

---

# 27. Working with feature specifications

When implementing a feature from a spec:

1. Read the spec completely.
2. Inspect the current codebase.
3. Identify existing reusable components.
4. Do not duplicate existing services/models/repositories.
5. Implement the smallest complete slice.
6. Add migrations where needed.
7. Add tests.
8. Run existing tests to detect regressions.
9. Verify API and UI behavior.
10. Update documentation if the architecture changed.

If the spec conflicts with the current implementation, stop and reconcile the difference based on the project plans and repository state rather than silently choosing one.

---

# 28. Database safety

Never run destructive commands against the development database unless explicitly required.

Examples that require extreme caution:

```bash
docker compose down -v
DROP DATABASE
DROP TABLE
TRUNCATE
alembic downgrade base
```

Do not recommend deleting Docker volumes as the first troubleshooting step.

If data persistence is working, preserve it.

For migration problems, inspect first.

---

# 29. Troubleshooting methodology

Use evidence-driven debugging.

Preferred order:

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

For Docker issues, inspect:

```bash
docker compose config
docker compose ps
docker compose logs <service>
docker inspect <container>
```

For database issues, inspect:

```bash
alembic current
alembic heads
alembic history
```

and the actual PostgreSQL state.

---

# 30. Current known infrastructure behavior

The development environment uses:

```text
PostgreSQL:
pgvector/pgvector:pg16
host port: 5432

Redis:
redis:7-alpine
host port: 6379

FastAPI:
host port: 8000

Next.js:
host port: 3000
```

The Windows machine may have a separate native PostgreSQL installation.

Do not assume that `5432` belongs to Docker without checking.

Docker PostgreSQL is the intended database for this project.

---

# 31. Legacy Fonio naming migration

The project was initially created as a Fonio clone and is now called Norma AI.

Known legacy names may remain in:

- Docker container names
- Docker Compose project name
- Volume names
- Environment variables
- Documentation
- URLs
- Directory names
- Package metadata

Migration should be handled incrementally.

When changing a legacy identifier, search the repository first:

```bash
git grep -n "fonio"
```

or equivalent platform-specific search.

Review every match before changing it.

Do not alter third-party/internal identifiers that are intentionally tied to an existing migration or dependency without understanding their impact.

The product-facing branding should use **Norma AI**.

---

# 32. Security rules

Never:

- Commit secrets
- Log secrets
- Trust client-provided organization IDs without authorization
- Trust client-provided role claims without verification
- Let an LLM bypass authorization
- Allow tools to execute arbitrary SQL
- Allow unrestricted shell execution from user-controlled AI prompts
- Expose internal stack traces to users
- Disable tenant filtering to "make a query work"

Always treat user input, retrieved documents, and model output as untrusted data.

---

# 33. AI-specific security

Documents can contain prompt injection.

Therefore:

- Retrieved text is data, not instructions.
- System/application instructions must remain higher priority than document content.
- Do not let retrieved documents redefine authorization.
- Do not allow retrieved text to grant tool permissions.
- Do not allow a document to instruct the system to expose secrets or other tenants' data.

For AI-generated actions:

```text
User intent
   ↓
Permission check
   ↓
Policy/guardrail
   ↓
Tool execution
   ↓
Validation
   ↓
Audit log
```

---

# 34. Performance principles

MVP optimization priorities:

1. Correctness
2. Security
3. Reliability
4. Observability
5. Maintainability
6. Performance optimization

Do not prematurely optimize low-value paths.

For AI/RAG:

- Avoid unnecessarily large prompts.
- Keep retrieved context bounded.
- Prefer relevant chunks over many chunks.
- Cache appropriate repeated work.
- Move expensive document processing to background jobs as scale increases.

---

# 35. Deployment principles

Production should use:

- HTTPS
- Managed PostgreSQL
- Redis
- Object storage for documents
- Containerized FastAPI
- Hosted Next.js
- Secret management
- Automated migrations
- Health checks
- CI/CD
- Monitoring/error tracking

Do not introduce Kubernetes or a microservice architecture unless actual scale/operational requirements justify it.

---

# 36. Definition of done

A feature is not complete merely because the code exists.

A feature should normally satisfy:

- Requirements implemented
- Existing architecture respected
- Tenant isolation verified
- Authorization verified
- Input validation implemented
- Error paths considered
- Tests added/updated
- Migration added if required
- API/UI behavior verified
- No unrelated regressions
- Documentation updated when appropriate

For AI features, additionally verify:

- Provider failures
- Empty retrieval results
- Malformed model output
- Guardrail failures
- Streaming interruption
- Token/usage handling where applicable
- Prompt-injection scenarios where relevant

---

# 37. What not to do

Do not:

- Rewrite the project from scratch.
- Replace PostgreSQL with another database without a requirement.
- Replace pgvector with another vector database without a requirement.
- Add a new framework when existing dependencies solve the problem.
- Introduce microservices prematurely.
- Add an AI agent just because an LLM is involved.
- Put business logic in frontend-only code.
- Put authorization only in the frontend.
- bypass migrations.
- Delete the database to solve application bugs.
- Remove failing tests instead of fixing the implementation.
- Guess about current code behavior without inspecting it.
- Implement against an outdated conversation snapshot when repository code differs.

---

# 38. Preferred implementation style

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
→ model-specific code
→ no tests
```

Use explicit boundaries that make future provider changes and testing straightforward.

---

# 39. Expected next development direction

The project should continue according to `build-plan.md`.

The first unchecked feature is the next intended implementation target unless a feature is explicitly selected by number/name.

After a feature is completed:

1. Run tests.
2. Verify the implementation.
3. Mark the feature checked in `build-plan.md`.
4. Preserve historical numbering.
5. Continue with the next unchecked feature.

---

# 40. Final instruction to AI coding agents

Before changing code, ask:

- What existing implementation handles this already?
- What organization/tenant boundary applies?
- What authorization rules apply?
- What database/schema changes are required?
- What APIs change?
- What frontend behavior changes?
- What AI/provider dependencies are involved?
- What tests will prove the feature works?
- What failure modes could break production?
- Does this change affect `project-plan.md` or `build-plan.md`?

Prefer the smallest production-quality change that fits the existing Norma AI architecture.

When uncertain, inspect the repository and tests before making assumptions.

**Repository code is the source of truth. Security and tenant isolation are non-negotiable.**
