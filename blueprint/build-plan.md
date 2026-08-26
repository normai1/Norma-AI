# Build Plan

> One of the two planning docs for **Norma AI**. This is the living feature roadmap and progress tracker. Items stay intentionally high-level; detailed architecture, API contracts, schemas, edge cases, and implementation decisions belong in feature specifications.

## MVP

- [x] 1. **User authentication** - register, log in, log out, and maintain secure authenticated sessions
  - [x] 1a. **User identity foundation** - User and Session tables, model conventions, password hashing, and the async test-database fixtures
  - [x] 1b. **Registration and login API** - register and login endpoints issuing access and refresh tokens
  - [x] 1c. **Session lifecycle and route protection** - token refresh, logout and session invalidation, and the authenticated-user dependency
  - [x] 1d. **Authentication UI** - register, log in, and log out screens wired to the auth API
- [x] 2. **Organization workspaces and invitations** - create organizations, manage memberships, invite users, and enforce tenant isolation
  - [x] 2a. **Organization and membership foundation** - Organization and OrganizationMember tables, org creation with an owner, org listing/detail, and the tenant-isolation membership dependency later features depend on
  - [x] 2b. **Membership management** - update organization details, list and remove members, change member roles, with last-owner protection
  - [x] 2c. **Invitations** - invite by email, accept/revoke/list invitations, behind a swappable email-sending abstraction (no provider is decided in the plans yet, so this ships with a logging mock)
  - [x] 2d. **Organization UI** - create-organization flow and a members/invitations management screen
- [x] 3. **Role-based access control** - define and enforce organization roles and permissions
- [ ] 4. **User and organization settings** - manage profiles, organization configuration, and basic preferences
- [ ] 5. **Core application shell** - provide the authenticated dashboard, navigation, workspace switching, and global states
- [ ] 6. **Company management** - create, view, search, update, and organize company records
- [ ] 7. **Contact management** - create, view, search, update, and associate contacts with companies
- [ ] 8. **Opportunity management** - manage deals, stages, values, owners, and opportunity status
- [ ] 9. **Task management** - create, assign, track, and complete business tasks
- [ ] 10. **Notes and activity timeline** - capture notes and display user-facing business activity history
- [ ] 11. **Document upload and storage** - upload documents and manage their metadata and production object-storage references
- [ ] 12. **Document processing pipeline** - parse, normalize, chunk, and track document processing status
- [ ] 13. **Vector knowledge indexing** - generate OpenAI text-embedding-3-small embeddings at 1536 dimensions and store them in pgvector
- [ ] 14. **Semantic knowledge retrieval** - retrieve relevant tenant-scoped organizational knowledge for user questions
- [ ] 15. **AI chat interface** - provide persistent conversational chat with streaming responses
- [ ] 16. **Grounded RAG responses** - answer questions using retrieved organizational knowledge and provide sources
- [ ] 17. **Conversation management** - create, rename, browse, persist, and delete AI conversations
- [ ] 18. **AI provider abstraction** - support configurable LLM and embedding providers through stable interfaces
- [ ] 19. **Prompt management** - manage reusable prompts and prompt versions for AI workflows
- [ ] 20. **AI guardrails** - validate inputs and outputs and prevent unsafe or unauthorized AI behavior
- [ ] 21. **AI and retrieval observability** - capture useful AI, retrieval, latency, usage, and error telemetry
- [ ] 22. **Audit logging** - record important authentication, authorization, data, security, and administrative events
- [ ] 23. **Global search** - search across core business entities and the organization knowledge base with typed, tenant-scoped results
- [ ] 24. **API and service health monitoring** - expose application, database, Redis, and dependency health checks
- [ ] 25. **Automated testing foundation** - establish backend pytest coverage plus frontend Vitest/React Testing Library and Playwright coverage
- [ ] 26. **Dockerized development environment** - run the complete Norma AI stack consistently with Docker Compose
- [ ] 27. **Production configuration** - separate development and production configuration and secure environment handling
- [ ] 28. **Initial CI/CD pipeline** - run linting, type checks, backend tests, frontend tests, E2E tests, builds, and deployment checks automatically with GitHub Actions
- [ ] 29. **Production deployment readiness** - configure Vercel, Render, Render Postgres/pgvector, Render Key Value, and Amazon S3 and verify production builds and health checks
- [ ] 30. **MVP release validation** - validate the complete user journey from onboarding through AI-powered business questions

## Post-MVP

- [ ] 31. **Team collaboration** - improve shared workspaces, team activity, and collaborative workflows
- [ ] 32. **AI business actions** - allow authorized AI workflows to perform supported CRM and productivity actions
- [ ] 33. **Background AI workflows** - run long-running document, retrieval, and agent workflows asynchronously
- [ ] 34. **Advanced knowledge management** - improve document organization, permissions, metadata, and knowledge sources
- [ ] 35. **External integrations** - connect selected email, calendar, storage, communication, and CRM systems
- [ ] 36. **Advanced analytics** - provide organization, CRM, AI usage, and productivity analytics
- [ ] 37. **Usage limits and metering** - track organization-level AI, storage, and processing usage
- [ ] 38. **Subscription billing** - introduce free, Pro, Team, and usage-aware subscription plans
- [ ] 39. **Enterprise security** - add advanced RBAC, SSO/SAML, enterprise controls, and compliance capabilities
- [ ] 40. **Advanced AI agents** - introduce stateful multi-step agents with explicit tool permissions and approval flows
- [ ] 41. **Production observability** - add centralized logs, tracing, metrics, error tracking, and operational dashboards
- [ ] 42. **Scalability improvements** - optimize database, retrieval, caching, workers, and AI infrastructure for higher usage
- [ ] 43. **Advanced document storage lifecycle** - add retention, archival, lifecycle policies, replication, and large-scale storage optimization
- [ ] 44. **Advanced deployment operations** - improve CI/CD, automated rollback procedures, environment promotion, and production operations
- [ ] 45. **Mobile and responsive improvements** - expand the experience for smaller screens and mobile workflows
- [ ] 46. **Enterprise integrations and workflows** - support organization-specific integrations and configurable business automation
- [ ] 47. **Public launch readiness** - finalize security, performance, documentation, onboarding, support, and production launch processes

## Planning decisions

- **LLM Provider:** Anthropic
- **Chat Model:** claude-opus-5
- **LLM environment variables:** LLM_PROVIDER, LLM_MODEL, ANTHROPIC_API_KEY, and configurable ANTHROPIC_BASE_URL
- **Embeddings:** OpenAI `text-embedding-3-small`.
- **Embedding vector dimension:** `1536`.
- **Test embedding provider:** deterministic `MockEmbeddingProvider`.
- **Production backend:** Render Docker Web Service.
- **Production frontend:** Vercel.
- **Production database:** Render Managed PostgreSQL with pgvector.
- **Production Redis:** Render Key Value.
- **Production document storage:** Amazon S3 from the first production release.
- **Global search:** one tenant-scoped search experience spanning core business entities and knowledge.
- **Invitations:** included in item 2; split into 2a/2b only when the feature is formally specified if the implementation scope requires it.
- **Activity vs AuditLog:** distinct concepts; Activity is user-facing business history, AuditLog is security/compliance history.
- **Frontend test tooling:** Vitest + React Testing Library for fast frontend tests; Playwright Test for E2E browser coverage.
- **Environment variable casing:** `OPENAI_API_KEY` and other provider credentials use conventional uppercase names.
- **Container naming:** new project-facing configuration uses `norma-*`; legacy `fonio-*` identifiers should not be introduced into new work.

## Continuing after the initial build

Completed items remain checked and their numbers must not be reused. Large items may be split into sub-items such as `2a`, `2b`, `12a`, or `12b` when feature specifications expose meaningful independent build slices.

When a new feature materially changes product direction, users, data, stack, monetization, UI/UX, or deployment, update `project-plan.md` and then refresh the overview before creating the feature spec.

The next unchecked item is the default feature target unless a specific feature number or name is selected.
