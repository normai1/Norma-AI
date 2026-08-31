# Build Plan

> One of the two planning docs for **Norma AI**. This is the living feature roadmap and progress tracker. Items stay intentionally high-level; detailed architecture, API contracts, schemas, edge cases, and implementation decisions belong in feature specifications.
>
> **Renumbering note.** This plan was rewritten when the product direction changed to an AI phone assistant platform. Completed items 1–3 carried over unchanged because authentication, organizations, and RBAC are identical requirements under the new direction. Everything from item 4 onward was re-planned and renumbered. Items 4–47 of the previous roadmap were never started and their numbers have been reused.
>
> **Second renumbering note.** Item 23 (assistant editor redesign) was inserted after item 22 and every item from the old 23 onward shifted up by one (old 23→24, ..., old 84→85), none of which were started or archived at the time. Historical feature archives under `blueprint/history/` still reference the numbering as it stood when each was written and were deliberately left unchanged - they are point-in-time records, not living documents.

## MVP

### Foundations

- [x] 1. **User authentication** - register, log in, log out, and maintain secure authenticated sessions
  - [x] 1a. **User identity foundation** - User and Session tables, model conventions, password hashing, and the async test-database fixtures
  - [x] 1b. **Registration and login API** - register and login endpoints issuing access and refresh tokens
  - [x] 1c. **Session lifecycle and route protection** - token refresh, logout and session invalidation, and the authenticated-user dependency
  - [x] 1d. **Authentication UI** - register, log in, and log out screens wired to the auth API
- [x] 2. **Organization workspaces and invitations** - create organizations, manage memberships, invite users, and enforce tenant isolation
  - [x] 2a. **Organization and membership foundation** - Organization and OrganizationMember tables, org creation with an owner, org listing/detail, and the tenant-isolation membership dependency later features depend on
  - [x] 2b. **Membership management** - update organization details, list and remove members, change member roles, with last-owner protection
  - [x] 2c. **Invitations** - invite by email, accept/revoke/list invitations, behind a swappable email-sending abstraction (ships with a logging mock)
  - [x] 2d. **Organization UI** - create-organization flow and a members/invitations management screen
- [x] 3. **Role-based access control** - define and enforce organization roles and permissions
- [x] 4. **Automated testing foundation** - establish backend pytest patterns, tenant-isolation regression tests, deterministic provider mocks, and frontend Vitest/React Testing Library plus Playwright scaffolding, before the feature surface grows
  - [x] 4a. **Backend test foundation** - a consolidated cross-organization tenant-isolation regression suite covering every existing org-scoped endpoint, plus tuning `coding-standards.md`'s Testing section to describe this project's actual pytest conventions and provider-mock pattern
  - [x] 4b. **Frontend test tooling** - install and wire Vitest, React Testing Library, and Playwright, turn on the frontend test gate in `AGENTS.md`, and ship one example unit test and one example E2E test
- [x] 5. **Dockerized development environment** - run the full stack with Docker Compose including the media-plane and background-worker services, and document the public-tunnel setup required for telephony webhooks
- [x] 6. **Workspaces** - Workspace and WorkspaceMember tables, workspace-scoped authorization, and the scoping dependency that assistants, numbers, calls, and contacts all depend on
  - [x] 6a. **Workspace foundation** - Workspace/WorkspaceMember tables, workspace CRUD, and the `require_workspace_access` dependency later features depend on
  - [x] 6b. **Workspace membership management** - add/remove/list organization members on a workspace, backend only
  - [x] 6c. **Workspace switching UI** - workspace list/switcher, create-workspace form, and a member management screen
- [x] 7. **Core application shell** - authenticated layout, navigation, organization and workspace switching, and global loading/empty/error states
  - [x] 7a. **Session and tenant context** - a session provider that resolves the signed-in user once, an active organization/workspace context persisted and validated against real membership, and an authenticated route group whose layout guards every page inside it
  - [x] 7b. **Application shell and navigation** - sidebar navigation showing only built features, a top bar with the organization and workspace switchers and a user menu with sign-out, and the existing organization/workspace pages moved onto the shell
  - [x] 7c. **Overview home and global states** - the `/overview` authenticated landing page, shared loading/empty/error primitives with a route error boundary applied shell-wide, and post-authentication redirect wiring
- [x] 8. **User and organization settings** - profile and password management, and validated organization/workspace settings (timezone, locale, business hours, currency) in place of an unvalidated JSON blob
  - [x] 8a. **Profile and password management** - update your own name and avatar, and change your own password with current-password verification, full session revocation, and a fresh token pair; backend only
  - [x] 8b. **Validated organization and workspace settings** - replace the unvalidated `settings` JSONB blob with a validated shape (timezone, locale, currency, business hours), decide organization-versus-workspace ownership and inheritance, and backfill existing rows; backend only
  - [x] 8c. **Settings UI** - a `/settings` area covering the account screen (profile and password) and the organization/workspace settings forms

### Assistant configuration

- [x] 9. **Speech provider abstractions** - `SpeechToTextProvider` and `TextToSpeechProvider` interfaces with one real implementation each plus deterministic mocks
  - [x] 9a. **Speech provider contracts and mocks** - the two streaming interfaces, their shared value types and error hierarchy, deterministic `MockSTT`/`MockTTS` with latency and failure injection, and config-driven provider selection; no network, no new dependencies
  - [x] 9b. **ElevenLabs speech adapters** - `ElevenLabsSTT` and `ElevenLabsTTS` implementing 9a's contracts, with the transport dependency they need and adapter tests against a stubbed transport
- [x] 10. **Voice and language catalogue** - browsable voice list with language and gender metadata, and in-browser voice preview
- [x] 11. **Assistant foundation** - Assistant and AssistantVersion tables, CRUD, immutable configuration snapshots, and version pinning per call
  - [x] 11a. **Assistant model and CRUD** - create, list, update, archive assistants within a workspace
  - [x] 11b. **Configuration schema** - validated voice, language, greeting, persona, speech rate, sensitivity, creativity, and ambience settings
  - [x] 11c. **Versioning** - immutable version snapshots, diffing, and rollback
  - [x] 11d. **Assistant editor UI** - simple and advanced modes over the same configuration
- [x] 12. **Prompt templates and versioning** - reusable use-case templates (receptionist, support, scheduling, answering machine, field service, order intake) with versions, variable interpolation, and rollback
  - [x] 12a. **Prompt template backend** - PromptTemplate and PromptVersion tables, CRUD, immutable versioning, publish/rollback, and diff, mirroring item 11's proven Assistant/AssistantVersion pattern
  - [x] 12b. **Variable interpolation and assistant wiring** - a pure `{{namespace.field}}` renderer over assistant/workspace/caller context, plus the additive `prompt_template_id`/`prompt_version` columns on `AssistantVersion`
  - [x] 12c. **Prompt template editor UI** - list, editor, version history, publish/rollback, and diff, mirroring 11d
- [x] 13. **Glossary and pronunciation** - per-assistant terms, abbreviations, and phonetic overrides, applied as STT biasing and TTS pronunciation
  - [x] 13a. **Glossary backend** - GlossaryEntry model and CRUD, scoped to organization, workspace, and assistant
  - [x] 13b. **Glossary UI** - a glossary section on the assistant editor page (list, add, edit, delete)

### Knowledge

- [x] 14. **Knowledge source foundation** - KnowledgeSource model, upload of PDF/DOCX/Markdown/TXT, S3-backed storage, and processing status/error surfacing
- [x] 15. **Website ingestion** - crawl a supplied domain, extract page content, handle recrawl and content-hash deduplication
- [x] 16. **Manual FAQ entries** - operator-authored question/answer pairs treated as a first-class knowledge source
- [x] 17. **Document processing pipeline** - parse, normalize, and chunk all source types with status tracking and retryable failures
- [x] 18. **Vector knowledge indexing** - OpenAI `text-embedding-3-small` embeddings at 1536 dimensions stored in pgvector, scoped by organization, workspace, and assistant
- [x] 19. **Low-latency retrieval** - tenant-scoped semantic retrieval and context builder that fits inside the in-call turn budget, with source attribution recorded per answer

### Real-time voice engine

- [x] 20. **Real-time voice session engine** - the core product. Sub-items are independent build slices but the item is only complete when a full spoken conversation works end to end
  - [x] 20a. **Framework selection and media transport** - evaluate LiveKit Agents against Pipecat, record the decision, and establish bidirectional streaming audio behind Norma's own interfaces
  - [x] 20b. **Streaming STT integration** - partial and final transcripts with glossary biasing
  - [x] 20c. **Turn detection** - VAD plus semantic end-of-turn classification with operator-configurable sensitivity
  - [x] 20d. **LLM turn loop** - realtime model streaming, assistant configuration and retrieved context assembly, conversation state
  - [x] 20e. **Streaming TTS and barge-in** - sentence-chunked playback starting before the LLM finishes, with immediate cancellation on caller speech
  - [x] 20f. **Latency instrumentation** - per-turn TurnMetric rows across every leg, with a p95 budget enforced in CI
  - [x] 20g. **Session resilience** - provider timeouts, retries, and failover to forwarding or message-taking instead of dead air
- [x] 21. **In-browser test call** - operators talk to an assistant from the assistant editor with no phone number required, over WebRTC or WebSocket audio
  - [x] 21a. **Voice session authorization** - a short-lived, workspace-checked ticket the browser exchanges for `/media/session` access, closing the current gap where the WebSocket route trusts a bare client-supplied assistant_id with no authentication at all
  - [x] 21b. **Browser test-call UI** - microphone capture and playback wired to `/media/session` (mic permission, connection state, speaking/listening indicator, client-side barge-in), on the new `/assistants/[id]/test-call` page
- [x] 22. **Voice pipeline test harness** - fixture-audio conversation replay against the full pipeline with mock providers, plus barge-in and turn-detection behavioural tests
- [ ] 23. **Assistant editor redesign: General/Knowledge/Custom Prompt/Technical tabs** - reorganizes the assistant editor into four tabs; Knowledge adds full knowledge-source management and closes the assistant-scoping gap retrieval (item 19) has deferred; Custom Prompt adds the prompt-template picker; Technical relocates speech rate/sensitivity/creativity and Glossary (renamed Technical Terms), and adds configuration-only call-duration/silence-timeout limits, a recording toggle, auto-delete-on-declined-consent, and ambient-sound presets with volume - pending enforcement until telephony (items 24-28) exists
  - [x] 23a. **Tab shell and field relocation** - convert the single-page editor into four tabs; General keeps name/voice/language/greeting/persona, Technical gets speech rate/sensitivity/creativity plus Glossary (renamed Technical Terms), Knowledge and Custom Prompt start as placeholder tabs
  - [ ] 23b. **Technical tab: call and recording settings** - max call duration, max silence timeout, a recording toggle, auto-delete-on-declined-consent, and ambient-sound presets (office/conference room) with volume - configuration-only until telephony (items 24-28) exists
  - [ ] 23c. **Custom Prompt tab** - prompt-template picker using the existing prompt_template_id/prompt_version fields
  - [ ] 23d. **Knowledge tab backend** - assistant_id scoping on KnowledgeSource/Chunk and a retrieval filter update, closing the gap item 19 deferred
  - [ ] 23e. **Knowledge tab UI** - file upload, website ingestion, manual FAQ, and processing status management inside the assistant editor

### Telephony

- [ ] 24. **Telephony provider abstraction** - `TelephonyProvider` interface, verified webhook signature handling, and a mock provider that simulates the full call lifecycle without real audio
- [ ] 25. **Phone number provisioning** - search and claim numbers by country and area, surface regulatory document requirements, assign numbers to assistants, and release numbers
- [ ] 26. **Inbound call handling** - answer an inbound call on a provisioned number, route it to the correct assistant version, and hold a complete conversation
- [ ] 27. **Call records and transcripts** - Call, CallLeg, and TranscriptTurn persistence with direction, duration, billable seconds, outcome, and interruption marking
- [ ] 28. **Call recording** - optional recording to S3, configurable retention, automatic deletion, and signed short-lived playback URLs
- [ ] 29. **Call detail UI** - transcript synchronized to audio playback, tool-call log, knowledge sources used per answer, and latency detail behind a disclosure
- [ ] 30. **Live call forwarding** - transfer to a human on request, on failure, or outside business hours, with prioritized targets and international forwarding
- [ ] 31. **DTMF and IVR navigation** - send keypress tones so the assistant can traverse external phone menus
- [ ] 32. **Concurrency and call admission control** - per-plan concurrent-call limits enforced at admission, with defined overflow behaviour rather than queueing indefinitely
- [ ] 33. **Bring-your-own SIP trunk** - connect an existing PBX or carrier for inbound and outbound, with credential storage and allowed-IP configuration

### In-call skills and post-call handling

- [ ] 34. **Tool permission framework** - declarative per-assistant skill enablement, enforced independently of model output, with ToolInvocation logging
- [ ] 35. **Variable extraction** - operator-defined field schema, structured extraction from the conversation, and confidence reporting
- [ ] 36. **Post-call processing** - generated summary and disposition using the post-call model, assembled from the transcript and extracted fields
- [ ] 37. **Post-call delivery** - email and SMS delivery of transcript, summary, and fields, behind swappable messaging providers
- [ ] 38. **Contacts** - workspace-scoped contact records, automatic creation and phone-number matching from calls, per-contact call history, custom fields, import/export, and do-not-call flags
- [ ] 39. **Calendar connections** - Google Calendar and Microsoft 365 OAuth connections, calendar selection, and availability rules
- [ ] 40. **In-call scheduler** - live availability check, appointment booking, rescheduling and cancellation during the call, and confirmation by SMS or email
- [ ] 41. **Webhooks** - outbound webhooks for call events with signing and retry, plus an inbound webhook for triggering calls and updating assistant data
- [ ] 42. **Custom API actions** - operator-configured authenticated HTTP requests executed during or after a call, with request/response schemas and timeout handling
- [ ] 43. **Integration framework** - credential storage, per-integration scopes, connection status, and the first native CRM integration
- [ ] 44. **Public API and API keys** - versioned REST API surface with hashed, scoped, revocable organization API keys

### Outbound

- [ ] 45. **Outbound calls** - place a single outbound call from an assistant, with caller-ID configuration and per-destination gating
- [ ] 46. **Outbound campaigns** - contact lists, calling windows with timezone awareness, retry policy, per-contact outcome tracking, suppression lists, and per-organization dialling concurrency
- [ ] 47. **Outbound abuse controls** - spend caps, destination allow-lists, consent gating, and rate limits on call-triggering endpoints

### Trust, insight, and operations

- [ ] 48. **AI guardrails** - prompt-injection resistance for retrieved and caller-supplied text, topic and action allow-lists, refusal to state ungrounded prices/hours/policy, PII rules in transcripts and logs, and output validation before spoken commitments
- [ ] 49. **Call analytics** - calls handled and missed, minutes against allowance, resolution and transfer rates, average handle time, appointment conversion, top intents, and latency percentiles
- [ ] 50. **Voice and retrieval observability** - structured logging with call and turn correlation IDs, per-provider latency and error telemetry, token and cost capture per call
- [ ] 51. **Activity timeline** - user-facing business history (call handled, appointment booked, contact created, assistant published)
- [ ] 52. **Audit logging** - security and administrative events including logins, role changes, number provisioning, recording deletion, integration connection, and API key issuance
- [ ] 53. **Global search** - tenant-scoped typed search across assistants, calls, transcripts, contacts, appointments, numbers, and knowledge sources
- [ ] 54. **API and service health monitoring** - application, database, Redis, media-plane capacity, and per-provider dependency health checks

### Commercial

- [ ] 55. **Usage metering** - per-call minute accounting, plus call, contact, number, and workspace counters aggregated per billing period, with provider cost capture for margin visibility
- [ ] 56. **Subscription billing** - Stripe plans for Solo, Team, and Scale with monthly and annual intervals, checkout, plan changes, and webhook reconciliation
- [ ] 57. **Plan limit enforcement** - concurrency, minute, number, workspace, and contact allowances enforced in the application, with automatic overage packages and defined behaviour on exhaustion that never fails silently mid-call
- [ ] 58. **Onboarding flow** - guided first-run: pick a use-case template, crawl the business website, choose a voice, hear a test call, then claim a number

### Ship

- [ ] 59. **Production configuration** - separated development and production configuration, secure secret handling, and per-plane environment definitions
- [ ] 60. **Initial CI/CD pipeline** - lint, type check, backend tests, frontend tests, voice pipeline and latency regression tests, E2E tests, builds for web/api/voice, migration step, and staged deploy with health verification
- [ ] 61. **Production deployment readiness** - Vercel frontend, Render API and worker, media plane on Fly.io or AWS ECS with connection-draining deploys and session-based scaling, Render Postgres with pgvector, Render Key Value, S3, and Stripe live mode
- [ ] 62. **Load and latency validation** - concurrent-call load test against production topology confirming p95 time-to-first-audio within budget and no dropped calls during a deploy
- [ ] 63. **MVP release validation** - a real inbound call to a real number, answered by a real assistant, that books a real calendar appointment and delivers a correct transcript, summary, and extracted fields, repeatedly, from a cold production deployment

## Post-MVP

- [ ] 64. **Self-learning knowledge base** - unanswered questions surfaced as an actionable inbox, assistant-proposed answers reviewed and approved by an operator, and occurrence counting to prioritize gaps
- [ ] 65. **Internet search skill** - optional live web search as an explicitly enabled, rate-limited in-call tool
- [ ] 66. **WhatsApp channel** - conversation-based AI chatbot on WhatsApp Business, with human handover and shared knowledge/skills
- [ ] 67. **Web chat channel** - embeddable site widget sharing assistant configuration and knowledge
- [ ] 68. **AI email assistant** - inbound email triage, drafting, and reply automation
- [ ] 69. **Omnichannel inbox** - one conversation view across voice, WhatsApp, chat, and email with unified contact history
- [ ] 70. **Custom voice** - voice cloning and brand-voice creation with consent verification
- [ ] 71. **Zero data retention mode** - discard audio and transcript text after delivery, keeping only metadata and metrics
- [ ] 72. **Enterprise security** - SSO/SAML, advanced RBAC, custom contractual agreements, and compliance controls
- [ ] 73. **Custom SLA and support tiers** - uptime commitments, priority routing, and account management tooling
- [ ] 74. **Integration expansion** - additional native CRM, calendar, helpdesk, PMS, and industry-vertical systems
- [ ] 75. **Vertical templates** - packaged assistant configurations, prompts, and field schemas per industry
- [ ] 76. **Localization** - application and marketing i18n across target markets
- [ ] 77. **Partner and reseller program** - agency multi-organization management, white-labelling, and revenue sharing
- [ ] 78. **Advanced analytics** - cohort and outcome analysis, intent trend detection, quality scoring, and per-assistant A/B comparison
- [ ] 79. **Multi-region media routing** - regional session placement, data residency selection, and failover
- [ ] 80. **Scalability improvements** - call and transcript table partitioning or archival, retrieval caching, media-plane autoscaling on session count, and provider failover
- [ ] 81. **Production observability** - centralized logs, distributed tracing across control and media planes, metrics, error tracking, and on-call dashboards
- [ ] 82. **Advanced deployment operations** - automated rollback, environment promotion, and progressive delivery for the media plane
- [ ] 83. **Hosted PBX and softphone** - inbound routing rules, extensions, and a browser softphone for human handover
- [ ] 84. **Advanced AI workflows** - stateful multi-step agents with explicit tool permissions and human approval steps
- [ ] 85. **Public launch readiness** - security review, penetration test, performance validation, documentation, help centre, onboarding academy, support processes, and launch

## Planning decisions

- **Product direction:** AI phone assistant platform. Voice is the MVP channel; WhatsApp, web chat, and email are post-MVP.
- **Architecture split:** separate control plane (API, web, workers) and media plane (long-lived voice session workers). This split is fixed and drives the deployment model.
- **Realtime orchestration framework:** LiveKit Agents is the primary candidate, Pipecat the evaluated alternative. Decided in item 20a and recorded here once chosen.
- **Realtime LLM:** a latency-optimized small model (claude-haiku-4-5 class). A frontier model in the per-turn loop is explicitly rejected on first-token latency grounds.
- **Post-call LLM:** a higher-quality latency-tolerant model (claude-sonnet-5 class) for summaries, extraction, and learning candidates.
- **LLM environment variables:** `LLM_PROVIDER`, `LLM_REALTIME_MODEL`, `LLM_POSTCALL_MODEL`, `ANTHROPIC_API_KEY`, configurable `ANTHROPIC_BASE_URL`.
- **Speech:** ElevenLabs serves both directions - `ElevenLabsSTT` behind `SpeechToTextProvider`, `ElevenLabsTTS` (low-latency streaming) behind `TextToSpeechProvider`. Deliberately a single vendor: one contract, one key, one integration surface, accepting a shared failure domain. No alternative implementation is planned for either direction; a speech-provider outage falls back to forwarding or message-taking, not to a second vendor. The abstractions still exist so a second provider can be added later without touching the application layer.
- **Turn detection:** VAD plus semantic end-of-turn classification, with operator-configurable sensitivity.
- **Latency budget:** p50 under 700 ms and p95 under 1,200 ms time-to-first-audio; barge-in within 200 ms. Enforced by CI regression tests, not aspirational.
- **Telephony:** Twilio as the first provider behind `TelephonyProvider`; Telnyx evaluated for EU number economics; SIP trunk supported for bring-your-own numbers.
- **Embeddings:** OpenAI `text-embedding-3-small` at `1536` dimensions in pgvector. Deterministic `MockEmbeddingProvider` for tests.
- **Retrieval budget:** under 80 ms, small top-k, scoped by organization, workspace, and assistant.
- **Recording:** off by default, per-assistant configurable, retention and deletion configurable per organization, served only through signed short-lived URLs.
- **Activity vs AuditLog:** distinct concepts. Activity is user-facing business history; AuditLog is security/compliance history.
- **Billing:** Stripe. Minutes are the primary metered unit; numbers, workspaces, and contacts are secondary billable quantities. Plan limits are enforced in the application, not only on the invoice.
- **Billing in MVP:** yes. The product is minute-metered, so metering and enforcement cannot be deferred without breaking unit economics and abuse control.
- **Test tooling:** pytest and httpx for backend; Vitest and React Testing Library for frontend; Playwright for E2E. Jest is not introduced. A voice-pipeline replay harness is a first-class test tier.
- **Production topology:** Vercel frontend, Render Docker API and background worker, media plane on Fly.io (fallback AWS ECS/Fargate), Render Postgres with pgvector, Render Key Value, Amazon S3.
- **Environment variable casing:** provider credentials use conventional uppercase names.
- **Container naming:** project-facing configuration uses `norma-*`. Legacy `fonio-*` identifiers must not be introduced into new work.
- **Regulatory:** number provisioning and outbound automated calling rules are per-country configuration. For any early market, current local requirements must be verified before those flows are enabled — treated as a launch blocker, not an implementation detail.

## Continuing after the initial build

Completed items remain checked and their numbers must not be reused. Large items may be split into sub-items such as `11a`, `20c`, or `20g` when feature specifications expose meaningful independent build slices.

Item 20 is the highest-risk item in the plan and the one most likely to expose incorrect assumptions elsewhere. Consider building a throwaway spike of 20a–20e before committing to the surrounding items, and expect the spike to change decisions recorded above.

When a new feature materially changes product direction, users, data, stack, monetization, UI/UX, or deployment, update `project-plan.md` and then refresh the overview before creating the feature spec.

The next unchecked item is the default feature target unless a specific feature number or name is selected.
