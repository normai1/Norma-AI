# Norma AI - Project Overview

> An AI phone assistant platform: answers a business's calls, holds a natural spoken conversation, completes the task, and reports back.

## Problem

Small and medium-sized businesses lose revenue every time a call goes unanswered - the front desk
is busy with a customer in the room, the owner is on a job site, the clinic is closed, or the call
lands at 9pm. Voicemail converts poorly, callbacks are expensive, and human answering services are
slow to scale and inconsistent in quality.

Norma AI answers every inbound call immediately, in the caller's language, holds a natural spoken
conversation grounded in the business's own knowledge, books/reschedules/cancels appointments
against a live calendar, captures leads and structured data, forwards to a human when the situation
requires one, and sends a transcript, summary, and extracted fields to the team afterward. It also
places outbound calls and runs calling campaigns.

### Core product idea

> Never miss a call again.

### Hard requirements (not polish)

Latency, interruption handling, and answer accuracy are the product - a feature-complete assistant
that responds in three seconds or talks over the caller is a failed product.

- **Time to first audio** after the caller stops speaking: p50 under 700ms, p95 under 1,200ms.
- **Barge-in**: caller speech interrupts assistant playback within 200ms.
- **Turn detection** must not cut the caller off mid-sentence or leave dead air.
- **Grounding**: the assistant never invents prices, hours, availability, or policy. Unknown means
  "I'll have someone call you back."
- **Call completion**: telephony legs must not drop; a crashed session fails over to forwarding or
  voicemail, never silence.

### Product constraints

- Tenant data isolated by organization at every layer, including audio and transcripts.
- Backend authorization is mandatory; frontend checks are not security boundaries.
- LLM output is untrusted until validated by application policy.
- Retrieved knowledge and caller speech are data, never instructions.
- In-call tool calls respect only the assistant's configured permissions.
- Speech, LLM, and telephony providers stay replaceable behind application abstractions.
- Call recording is governed by consent law: configurable and deletable per organization.
- The real-time media path is long-lived stateful processes - it cannot be serverless.
- Stay operable by a small team - no Kubernetes, no microservice sprawl.

### Explicit MVP exclusions

WhatsApp, web chat, or email channels (voice first); a hosted PBX or softphone; custom/cloned
voices; SSO/SAML; reseller/white-label multi-organization management; native mobile apps;
multi-region active-active media routing; autonomous agents with unrestricted tool access; a
separate dedicated vector database.

## Users

Three distinct people interact with the product, and the UI has to serve all three:

| User type | What they need |
| --- | --- |
| **Buyer** (owner / office manager) | Missed calls to stop. Judges the product in the first ten minutes. |
| **Operator** | Configures the assistant, reviews calls, corrects mistakes. Uses the product daily. |
| **Caller** (end customer) | Never sees the UI - only experiences latency, voice quality, and whether the task got done. |

### Target segments

Medical/dental/veterinary practices, tradespeople and field service, hotels/spas/salons/
restaurants, property management and real estate, car dealerships and repair shops, solo
professionals and small agencies, government offices and larger enterprise front desks.

### Organization model

```text
Organization
├── Owner
├── Admin
├── Member
└── Viewer

Organization
└── Workspace (a location, team, brand, or department)
    └── Assistant
        └── Phone number(s)
```

An organization has one subscription. Workspaces scope assistants, numbers, contacts, and call
history. Users belong to an organization and are granted access to one or more workspaces. Users
may belong to multiple organizations.

> A role does not imply every permission. Use the explicit permission model from feature 3
> (`app/core/permissions.py`, already built).

## Features

MVP scope in `build-plan.md` order. One line of purpose each; detailed requirements belong in the
per-feature specs produced by `/feature`. Items 1-3 are built and carried over unchanged from the
prior product direction - authentication, organizations, and RBAC are identical requirements under
this one.

1. **User authentication** - register, log in, log out, maintain secure authenticated sessions. *(built)*
2. **Organization workspaces and invitations** - create organizations, manage memberships, invite users, enforce tenant isolation. *(built)*
3. **Role-based access control** - define and enforce organization roles and permissions. *(built)*
4. **Automated testing foundation** - backend pytest patterns, tenant-isolation regression tests, deterministic provider mocks, frontend Vitest/RTL/Playwright scaffolding, before the feature surface grows further.
5. **Dockerized development environment** - the full stack via Docker Compose, including the media-plane and background-worker services, plus the public-tunnel setup telephony webhooks require.
6. **Workspaces** - Workspace/WorkspaceMember tables, workspace-scoped authorization, the scoping dependency assistants/numbers/calls/contacts all depend on.
7. **Core application shell** - authenticated layout, navigation, organization and workspace switching, global loading/empty/error states.
8. **User and organization settings** - profile and password management, and validated organization/workspace settings (timezone, locale, business hours, currency) in place of an unvalidated JSON blob.
9. **Speech provider abstractions** - `SpeechToTextProvider`/`TextToSpeechProvider` interfaces, one real implementation each, plus deterministic mocks.
10. **Voice and language catalogue** - browsable voice list with language/gender metadata, in-browser preview.
11. **Assistant foundation** - Assistant/AssistantVersion tables, CRUD, immutable configuration snapshots, version pinning per call.
    - 11a model + CRUD, 11b configuration schema, 11c versioning, 11d editor UI (simple + advanced modes).
12. **Prompt templates and versioning** - reusable use-case templates, versions, variable interpolation, rollback.
13. **Glossary and pronunciation** - per-assistant terms/abbreviations/phonetic overrides, applied as STT biasing and TTS pronunciation.
14. **Knowledge source foundation** - KnowledgeSource model, PDF/DOCX/Markdown/TXT upload, S3-backed storage, processing status/error surfacing.
15. **Website ingestion** - crawl a supplied domain, extract page content, handle recrawl and content-hash dedup.
16. **Manual FAQ entries** - operator-authored Q&A pairs as a first-class knowledge source.
17. **Document processing pipeline** - parse, normalize, and chunk every source type, with status tracking and retryable failures.
18. **Vector knowledge indexing** - OpenAI `text-embedding-3-small` embeddings at 1536 dimensions in pgvector, scoped by organization, workspace, and assistant.
19. **Low-latency retrieval** - tenant-scoped semantic retrieval and context builder that fits inside the in-call turn budget, source attribution recorded per answer.
20. **Real-time voice session engine** - **the core product.** Sub-items are independent build slices, but the item is only complete when a full spoken conversation works end to end.
    - 20a framework selection + media transport (LiveKit Agents vs Pipecat, decided here), 20b streaming STT w/ glossary biasing, 20c turn detection (VAD + semantic, operator-configurable sensitivity), 20d LLM turn loop, 20e streaming TTS + barge-in, 20f latency instrumentation (p95 budget enforced in CI), 20g session resilience (provider timeouts/retries/failover).
21. **In-browser test call** - talk to an assistant with no phone number required, over WebRTC or WebSocket audio.
22. **Voice pipeline test harness** - fixture-audio conversation replay against the full pipeline with mock providers, plus barge-in and turn-detection behavioral tests.
23. **Telephony provider abstraction** - `TelephonyProvider` interface, verified webhook signature handling, a mock provider simulating the full call lifecycle.
24. **Phone number provisioning** - search/claim numbers by country and area, surface regulatory document requirements, assign numbers to assistants, release numbers.
25. **Inbound call handling** - answer an inbound call, route it to the correct assistant version, hold a complete conversation.
26. **Call records and transcripts** - Call, CallLeg, and TranscriptTurn persistence (direction, duration, billable seconds, outcome, interruption marking).
27. **Call recording** - optional, S3, configurable retention, automatic deletion, signed short-lived playback URLs.
28. **Call detail UI** - transcript synchronized to audio, tool-call log, knowledge sources used per answer, latency detail behind a disclosure.
29. **Live call forwarding** - transfer on request, on failure, or outside business hours, prioritized and international targets.
30. **DTMF and IVR navigation** - keypress tones so the assistant can traverse external phone menus.
31. **Concurrency and call admission control** - per-plan concurrent-call limits enforced at admission, defined overflow behavior rather than indefinite queueing.
32. **Bring-your-own SIP trunk** - connect an existing PBX/carrier, credential storage, allowed-IP configuration.
33. **Tool permission framework** - declarative per-assistant skill enablement enforced independently of model output, with ToolInvocation logging.
34. **Variable extraction** - operator-defined field schema, structured extraction from the conversation, confidence reporting.
35. **Post-call processing** - generated summary and disposition from the post-call model, assembled from the transcript and extracted fields.
36. **Post-call delivery** - email/SMS delivery of transcript/summary/fields, behind swappable messaging providers.
37. **Contacts** - workspace-scoped, automatic creation/matching from calls by phone number, per-contact call history, custom fields, import/export, do-not-call flags.
38. **Calendar connections** - Google Calendar and Microsoft 365 OAuth, calendar selection, availability rules.
39. **In-call scheduler** - live availability check, booking/rescheduling/cancellation during the call, confirmation by SMS/email.
40. **Webhooks** - outbound call-event webhooks with signing and retry, plus an inbound webhook to trigger calls or update assistant data.
41. **Custom API actions** - operator-configured authenticated HTTP requests during/after a call, with request/response schemas and timeout handling.
42. **Integration framework** - credential storage, per-integration scopes, connection status, the first native CRM integration.
43. **Public API and API keys** - versioned REST API surface with hashed, scoped, revocable organization API keys.
44. **Outbound calls** - place a single outbound call from an assistant, caller-ID configuration, per-destination gating.
45. **Outbound campaigns** - contact lists, timezone-aware calling windows, retry policy, per-contact outcome tracking, suppression lists, per-organization dialling concurrency.
46. **Outbound abuse controls** - spend caps, destination allow-lists, consent gating, rate limits on call-triggering endpoints.
47. **AI guardrails** - prompt-injection resistance for retrieved/caller-supplied text, topic and action allow-lists, refusal to state ungrounded prices/hours/policy, PII rules, output validation before spoken commitments.
48. **Call analytics** - handled/missed calls, minutes against allowance, resolution/transfer rates, average handle time, appointment conversion, top intents, latency percentiles.
49. **Voice and retrieval observability** - structured logging with call/turn correlation IDs, per-provider latency/error telemetry, token/cost capture per call.
50. **Activity timeline** - user-facing business history (call handled, appointment booked, contact created, assistant published).
51. **Audit logging** - security/administrative events: logins, role changes, number provisioning, recording deletion, integration connection, API key issuance.
52. **Global search** - tenant-scoped typed search across assistants, calls, transcripts, contacts, appointments, numbers, and knowledge sources.
53. **API and service health monitoring** - application, database, Redis, media-plane capacity, and per-provider dependency health checks.
54. **Usage metering** - per-call minute accounting, plus call/contact/number/workspace counters per billing period, with provider cost capture.
55. **Subscription billing** - Stripe plans (Solo/Team/Scale), monthly and annual, checkout, plan changes, webhook reconciliation.
56. **Plan limit enforcement** - concurrency/minute/number/workspace/contact allowances enforced in the application, automatic overage packages, defined exhaustion behavior that never fails silently mid-call.
57. **Onboarding flow** - pick a use-case template, crawl the business website, choose a voice, hear a test call, claim a number.
58. **Production configuration** - separated development/production configuration, secure secret handling, per-plane environment definitions.
59. **Initial CI/CD pipeline** - lint, typecheck, backend tests, frontend tests, voice pipeline + latency regression tests, E2E tests, builds (web/api/voice), migration step, staged deploy with health verification.
60. **Production deployment readiness** - Vercel frontend, Render API + worker, media plane on Fly.io/AWS ECS with connection-draining deploys and session-based scaling, Render Postgres + pgvector, Render Key Value, S3, Stripe live mode.
61. **Load and latency validation** - concurrent-call load test against production topology, confirming p95 time-to-first-audio within budget and no dropped calls during a deploy.
62. **MVP release validation** - a real inbound call to a real number, answered by a real assistant, that books a real calendar appointment and delivers a correct transcript, summary, and extracted fields, repeatedly, from a cold production deployment.

### MVP success path

```text
Caller dials a Norma number -> telephony leg established -> streaming STT
  -> turn detection -> assistant configuration + retrieved knowledge -> LLM turn
  -> streaming TTS -> caller hears a reply (< 900ms) -> in-call tool calls
  -> call ends -> recording/transcript/summary/extracted variables
  -> delivery to the team + webhook/integration push -> minutes metered
```

### Post-MVP (63-84)

Out of scope now, but the MVP data model and abstractions should not block them: self-learning
knowledge base (63), internet search skill (64), WhatsApp channel (65), web chat channel (66), AI
email assistant (67), omnichannel inbox (68), custom voice (69), zero data retention mode (70),
enterprise security incl. SSO/SAML (71), custom SLA/support tiers (72), integration expansion (73),
vertical templates (74), localization (75), partner/reseller program (76), advanced analytics (77),
multi-region media routing (78), scalability improvements (79), production observability (80),
advanced deployment operations (81), hosted PBX/softphone (82), advanced AI workflows (83), public
launch readiness (84).

> See Open questions - item 70 (zero data retention) may need to move into the MVP list; the
> commercial plan table already promises it on the Scale tier.

## Data model

PostgreSQL 16 + pgvector is the system of record. Every organization-owned table carries
`organization_id`, and most operational tables also carry `workspace_id` once workspaces exist
(item 6) - both filtered server-side in the repository layer, never only in a route or the
frontend.

### Shared conventions

- `id` (UUID, PK) on every table.
- `created_at`, `updated_at` (timestamptz) on every table.
- `organization_id` (UUID, FK -> Organization, indexed, NOT NULL) on every organization-owned table.
- `workspace_id` (UUID, FK -> Workspace, indexed, NOT NULL) on every workspace-scoped table, once item 6 ships.
- Soft-delete is not assumed; use hard deletes unless a feature spec requires otherwise.

### Identity - built, locked

These four tables exist in the repository today (features 1-3) and are unchanged by the product
pivot. Do not alter this shape without a real requirement.

#### User

- `email` (text, unique, NOT NULL, stored lowercase, normalized before compare/insert)
- `password_hash` (text, NOT NULL)
- `full_name` (text, nullable)
- `avatar_url` (text, nullable)
- `is_active` (bool, default true)
- `last_login_at` (timestamptz, nullable)

#### UserSession

- `user_id` (UUID, FK -> User)
- `token_hash` (text) - a hash of the refresh token, never the token itself
- `expires_at` (timestamptz)
- `revoked_at` (timestamptz, nullable)
- `user_agent`, `ip_address` (text, nullable)

> Redis may hold live session/refresh state; durable session records stay in PostgreSQL.

#### Organization

- `name` (text, NOT NULL)
- `slug` (text, unique)
- `settings` (JSONB, default `{}`) - **feature 8 gives this a validated shape**; today it is an
  unvalidated blob
- `status` (text, CHECK-constrained: `active`, `suspended` - project convention is CHECK
  constraints over native Postgres enums, avoiding `ALTER TYPE` migration ceremony)

#### OrganizationMember

- `organization_id` (UUID, FK -> Organization)
- `user_id` (UUID, FK -> User)
- `role` (text, CHECK-constrained: `owner`, `admin`, `member`, `viewer`)
- Unique on (`organization_id`, `user_id`)

#### Invitation

- `organization_id` (UUID, FK -> Organization)
- `email` (text, NOT NULL)
- `role` (text, as above)
- `token_hash` (text), `expires_at` (timestamptz)
- `status` (text: `pending`, `accepted`, `revoked`, `expired`)
- `invited_by_user_id` (UUID, FK -> User)
- Partial unique index on (`organization_id`, `email`) where `status = 'pending'`

> **Locked shape.** Every feature from item 6 onward depends on `organization_id` plus
> `OrganizationMember.role`, and `app/core/permissions.py`'s `has_permission()` contract from
> feature 3. Changing that pair is a breaking change across the whole app.

### Organizations & workspaces - new (item 6, 8)

#### Workspace

- `organization_id` (UUID, FK -> Organization)
- `name` (text, NOT NULL) - a location, team, brand, or department
- `settings` (JSONB, default `{}`) - unvalidated for now, matching `Organization.settings`;
  item 8 gives timezone/locale/business-hours/currency a validated shape here
- Has many `WorkspaceMember`, and owns every workspace-scoped record (assistants, numbers, contacts, calls)

#### WorkspaceMember

- `workspace_id` (UUID, FK -> Workspace)
- `user_id` (UUID, FK -> User)
- Unique on (`workspace_id`, `user_id`)

> **Locked shape once built.** Item 6 explicitly says this is "the scoping dependency assistants,
> numbers, calls, and contacts all depend on" - treat it with the same care as the feature-2
> organization/membership foundation.

### Assistants (items 11-13)

#### Assistant

- `organization_id`, `workspace_id`
- `name` (text, NOT NULL)
- `status` (text: `draft`, `published`, `archived`)
- `current_version_id` (UUID, FK -> AssistantVersion, nullable) - the version live calls use

#### AssistantVersion

- `assistant_id` (UUID, FK -> Assistant)
- `version` (int) - unique per assistant, never reused
- `voice_id`, `language` (text)
- `greeting` (text), `greeting_interruptible` (bool)
- `persona` (text) - behavioral instructions
- `speech_rate` (numeric), `turn_sensitivity` (numeric), `creativity` (numeric, bounded)
- `ambient_sound` (text, nullable)
- `business_hours_behavior` (JSONB)
- `fallback_behavior` (JSONB) - what to do when the assistant cannot resolve the request
- `enabled_skills` (JSONB) - the tool-permission set feature 33 enforces
- `prompt_template_id`, `prompt_version` (nullable) - which prompt this version used

> **Immutable.** Every call records exactly which `AssistantVersion` answered it (item 20). Never
> mutate a version in place; publishing a change creates a new version.

### Telephony (items 23-24, 32)

#### PhoneNumber

- `organization_id`, `workspace_id`
- `e164` (text, unique) - the number itself
- `country` (text), `provider` (text), `provider_resource_id` (text)
- `assistant_id` (UUID, FK -> Assistant, nullable)

#### SipTrunk

- `organization_id`, `workspace_id`
- `credentials_ref` (text) - reference into secret storage, never the credential itself
- `direction` (text: `inbound`, `outbound`, `both`)
- `allowed_ips` (JSONB)
- `assistant_id` (UUID, FK -> Assistant, nullable)

#### ForwardingTarget

- `organization_id`, `workspace_id`
- `name` (text), `number` (text)
- `hours` (JSONB), `priority` (int)

### Calls (items 20, 26-27, 33-36)

#### Call

- `organization_id`, `workspace_id`
- `direction` (text: `inbound`, `outbound`)
- `from_number`, `to_number` (text)
- `assistant_version_id` (UUID, FK -> AssistantVersion)
- `started_at`, `ended_at` (timestamptz)
- `duration_seconds`, `billable_seconds` (int)
- `outcome`, `disposition` (text)
- `cost` (numeric, nullable)
- `provider_call_sid` (text)

#### CallLeg

- `call_id` (UUID, FK -> Call)
- `leg_type` (text: `inbound`, `forwarded`, `outbound`)

#### TranscriptTurn

- `call_id` (UUID, FK -> Call, indexed)
- `speaker` (text: `caller`, `assistant`)
- `text` (text)
- `start_offset_ms`, `end_offset_ms` (int)
- `confidence` (numeric, nullable)
- `interrupted` (bool)

#### Recording

- `call_id` (UUID, FK -> Call)
- `storage_key` (text) - S3 object key; never a public object, always served via signed short-lived URL
- `duration_seconds` (int)
- `retention_policy` (text), `delete_at` (timestamptz, nullable)

#### CallSummary

- `call_id` (UUID, FK -> Call)
- `summary` (text)
- `model` (text), `prompt_version` (text)

#### ExtractedVariable

- `call_id` (UUID, FK -> Call)
- `field_name` (text), `value` (text), `type` (text)
- `confidence` (numeric, nullable)
- `schema_ref` (text) - which operator-defined field schema this came from

#### ToolInvocation

- `call_id` (UUID, FK -> Call)
- `tool_name` (text), `arguments` (JSONB), `result` (JSONB)
- `latency_ms` (int), `success` (bool)

> One row per in-call tool call - the enforcement record for feature 33's permission framework.

#### TurnMetric

- `call_id` (UUID, FK -> Call)
- `stt_finalized_at`, `retrieval_done_at`, `llm_first_token_at`, `llm_complete_at`, `tts_first_byte_at`, `audio_out_at` (timestamptz)

> One row per conversational turn, across every leg of the pipeline (item 20f). This is the table
> the p95 latency CI gate reads from.

### Knowledge (items 14-19)

#### KnowledgeSource

- `organization_id`, `workspace_id`
- `type` (text: `file`, `website`, `manual_faq`)
- `status` (text: `pending`, `processing`, `completed`, `failed`)
- `error_message` (text, nullable)
- `owner_user_id` (UUID, FK -> User)

#### Document

- `knowledge_source_id` (UUID, FK -> KnowledgeSource)
- `filename` (text), `content_type` (text)
- `storage_key` (text) - S3 in production, local adapter path in development
- `processing_status`, `processing_error` (text)

#### CrawledPage

- `knowledge_source_id` (UUID, FK -> KnowledgeSource)
- `url` (text), `fetched_at` (timestamptz)
- `extracted_text` (text), `content_hash` (text) - dedup key for recrawl

#### Chunk

- `organization_id`, `workspace_id` (denormalized, NOT NULL - retrieval filters on these directly)
- `knowledge_source_id` (UUID, FK -> KnowledgeSource, indexed)
- `text` (text), `ordering` (int)
- `metadata` (JSONB) - page/section/offset for citation
- `embedding` (`vector(1536)`) - pgvector column

> **The RAG contract is locked.** OpenAI `text-embedding-3-small` at **1536** dimensions, matching
> the embedding-dimension setting and the `vector(1536)` column. Embeddings of different dimensions
> must never be mixed in one column. Changing the model or dimension later requires an explicit
> migration **and** reindex strategy - never "fix" a mismatch by editing the column.

#### GlossaryEntry

- `organization_id`, `workspace_id`
- `term` (text), `meaning` (text, nullable)
- `phonetic_spelling` (text, nullable)
- `stt_boost_weight` (numeric)

#### UnansweredQuestion

- `organization_id`, `workspace_id`
- `transcript_turn_id` (UUID, FK -> TranscriptTurn)
- `question_text` (text), `occurrence_count` (int)
- `status` (text), `proposed_answer` (text, nullable), `approving_user_id` (UUID, nullable)

### Contacts & appointments (items 37-39)

#### Contact

- `organization_id`, `workspace_id`
- `name` (text), `phone_numbers` (JSONB), `email` (text, nullable)
- `custom_fields` (JSONB)
- `do_not_call` (bool, default false)
- `source` (text)

#### Appointment

- `organization_id`, `workspace_id`
- `contact_id` (UUID, FK -> Contact)
- `assistant_id` (UUID, FK -> Assistant)
- `external_calendar_event_id` (text)
- `starts_at`, `ends_at` (timestamptz)
- `status` (text), `service_type` (text, nullable)

#### CalendarConnection

- `organization_id`, `workspace_id`
- `provider` (text: `google`, `microsoft365`)
- `credentials_ref` (text), `calendar_id` (text)
- `availability_rules` (JSONB)

### Campaigns (items 44-46)

#### Campaign

- `organization_id`, `workspace_id`
- `name` (text), `assistant_id` (UUID, FK -> Assistant)
- `calling_window` (JSONB), `timezone` (text)
- `retry_policy` (JSONB), `status` (text)

#### CampaignContact

- `campaign_id` (UUID, FK -> Campaign)
- `contact_id` (UUID, FK -> Contact)
- `attempt_count` (int), `last_attempt_at` (timestamptz, nullable)
- `outcome` (text, nullable)

#### SuppressionEntry

- `organization_id`
- `phone_number` (text), `reason` (text)
- `added_by_user_id` (UUID, FK -> User)

### Integrations & API (items 40-43)

#### Integration

- `organization_id`, `workspace_id`
- `provider` (text), `credentials_ref` (text)
- `scopes` (JSONB), `status` (text), `last_sync_at` (timestamptz, nullable)

#### Webhook

- `organization_id`
- `url` (text), `secret` (text)
- `subscribed_events` (JSONB)

#### ApiAction

- `organization_id`, `workspace_id`
- `name` (text), `method` (text), `url_template` (text)
- `headers` (JSONB), `auth_ref` (text)
- `request_schema`, `response_schema` (JSONB)
- `timing` (text: `in_call`, `post_call`)

#### ApiKey

- `organization_id`
- `hashed_key` (text) - never store the raw key
- `scopes` (JSONB), `last_used_at` (timestamptz, nullable)

### Billing & usage (items 54-56)

#### Subscription

- `organization_id`
- `plan` (text: `solo`, `team`, `scale`), `interval` (text), `status` (text)
- `provider_subscription_id` (text)
- `included_minutes` (int), `concurrency_limit` (int)
- `number_allowance`, `contact_allowance` (int)

#### UsageRecord

- `organization_id`, `period` (text)
- `metric` (text: `minutes`, `calls`, `contacts`, `numbers`)
- `quantity` (numeric), `source_call_id` (UUID, FK -> Call, nullable)

#### Overage

- `organization_id`, `period`
- `package` (text), `purchased_at` (timestamptz)

### Audit & observability (items 49-51)

#### Activity

- `organization_id`, `workspace_id` (nullable)
- `actor_user_id` (UUID, FK -> User, nullable)
- `type` (text) - e.g. `call.handled`, `appointment.booked`, `contact.created`, `assistant.published`
- `payload` (JSONB)

#### AuditLog

- `organization_id` (nullable for pre-organization auth events)
- `actor_user_id` (UUID, FK -> User, nullable)
- `event_type` (text) - login, role change, number provisioning, recording deletion, integration
  connection, API key issuance, and other security/admin events
- `resource_type`, `resource_id`
- `ip_address`, `user_agent` (text, nullable)
- `metadata` (JSONB)

> **Activity is not AuditLog.** Activity is the user-facing business timeline (item 50). AuditLog is
> the security/compliance record (item 51). The plans call these deliberately distinct.

### Not stored in PostgreSQL

- **Secrets / API credentials** - environment and platform secret management, never application records.
- **Call audio and recordings** - Amazon S3 (production) or a local adapter (development).
  PostgreSQL keeps `storage_key` and metadata only. Production must never depend on container-local
  disk, which is ephemeral.
- **Redis** - job queues, session/concurrency accounting, rate limiting, caching. Never the system
  of record for durable business data.

## Tech stack

The system splits into two runtime planes with different characteristics - this split is the single
most important architectural decision in the project.

```text
Control plane                        Media plane
─────────────                        ───────────
Next.js web app                      Voice session workers
FastAPI REST API                     Long-lived stateful processes
Postgres + pgvector                  Bidirectional audio streaming
Redis                                STT / LLM / TTS orchestration
Background workers                   Sub-second latency budget
Request/response, seconds            Persistent connections, milliseconds
Serverless-friendly                  NOT serverless-deployable
```

| Layer | Choice | Role |
| --- | --- | --- |
| Frontend | **Next.js** + React + TypeScript | Web app: assistant editor, calls, knowledge, contacts, analytics |
| Styling | **Tailwind CSS** + shadcn/ui (or existing repo primitives) | Consistent UI primitives |
| Backend (control plane) | **FastAPI** on **Python 3.12+** | REST API, orchestration, tenant/workspace authorization, health checks |
| Validation | **Pydantic** | Request/response schemas and settings |
| ORM / DB access | **SQLAlchemy** + **asyncpg** | Async data access and repositories |
| Migrations | **Alembic** | Every schema change, no exceptions |
| Database | **PostgreSQL 16** + **pgvector** | Primary store and vector store; no separate vector DB |
| Cache / state | **Redis 7** | Sessions, cache, rate limiting, job queues |
| Media plane | Python voice agent workers | Real-time call sessions - long-lived, not serverless |
| Realtime framework | **LiveKit Agents** (primary) vs **Pipecat** (alternative) | Selected in item 20a; pipeline stages sit behind Norma's own interfaces regardless |
| Background jobs | Redis-backed queue (ARQ or Celery) | Document parsing, crawling, embedding, post-call summarization, webhook delivery, campaign dialling, recording deletion |
| Backend testing | **pytest**, **httpx** | Unit, DB integration, authorization/tenant-isolation regression tests |
| Frontend testing | **Vitest** + React Testing Library; **Playwright Test** for E2E | Playwright is the required E2E runner; Jest is not introduced |
| Voice pipeline testing | Fixture-audio replay harness (item 22) | Full pipeline against mock providers, latency regression, barge-in/turn-detection behavior |
| Containers | **Docker** + **Docker Compose** | Full local stack, both planes |
| CI/CD | **GitHub Actions** (item 59) | Lint, typecheck, tests, voice pipeline + latency regression, E2E, build, migrate, staged deploy, health verification |

### LLM - two tiers, one provider

```text
Realtime turn model     - latency-critical, small, streaming - e.g. claude-haiku-4-5 class
Post-call model         - quality-critical, latency-tolerant  - e.g. claude-sonnet-5 class
```

A frontier model in the per-turn loop is explicitly rejected: first-token latency dominates
perceived call quality, and a large model there makes the p95 target unreachable. Both tiers sit
behind `LLMProvider`. Environment: `LLM_PROVIDER`, `LLM_REALTIME_MODEL`, `LLM_POSTCALL_MODEL`,
`ANTHROPIC_API_KEY`, configurable `ANTHROPIC_BASE_URL`.

### Embeddings

`EmbeddingProvider` -> `OpenAIEmbeddingProvider` (`text-embedding-3-small`, 1536 dims) for
production, `MockEmbeddingProvider` (deterministic) for tests. Configuration:
`OPENAI_API_KEY`, `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIMENSION=1536`.

### Speech

```text
SpeechToTextProvider  -> DeepgramSTT (primary, streaming + glossary boosting), one alternative
                          implementation, MockSTT (deterministic, fixture audio)
TextToSpeechProvider  -> ElevenLabsTTS (primary, low-latency streaming), one or more alternative
                          implementations, MockTTS (deterministic, silent audio)
```

> The two plans name different alternative vendors for both directions - see Open questions. The
> primary picks (Deepgram STT, ElevenLabs TTS) agree in both documents and are not in question.

### Telephony

`TelephonyProvider` -> `TwilioProvider` (primary), `TelnyxProvider` (alternative, EU number
economics), `SipTrunkProvider` (bring-your-own PBX/carrier), `MockTelephony` (tests: synthetic call
lifecycle, no real audio). Number provisioning and outbound-calling rules are per-country
configuration, not a global setting - verify local regulatory requirements before enabling those
flows in any new market; this is a launch blocker, not an implementation detail.

### Turn detection

Voice activity detection (Silero-class VAD) for speech boundaries, plus semantic end-of-turn
classification on top, with operator-configurable sensitivity. Barge-in is immediate TTS playback
cancellation.

### Retrieval

```text
Knowledge source (file / website / FAQ) -> Parser -> Normalized text -> Chunker
  -> OpenAI embedding -> pgvector(1536), filtered by organization + workspace + assistant
  -> Retrieval (hard latency budget, small top-k) -> Context builder -> Realtime LLM turn
  -> Spoken answer (+ source recorded for the operator)
```

Target retrieval budget: under 80ms. Pre-warmed connections, small top-k, per-assistant
chunk-count ceilings, embedding caching for repeated caller phrasings, and preloading
highest-frequency FAQ content into the prompt rather than retrieving it every turn.

### Orchestration

Service-based orchestration for the MVP. In-call tool calling is explicit, permission-checked
functions - not open-ended agency. LangGraph is reserved for post-MVP stateful multi-step
workflows, not the per-turn conversation loop.

### Provider rules

- No provider-specific code in routes or business services - always behind an interface.
- Providers stay stateless where possible.
- `MockEmbeddingProvider`, `MockSTT`, `MockTTS`, and `MockTelephony` must exist so the test suite
  never depends on a paid or live external API.

### Development container services

```text
norma-postgres
norma-redis
norma-api
norma-web
norma-voice      (media plane worker)
norma-worker     (background jobs)
```

Legacy `fonio-*` identifiers must not be introduced into new configuration.

## Monetization

Subscription with metered usage. Minutes are the primary billable unit; phone numbers, workspaces,
and contacts are secondary billable quantities. Billing is in the MVP - the product is
minute-metered, so metering and enforcement cannot be deferred without breaking unit economics.

| | Solo | Team | Scale |
| --- | --- | --- | --- |
| Target | up to ~20 calls/day | ~20-100 calls/day | 100+ calls/day |
| Included minutes | baseline | ~3x baseline | custom, higher |
| Concurrent calls | 1 | 3 | custom |
| Users | 1 | unlimited | unlimited |
| Phone numbers | 1 | 3 | custom |
| Workspaces | 1 | 3 | custom |
| Outbound + campaigns | - | included, carrier cost extra | included |
| BYO SIP trunk | - | included | included |
| Custom voice | - | - | included (post-MVP capability, item 69) |
| No-retention mode | - | - | included (see Open questions - item 70 is currently post-MVP) |
| Support | email | priority | account manager |

Monthly and annual billing with an annual discount. Overage minutes sell in fixed packages,
applied automatically within the billing period. Plan limits are enforced in the application:
concurrency gates call admission (excess calls forward or take a message, never queue
indefinitely); minute exhaustion triggers overage purchase or a configured degraded behavior,
never a silent mid-call failure; number/workspace/contact allowances are checked at creation time.

Usage and provider cost metadata is captured per call from the first release (item 54), even before
billing (item 55) is wired up, so gross margin per minute is visible from day one.

## UI/UX

Norma AI should feel like professional telephony infrastructure a non-technical office manager can
operate - confident, calm, fast. Not a chatbot toy, not an enterprise console.

### Design principles

Time-to-first-call under ten minutes; progressive disclosure (simple mode by default, advanced
settings one click away); every configuration change testable immediately; always show what the
assistant actually said; clear feedback and failure states; accessible.

### Core navigation

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

Navigation items appear only for features that are actually built.

### Routes

| Route | What's there |
| --- | --- |
| `/login`, `/register` | Authentication (feature 1, built) |
| `/settings` | Profile, organization/workspace config, members, invitations (features 2, 3, 8) |
| `/overview` | Authenticated home (feature 7) |
| `/assistants`, `/assistants/[id]` | Assistant list and the split-view editor (features 11-13) |
| `/assistants/[id]/test-call` | In-browser test call (item 21) |
| `/knowledge` | Sources, upload, crawl status, unanswered-questions inbox (items 14-19) |
| `/contacts`, `/contacts/[id]` | Contact list and detail with call history (item 37) |
| `/appointments` | Appointment list, calendar connections (items 38-39) |
| `/campaigns`, `/campaigns/[id]` | Outbound campaign management (items 44-46) |
| `/calls`, `/calls/[id]` | Call feed and the transcript/audio/tool-call detail view (items 26-28) |
| `/numbers` | Provisioning flow, assignment to assistants (item 24) |
| `/integrations` | Connected integrations, API keys (items 42-43) |
| `/analytics` | Overview cards, trend charts, top intents (item 48) |

### Onboarding

The first-run experience is the highest-leverage screen in the product: pick a use-case template,
enter the business website (Norma crawls it), pick a voice and hear a preview, test-call the
assistant from the browser, then claim a phone number. The operator hears their own assistant talk
before being asked for payment details.

### Assistant editor

Split view: configuration on the left, a live test call on the right. Changes apply to the next
test call without a save-and-reload cycle. Sections: identity/voice, greeting, behavior, knowledge,
skills, telephony, post-call handling.

### Calls

The call list is where operators spend their time: time, direction, caller, duration, outcome,
whether a human was involved. The detail view shows audio synced to the transcript, turn-by-turn
transcript with interruptions marked, summary and extracted fields, which knowledge sources were
used per answer, every tool call with arguments/result, and per-turn latency (available, not front
and center). An operator must be able to see exactly why the assistant said what it said - this is
the feature that rebuilds trust after the first mistake.

### Knowledge

Sources show type, status, last updated, failure reason. Website sources show crawled page counts.
Unanswered questions surface as an actionable inbox, not a buried report.

### Numbers

Provisioning flow states country/area and regulatory-document requirements up front. Clear
indication of which assistant answers which number.

### Analytics

Overview cards: calls handled, minutes used against allowance, resolution rate, transfer rate,
appointments booked. Trend charts. Top intents and top unanswered questions.

### Error UX

Never show internal exception names or stack traces. Distinguish a configuration problem the
operator can fix, a provider outage they cannot, and a plan limit they can lift by upgrading:

> "This number couldn't be provisioned - Germany requires a local address on file. Add one in
> Settings to continue."

### Responsive & accessibility

Desktop/laptop are first-class; tablet usable; mobile focuses on reading the call feed and
listening to a call. Semantic HTML, keyboard navigation, visible focus states, accessible labels,
sufficient contrast, screen-reader-friendly controls, keyboard-operable audio players with
transcript alternatives.

### Localization

The app UI and marketing site need i18n from an early stage - the assistant itself speaks many
languages, so the buyer often does not read English. Plan for it in the component layer even
though only English ships in the MVP.

## Deployment

### Development

```text
norma-postgres    pgvector/pgvector:pg16          host port 5432
norma-redis       redis:7-alpine                  host port 6379
norma-api         ./apps/api                      host port 8000
norma-web         ./apps/web                      host port 3000
norma-voice       media plane worker (new)        host port 8080 (test-call signalling)
norma-worker      background jobs (new)            -
```

```text
Web:      http://localhost:3000
API:      http://localhost:8000
API docs: http://localhost:8000/docs
Health:   http://localhost:8000/api/v1/health
Voice:    ws://localhost:8080
```

Inside Compose, services reach each other by service name (`postgres:5432`, `redis:6379`) - never
`localhost`. A public tunnel (ngrok or equivalent) is required for inbound telephony webhooks and
media streams in development; document this in the dev setup (item 5). The Windows host may also
run a native PostgreSQL - do not assume port 5432 belongs to Docker without checking.

### Production topology

This is the selected target, not a shortlist. The media plane cannot go on Vercel, and a standard
autoscaling web service is risky for it: scale-down must never terminate a process holding a live
call.

| Concern | Target |
| --- | --- |
| Frontend | **Vercel** - Next.js; `npm run build` / `npm run start`; `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_VOICE_WS_URL` required |
| Control-plane API | **Render Docker Web Service**, existing `apps/api/Dockerfile`; `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, never `--reload` |
| Media plane | **Fly.io** (fallback AWS ECS/Fargate) - separate image/deploy/scaling from the API; connection-draining deploys; scales on concurrent sessions, not HTTP rate; validate with a real load test before launch |
| Background jobs | **Render Background Worker** |
| Database | **Render Managed PostgreSQL** - v16+, `CREATE EXTENSION vector;`, automated backups, secure connections |
| Redis | **Render Key Value**, via `REDIS_URL` |
| Object storage | **Amazon S3** from day one, behind a storage abstraction; recordings write from the media plane, so it needs its own credentials |
| Billing | **Stripe**, live mode at launch |
| Migrations | **Alembic**, controlled pre-deploy step - never on app startup; both planes must tolerate briefly running against a newly migrated schema |
| Health checks | `GET /api/v1/health`, `/database`, `/redis`, `/providers`, `/all`; media plane exposes its own health + capacity endpoint |
| Domain | `norma.ai`, `app.norma.ai`, `api.norma.ai`, `voice.norma.ai` - availability not assumed |

Note that call/transcript volume grows fast - plan partitioning or archival for `Call`,
`TranscriptTurn`, and `TurnMetric` before they become a problem, and keep them off the same hot
path as assistant configuration reads.

### Environment variables

Core (already in `.env.example`, unchanged):

```text
APP_NAME  APP_VERSION  ENVIRONMENT  DEBUG
DATABASE_URL
POSTGRES_DB  POSTGRES_USER  POSTGRES_PASSWORD  POSTGRES_HOST  POSTGRES_PORT
REDIS_URL
SECRET_KEY
CORS_ORIGINS
NEXT_PUBLIC_API_URL
```

New for this direction (`.env.example` does not have these yet - see Known plan-to-repo drift):

```text
NEXT_PUBLIC_VOICE_WS_URL

LLM_PROVIDER  LLM_REALTIME_MODEL  LLM_POSTCALL_MODEL
ANTHROPIC_API_KEY (already present)  ANTHROPIC_BASE_URL

STT_PROVIDER  DEEPGRAM_API_KEY
TTS_PROVIDER  ELEVENLABS_API_KEY

TELEPHONY_PROVIDER  TWILIO_ACCOUNT_SID  TWILIO_AUTH_TOKEN  TWILIO_WEBHOOK_SIGNING_SECRET

AWS_REGION  AWS_S3_BUCKET  AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY
STRIPE_SECRET_KEY  STRIPE_WEBHOOK_SECRET
```

`OPENAI_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION` are already present and already named
correctly (`EMBEDDING_DIMENSION`, matching both plans - no drift here). Prefer workload
identity/IAM roles over long-lived keys where the host supports them. Extend the repository's
existing settings system rather than duplicating environment parsing.

### Production security

HTTPS everywhere, WSS for media signalling; verified telephony webhook signatures (an
unauthenticated call webhook is a free-calls exploit); strong `SECRET_KEY`; restrictive production
CORS; rate limiting on auth, API keys, and outbound-call triggers; tenant isolation and
authorization on every endpoint including media-plane session establishment; signed short-lived
recording URLs; encrypted integration credentials; audit logging; outbound-call abuse controls
(per-organization spend caps, destination allow-lists).

### Compliance

Call-recording consent, configurable per assistant and required by jurisdiction; data residency as
a configurable deployment property, not a hardcoded region; data processing agreements with every
speech/LLM provider; documented retention and deletion behavior; where the EU is a target market,
GDPR and EU AI Act transparency obligations apply, including disclosing to callers that they are
speaking to an AI.

### CI/CD pipeline (item 59)

```text
Push -> Lint -> Type check -> Backend tests -> Frontend tests
     -> Voice pipeline + latency regression tests -> E2E tests
     -> Build (web, api, voice) -> Migrate
     -> Deploy (staged: api -> voice -> web) -> Health + synthetic test-call verification
```

### Launch gate

The MVP is not shippable until a real phone call to a real number, answered by a real assistant,
books a real calendar appointment, and delivers a correct transcript and summary - repeatedly,
within the latency budget, from a cold-started production deployment (item 62).

### Known plan-to-repo drift (expected, no action needed yet)

`.env.example` does not yet contain `LLM_PROVIDER`, `LLM_REALTIME_MODEL`, `LLM_POSTCALL_MODEL`,
`ANTHROPIC_BASE_URL`, `NEXT_PUBLIC_VOICE_WS_URL`, any `STT_`/`TTS_`/`TELEPHONY_` variable, or the
`AWS_*`/`STRIPE_*` variables - its AI values are otherwise already correctly named
(`EMBEDDING_DIMENSION`, matching both plans). `docker-compose.yml` has only `postgres`, `redis`,
`api`, `web` - no `norma-voice` or `norma-worker` service yet. Items 5, 9, 58, and 60 close these
gaps as they are built; this is expected sequencing, not a bug.

## Open questions

> Resolve these in `project-plan.md` / `build-plan.md`, then re-run `/overview`.

1. **STT alternative vendor disagrees between the two plans.** `build-plan.md` -> Planning
   decisions says "AssemblyAI evaluated as alternative" for STT. `project-plan.md` §5 Speech lists
   the `SpeechToTextProvider` alternatives as `ElevenLabsSTT` only - AssemblyAI is not mentioned
   anywhere in `project-plan.md`. Pick one before item 9 scaffolds the interface and its adapters.
2. **TTS alternative vendors disagree between the two plans.** `build-plan.md` names "Cartesia and
   Azure Neural" as TTS alternatives. `project-plan.md` §5 Speech lists `DeepgramTTS` and
   `AzureNeuralTTS` - Cartesia is not mentioned anywhere in `project-plan.md`, and Deepgram is not
   mentioned in `build-plan.md`'s TTS alternatives. Azure Neural is the one name both documents
   agree on. Resolve before item 9.
3. **Stale item-number reference for the realtime-framework decision.** `project-plan.md` §5 Media
   plane says the LiveKit Agents vs. Pipecat decision is "selected during item 17 and the decision
   is recorded here." Under the current numbering, item 17 is "Document processing pipeline" (a
   Knowledge item) - the actual framework-selection work is item **20a** ("Framework selection and
   media transport"). Fix the reference in `project-plan.md` so nobody looks for the recorded
   decision in the wrong place.
4. **Zero data retention mode may need to be in the MVP, not post-MVP.** `project-plan.md` §6's
   plan table lists "No-retention mode" as included on the **Scale** plan - one of the three
   MVP-launch tiers - and §4's sensitive-data policy already describes the capability as part of
   the data model. `build-plan.md` defers "Zero data retention mode" entirely to post-MVP item
   **70**. If Scale-tier customers are sold this at MVP launch, item 70 needs to move into the MVP
   list before item 55 (Subscription billing) locks in plan features - or the Scale-plan promise
   needs to be scoped down until item 70 ships.
