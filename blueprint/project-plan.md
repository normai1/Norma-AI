# Project Plan

> One of the two planning docs for **Norma AI**. This document defines the product problem, target users, MVP scope, data model, technology choices, monetization, UI/UX direction, and deployment requirements. It is the product-level source of truth for feature planning.
>
> Norma AI is an independent product built to compete in the AI voice agent category. It targets feature parity with established players in that category, but uses its own name, branding, copy, visual design, and implementation. No third-party trademarks, marketing text, or design assets are reproduced.

---

## 1. Problem - What problem are we solving?

**Norma AI** is an AI phone assistant platform. It answers a business's inbound calls, holds a natural spoken conversation with the caller, completes the requested task, and reports back — 24 hours a day.

Small and medium-sized businesses lose revenue every time a phone rings and nobody picks up. The front desk is busy with a customer in the room, the owner is on a job site, the clinic is closed, the call comes in at 9pm. Voicemail converts poorly, callbacks are expensive, and human answering services are slow to scale and inconsistent in quality.

Norma AI gives a business a phone assistant that:

- Answers every inbound call immediately, in the caller's language.
- Understands what the caller wants.
- Answers questions using the business's own information.
- Books, reschedules, or cancels appointments against a live calendar.
- Captures leads, orders, and service requests as structured data.
- Forwards the call to a human when the situation requires one.
- Sends a transcript, summary, and extracted fields to the team afterwards.
- Places outbound calls and runs calling campaigns.

### Core product idea

> **Never miss a call again.**

### Primary MVP goal

The MVP must prove one complete loop, reliably and fast enough to feel human:

```text
Caller dials a Norma number
  ↓
Telephony leg established
  ↓
Streaming speech-to-text
  ↓
Turn detection
  ↓
Assistant configuration + retrieved business knowledge
  ↓
LLM turn
  ↓
Streaming text-to-speech
  ↓
Caller hears a reply (target < 900 ms from end of caller speech)
  ↓
Tool calls during the call (availability, booking, transfer, API request)
  ↓
Call ends
  ↓
Recording, transcript, summary, extracted variables
  ↓
Delivery to the team + webhook/integration push
  ↓
Minutes metered against the subscription
```

### What "good" means for this product

Latency, interruption handling, and answer accuracy are the product. A feature-complete assistant that responds in three seconds or talks over the caller is a failed product. The following are treated as hard requirements, not polish:

- **Time to first audio** after the caller stops speaking: p50 under 700 ms, p95 under 1,200 ms.
- **Barge-in**: caller speech interrupts assistant playback within 200 ms.
- **Turn detection** must not cut the caller off mid-sentence or leave dead air after they finish.
- **Grounding**: the assistant does not invent prices, hours, availability, or policies. Unknown means "I'll have someone call you back."
- **Call completion**: telephony legs must not drop, and a crashed session must fail over to forwarding or voicemail rather than silence.

### Product constraints

- Tenant data must be isolated by organization at every layer, including audio and transcripts.
- Backend authorization is mandatory; frontend checks are not security boundaries.
- LLM output is untrusted until validated by application policy.
- Retrieved knowledge and caller speech are data, not instructions.
- In-call tool calls must respect the assistant's configured permissions; the assistant can only do what the operator explicitly enabled.
- Speech, LLM, and telephony providers must remain replaceable behind application abstractions.
- Call recording is subject to consent law and must be configurable and deletable per organization.
- The real-time media path runs as long-lived stateful processes; it cannot be deployed as serverless functions.
- The MVP should stay operable by a small team — no Kubernetes, no microservice sprawl.

### Explicit MVP exclusions

The MVP does not include:

- WhatsApp, web chat, or email channels (post-MVP; voice first).
- A hosted PBX or softphone.
- Custom/cloned voices.
- SSO/SAML.
- Reseller or white-label multi-organization management.
- Native mobile applications.
- Multi-region active-active media routing.
- Autonomous agents with unrestricted tool access.
- A separate dedicated vector database.

---

## 2. Users - Who is this for?

### Primary users

Small and medium-sized businesses whose phone is a primary revenue channel and whose staff cannot always answer it.

### Buyer vs. operator vs. end user

Three distinct people interact with Norma AI, and the UI must serve all three:

- **Buyer** — owner or office manager. Wants missed calls to stop. Judges the product in the first ten minutes.
- **Operator** — the person who configures the assistant, reviews calls, and corrects mistakes. Uses the product daily.
- **Caller** — the end customer on the phone. Never sees the UI. Only experiences latency, voice quality, and whether the task got done.

### Target segments

#### Medical, dental, and veterinary practices

High call volume during hours when staff are with patients. Needs appointment booking, rescheduling, prescription and hours questions, and urgent-case escalation.

> "I need to move my Thursday cleaning to next week."

#### Tradespeople and field service

Owner is on site and cannot answer. Needs lead qualification, job details captured, and an SMS summary.

> "My boiler is leaking, can someone come out today?"

#### Hotels, spas, salons, and restaurants

Booking, availability, and repetitive guest questions.

> "Do you have a double room for the 14th, and is parking included?"

#### Property management and real estate

Tenant maintenance requests, viewing bookings, routing to the right manager.

> "The lift in building C is out again."

#### Car dealerships and repair shops

Service booking, parts and status questions, sales lead capture.

#### Solo professionals and small agencies

A one-person business that needs to look staffed.

#### Government offices and larger enterprise front desks

Routing, FAQ deflection, and overflow handling with stricter compliance requirements.

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

An organization has one subscription. Workspaces scope assistants, numbers, contacts, and call history. Users belong to an organization and are granted access to one or more workspaces. Users may belong to multiple organizations.

---

## 3. Features - What does the MVP need?

### Authentication and accounts

- Registration, login, logout.
- Secure password hashing.
- Access/refresh session management.
- Session invalidation.
- User profile management.

### Organizations and workspaces

- Organization creation and settings.
- Workspace creation and assignment.
- Membership and invitations.
- Organization/workspace switching.
- Tenant isolation.

### RBAC

- Owner, Admin, Member, Viewer.
- Permission checks on all protected resources.
- Organization- and workspace-scoped authorization.
- Cross-tenant access prevention.

### Assistant configuration

The assistant is the central configurable object. An operator must be able to set, without writing code:

- Name and workspace.
- Voice, from a catalogue of provider voices, with in-browser preview.
- Language, including per-assistant multilingual behaviour.
- Greeting/welcome message, and whether it can be interrupted.
- Persona and behavioural instructions.
- Speech rate.
- Turn-detection sensitivity.
- Creativity (LLM temperature, bounded).
- Optional ambient background sound.
- Business hours behaviour.
- What to do when the assistant cannot resolve the request.
- Which skills and tools are enabled.
- Guided "simple setup" mode and a full advanced mode over the same configuration.

Assistant configuration is versioned. Every call records which version answered it.

### Prompts

- Reusable prompt templates by use case (receptionist, support, scheduling, answering machine, field service, order intake).
- Prompt versioning with rollback.
- Variable interpolation from assistant, workspace, and caller context.

### Knowledge

The assistant answers from the organization's own information:

- File upload: PDF, DOCX, Markdown, TXT.
- Website ingestion: crawl a supplied domain and use the extracted content as background knowledge.
- Manual FAQ entries.
- Glossary of terms, abbreviations, product names, and pronunciation overrides — used both for STT biasing and TTS pronunciation.
- Parsing, normalization, chunking, embedding, vector indexing.
- Tenant- and workspace-scoped semantic retrieval, tight enough to run inside a call turn.
- Source traceability so an operator can see which document produced an answer.
- Optional live internet search as an explicitly enabled skill.

### Telephony

- Platform-provisioned phone numbers, with country selection.
- Multiple numbers per assistant and per workspace.
- Bring-your-own numbers via SIP trunk integration.
- Inbound call answering and routing to the correct assistant.
- Outbound calls.
- Live call forwarding/transfer to a human, including international forwarding.
- DTMF tone sending for navigating external IVR menus.
- Per-plan concurrent call limits with admission control.
- Graceful degradation: on session failure, forward or take a message rather than drop.

### Real-time voice engine

- Bidirectional streaming audio over the telephony provider's media transport.
- Streaming STT with partial results.
- Voice activity detection plus semantic turn detection.
- Streaming TTS with sentence-level chunking so audio starts before the LLM finishes.
- Barge-in and playback cancellation.
- Filler/backchannel handling for tool calls that take time.
- Per-turn latency instrumentation across every leg.
- In-browser test call so an operator can try an assistant without dialling.

### In-call skills

- Answer questions from knowledge.
- Check calendar availability and book, reschedule, or cancel appointments.
- Capture structured fields (name, phone, address, reason for call, order details).
- Look up and create records in a connected CRM.
- Make an authenticated HTTP request mid-call and use the response in the conversation.
- Transfer to a human.
- Take a message.
- Send an SMS or email during or after the call.

### Post-call processing

- Call recording, with configurable retention and automatic deletion.
- Full transcript with speaker turns and timestamps.
- Generated summary and call outcome/disposition.
- Structured variable extraction against an operator-defined schema.
- Delivery by email and SMS.
- Outbound webhook.
- Push into connected integrations.
- Post-call authenticated API request.

### Contacts

- Contact records scoped to a workspace.
- Automatic creation and matching from inbound calls by phone number.
- Call history per contact.
- Import and export.
- Contact count is a metered, billable quantity.

### Outbound campaigns

- Contact lists.
- Campaign scheduling with calling windows and time-zone awareness.
- Retry policy for no-answer and busy.
- Per-contact outcome tracking.
- Do-not-call suppression list.
- Consent and regulatory gating per destination country.

### Integrations and API

- Integration framework with credential storage and per-integration scopes.
- Native calendar integrations (Google Calendar, Microsoft 365).
- Native CRM integrations (start with HubSpot; framework must accept more).
- Inbound webhook to trigger outbound calls or update assistant data.
- Outbound webhooks for call events.
- Public REST API with API keys, scoped per organization.

### Analytics

- Calls answered, missed, and handled end-to-end.
- Minutes consumed against plan allowance.
- Average handle time and resolution rate.
- Transfer rate and reason.
- Appointment conversion.
- Latency percentiles.
- Top caller intents and top unanswered questions.

### Guardrails

- Prompt-injection resistance for retrieved and caller-supplied text.
- Topic and action allow-lists per assistant.
- Refusal to state prices, availability, or policy not present in knowledge.
- PII handling rules in transcripts and logs.
- Tool-permission enforcement independent of the model.
- Output validation before a spoken commitment is made.

### Auditability

- **Activity** — user-facing business timeline (call handled, appointment booked, contact created).
- **AuditLog** — security/compliance record (login, role change, number provisioned, recording deleted, integration connected, API key issued).

These remain deliberately distinct.

### Billing

- Subscription plans with included minutes.
- Metered overage in minute packages.
- Per-additional-number and per-additional-workspace pricing.
- Contact-count add-ons.
- Monthly and annual billing.
- Plan-limit enforcement in the application, not just on the invoice.

### Platform

- REST API with versioning.
- Health checks for application, database, Redis, media plane, and each external provider.
- Structured logging with call and turn correlation IDs.
- Automated tests including latency and voice-pipeline regression tests.
- Dockerized development.
- Alembic migrations.
- Production configuration and deployment.

---

## 4. Data - What are we storing?

### Identity

- Users, email addresses, password hashes, profile information, account status, session metadata.

### Organizations

- Organizations, organization settings, members, roles, invitations.
- Workspaces, workspace membership, workspace settings (timezone, locale, business hours, currency).

### Assistants

- Assistants.
- AssistantVersion — the full immutable configuration snapshot that answered a given call.
- Voice selection, language configuration, speech parameters, sensitivity, creativity.
- Enabled skills and tool permissions.
- Greeting and fallback behaviour.
- Prompt template and prompt version references.

### Telephony

- PhoneNumbers: E.164 number, country, provider, provider resource ID, assigned assistant, workspace.
- SipTrunks: credentials/reference, direction, allowed IPs, assigned assistant.
- ForwardingTargets: name, number, hours, priority.

### Calls

- Call: direction, from/to, assistant version, workspace, start/end, duration, billable seconds, outcome, disposition, cost, provider call SID.
- CallLeg: inbound leg, forwarded leg, outbound leg.
- TranscriptTurn: speaker, text, start/end offsets, confidence, interrupted flag.
- Recording: object-storage reference, duration, retention policy, deletion timestamp.
- CallSummary: generated summary, model, prompt version.
- ExtractedVariable: field name, value, type, confidence, schema reference.
- ToolInvocation: tool name, arguments, result, latency, success/failure — one row per in-call tool call.
- TurnMetric: STT finalization, retrieval, LLM first token, LLM complete, TTS first byte, audio out. One row per turn.

### Knowledge

- KnowledgeSource: type (file, website, manual FAQ), status, error, owner, workspace.
- Document: filename, MIME type, object-storage reference, processing status, processing error.
- CrawledPage: URL, fetched-at, extracted text, content hash.
- Chunk: text, ordering, metadata, source reference.
- Embedding: `vector(1536)`.
- GlossaryEntry: term, meaning, phonetic spelling, STT boost weight.
- UnansweredQuestion: transcript reference, question text, occurrence count, status, proposed answer, approving user.

### Contacts and appointments

- Contact: name, phone numbers, email, workspace, custom fields, do-not-call flag, source.
- Appointment: contact, assistant, external calendar event ID, start/end, status, service type.
- CalendarConnection: provider, credentials reference, calendar ID, availability rules.

### Campaigns

- Campaign: name, assistant, contact list, calling window, timezone, retry policy, status.
- CampaignContact: contact, attempt count, last attempt, outcome.
- SuppressionEntry: phone number, reason, added-by.

### Integrations and API

- Integration: provider, credentials reference, scopes, workspace, status, last sync.
- Webhook: URL, secret, subscribed events, delivery attempts.
- ApiAction: name, method, URL template, headers, auth reference, request/response schema, timing (in-call or post-call).
- ApiKey: hashed key, scopes, organization, last used.

### Billing and usage

- Subscription: plan, interval, status, provider subscription ID, included minutes, concurrency limit, number allowance, contact allowance.
- UsageRecord: organization, period, metric (minutes, calls, contacts, numbers), quantity, source call reference.
- Overage: package purchases within a period.
- Invoice references from the billing provider.

### Audit and observability

- Activity events.
- AuditLog events.
- Retrieval traces and retrieved chunk references.
- Provider/model metadata per call and per turn.
- Latency, token, and cost metrics.
- Document and crawl processing events.
- Operational errors with call correlation IDs.

### Sensitive-data policy

Call audio, transcripts, and extracted fields are the most sensitive data in the system. Rules:

- Recording is off by default and configurable per assistant.
- Retention is configurable per organization, with automatic deletion.
- A "no retention" mode discards audio and transcript text after post-call delivery, keeping only metadata and metrics.
- Transcript text must never appear in application logs.
- Consent announcements are configurable per assistant and required where the destination jurisdiction demands them.

### Vector configuration

Embedding provider for the MVP is **OpenAI `text-embedding-3-small`** behind the `EmbeddingProvider` abstraction. Vector dimension:

```text
1536
```

Column type:

```text
vector(1536)
```

Model and dimension are part of the retrieval contract. Changing either requires an explicit migration and reindex; embeddings of different dimensions must not share a column. A deterministic `MockEmbeddingProvider` is used in tests.

### Object storage

Recordings and uploaded documents live in S3-compatible object storage from the first production deployment. Local filesystem is a development-only adapter. Production must not depend on container-local disk.

---

## 5. Tech - What stack are we using?

The system splits into two planes with different runtime characteristics. This split is the single most important architectural decision in the project.

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

### Frontend

- Next.js.
- React.
- TypeScript.
- Tailwind CSS.
- shadcn/ui or the repository's existing UI primitives.
- WebRTC or WebSocket audio for the in-browser test call.

### Backend (control plane)

- Python 3.12+.
- FastAPI.
- Pydantic.
- SQLAlchemy.
- asyncpg.
- Alembic.
- Redis client.
- pytest, httpx.

### Media plane

- Python voice agent workers.
- **LiveKit Agents** as the primary candidate for the real-time orchestration framework, with **Pipecat** as the evaluated alternative. One is selected during item 17 and the decision is recorded here.
- Whichever is chosen, the pipeline stages sit behind Norma's own interfaces so the framework itself is replaceable.

### Speech

Both directions are provider-agnostic behind abstractions:

```text
SpeechToTextProvider
    ├── DeepgramSTT          (streaming, keyword/glossary boosting)
    ├── ElevenLabsSTT        (alternative)
    └── MockSTT              (deterministic tests, fixture audio)

TextToSpeechProvider
    ├── ElevenLabsTTS        (low-latency streaming model)
    ├── DeepgramTTS          (alternative)
    ├── AzureNeuralTTS       (breadth of languages, EU regions)
    └── MockTTS              (deterministic tests, silent audio)
```

Selection criteria are streaming latency, language coverage, interruption behaviour, and regional hosting. Voice catalogue breadth is a marketing-relevant number; the abstraction should allow aggregating voices from more than one provider.

### Turn detection

- Voice activity detection (Silero-class VAD) for speech boundaries.
- Semantic end-of-turn classification on top of VAD, so the assistant does not answer mid-sentence.
- Configurable sensitivity exposed to the operator.
- Barge-in handled by immediate TTS playback cancellation.

### LLM

Provider-agnostic behind `LLMProvider`. Two tiers, because the requirements differ:

```text
Realtime turn model     — latency-critical, small, streaming
                          e.g. claude-haiku-4-5 class
Post-call model         — quality-critical, latency-tolerant
                          summaries, extraction, learning candidates
                          e.g. claude-sonnet-5 class
```

The previous plan's choice of a frontier model for the conversational turn is explicitly rejected: first-token latency dominates perceived call quality, and a large model in the turn loop makes the p95 target unreachable. The exact model is configuration.

Environment: `LLM_PROVIDER`, `LLM_REALTIME_MODEL`, `LLM_POSTCALL_MODEL`, `ANTHROPIC_API_KEY`, configurable `ANTHROPIC_BASE_URL`.

### Embeddings

```text
EmbeddingProvider
    ├── OpenAIEmbeddingProvider
    │      └── text-embedding-3-small → 1536 dimensions
    └── MockEmbeddingProvider
```

Configuration: `OPENAI_API_KEY`, `EMBEDDING_MODEL=text-embedding-3-small`, `EMBEDDING_DIMENSION=1536`.

### Telephony

```text
TelephonyProvider
    ├── TwilioProvider       (broad country coverage, mature media streaming)
    ├── TelnyxProvider       (alternative, often better EU number economics)
    ├── SipTrunkProvider     (bring-your-own PBX/carrier)
    └── MockTelephony        (tests: synthetic call lifecycle, no real audio)
```

Number provisioning, regulatory documentation requirements, and outbound calling rules differ significantly by country and must be treated as per-country configuration rather than a global setting. If India is an early market, verify current TRAI/DoT requirements for number provisioning, outbound automated calling, and caller-ID before enabling those flows — this is a legal blocker, not an implementation detail.

### Retrieval

```text
Knowledge source (file / website / FAQ)
 ↓
Parser
 ↓
Normalized text
 ↓
Chunker
 ↓
OpenAI embedding
 ↓
pgvector(1536), filtered by organization + workspace + assistant
 ↓
Retrieval (hard latency budget, top-k small)
 ↓
Context builder
 ↓
Realtime LLM turn
 ↓
Spoken answer (+ source recorded for the operator)
```

Retrieval sits inside the latency budget. Target under 80 ms. Techniques that help: pre-warmed connections, small top-k, per-assistant chunk-count ceilings, caching of embeddings for repeated caller phrasings, and preloading the assistant's highest-frequency FAQ content into the prompt rather than retrieving it.

### Orchestration

- Service-based orchestration for the MVP.
- In-call tool calling is implemented as explicit, permission-checked functions, not open-ended agency.
- LangGraph is reserved for post-MVP stateful multi-step workflows, not per-turn conversation.

### Background jobs

- Redis-backed queue (ARQ or Celery) for document parsing, website crawling, embedding, post-call summarization, webhook delivery with retries, campaign dialling, and recording deletion.
- Campaign dialling requires a scheduler with per-organization concurrency limits.

### Testing

Frontend:

- **Vitest** for unit and component tests.
- **React Testing Library** for component behaviour and accessibility.
- **Playwright Test** for E2E. Playwright is the required E2E runner. Jest is not introduced.

Backend:

- pytest, httpx.
- Database integration tests.
- Authorization and tenant-isolation regression tests.
- Deterministic provider mocks for STT, TTS, LLM, embeddings, and telephony.

Voice pipeline (new, and non-negotiable):

- Fixture-audio conversation replay against the full pipeline with mock providers.
- Latency regression tests that fail the build when p95 time-to-first-audio exceeds budget.
- Barge-in and turn-detection behavioural tests.
- Tool-permission tests proving a disabled skill cannot be invoked by any model output.

### Infrastructure

- Docker.
- Docker Compose for local development.
- GitHub Actions for CI/CD.

### Development container services

```text
norma-postgres
norma-redis
norma-api
norma-web
norma-voice      (media plane worker)
norma-worker     (background jobs)
```

Legacy `fonio-*` identifiers must not be introduced into new configuration and should be removed from existing configuration when safe.

### Local networking

Inside Docker:

```text
API   → postgres:5432
API   → redis:6379
Voice → redis:6379
Voice → postgres:5432
```

From the host/browser:

```text
Web   → localhost:3000
API   → localhost:8000
Voice → localhost:8080  (test-call signalling)
```

Inbound telephony webhooks and media streams require a public tunnel in development (ngrok or equivalent). This must be documented in the development setup.

---

## 6. Monetize - How will this make money?

Subscription with metered usage. Minutes are the primary unit; phone numbers, workspaces, and contacts are secondary billable quantities.

### Plan structure

| | Solo | Team | Scale |
|---|---|---|---|
| Target | up to ~20 calls/day | ~20–100 calls/day | 100+ calls/day |
| Included minutes | baseline allowance | ~3× baseline | custom, higher |
| Concurrent calls | 1 | 3 | custom |
| Users | 1 | unlimited | unlimited |
| Phone numbers | 1 | 3 | custom |
| Workspaces | 1 | 3 | custom |
| Assistants | unlimited | unlimited | unlimited |
| Outbound + campaigns | — | included, carrier cost extra | included |
| BYO SIP trunk | — | included | included |
| Custom voice | — | — | included |
| No-retention mode | — | — | included |
| SLA | standard | standard | custom |
| Support | email | priority | account manager |

Monthly and annual billing, with an annual discount. A money-back window reduces purchase friction for a product that has to be trusted with customer calls.

### Metered and add-on revenue

- Overage minutes sold in fixed packages, applied automatically within the billing period.
- Additional phone numbers, monthly.
- Additional workspace connections, monthly.
- Additional contact capacity in blocks.
- Outbound carrier charges passed through by destination country.

### Enforcement

Plan limits are enforced in the application:

- Concurrency limit gates call admission; excess calls forward or take a message rather than queue indefinitely.
- Minute exhaustion triggers overage purchase or a configured degraded behaviour, never a silent failure mid-call.
- Number, workspace, and contact allowances are checked at creation time.

Usage and provider cost metadata must be captured per call from the first release, even before billing is wired up, so unit economics are visible from day one. Gross margin per minute is the number that determines whether this business works.

### Future enterprise

SSO/SAML, advanced RBAC, custom contractual agreements, dedicated infrastructure, custom integrations, custom pricing.

---

## 7. UI/UX - How should this look and feel?

Norma AI should feel like professional telephony infrastructure that a non-technical office manager can operate. Confident, calm, fast. Not a chatbot toy, not an enterprise console.

### Design principles

- Time-to-first-call under ten minutes.
- Progressive disclosure: simple mode by default, advanced settings one click away.
- Every configuration change testable immediately.
- Show what the assistant actually said, always.
- Clear feedback and clear failure states.
- Accessible.

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

### Onboarding

The first-run experience is the highest-leverage screen in the product:

1. Pick a use case template.
2. Enter the business website; Norma crawls it for background knowledge.
3. Pick a voice and language, hear a preview.
4. Test-call the assistant from the browser immediately.
5. Claim a phone number or connect an existing one.

The operator should hear their own assistant talk before they are asked for payment details.

### Assistant editor

Split view. Configuration on the left, a live test call on the right. Changes apply to the next test call without a save-and-reload cycle. Sections: identity and voice, greeting, behaviour, knowledge, skills, telephony, post-call handling.

### Calls

The call list is where operators spend their time. Each row: time, direction, caller, duration, outcome, and whether a human was involved. The detail view shows:

- Audio player synchronized to the transcript.
- Turn-by-turn transcript with interruptions marked.
- Summary and extracted fields.
- Which knowledge sources were used for each answer.
- Every tool call, with arguments and result.
- Per-turn latency, available but not front and centre.

An operator must be able to see exactly why the assistant said what it said. This is the feature that builds trust after the first mistake.

### Knowledge

Sources show type, status, last updated, and failure reason. Website sources show crawled page counts. Unanswered questions surface as an actionable inbox, not a buried report.

### Numbers

Provisioning flow with country, area, and regulatory-document requirements stated up front. Clear indication of which assistant answers which number.

### Analytics

Overview cards: calls handled, minutes used against allowance, resolution rate, transfer rate, appointments booked. Trend charts. Top intents and top unanswered questions.

### Error UX

Never show internal exception names or stack traces. Distinguish clearly between:

- A configuration problem the operator can fix.
- A provider outage they cannot.
- A plan limit they can lift by upgrading.

> "This number couldn't be provisioned — Germany requires a local address on file. Add one in Settings to continue."

### Responsive behaviour

Desktop and laptop are first-class. Tablet usable. Mobile focuses on the two things an owner does away from a desk: read the call feed and listen to a call.

### Accessibility

Semantic HTML, keyboard navigation, visible focus states, accessible labels, sufficient contrast, screen-reader-friendly controls. Audio players need keyboard controls and transcript alternatives.

### Localization

The application UI and the marketing site both need i18n from an early stage — the assistant speaks many languages, so the buyer often does not read English. Plan for it in the component layer even if only English ships in the MVP.

---

## 8. Deployment - Where and how will this ship?

### Development

Docker Compose runs:

```text
norma-postgres
norma-redis
norma-api
norma-web
norma-voice
norma-worker
```

Local endpoints:

```text
Web:      http://localhost:3000
API:      http://localhost:8000
API docs: http://localhost:8000/docs
Health:   http://localhost:8000/api/v1/health
Voice:    ws://localhost:8080
```

A public tunnel is required for telephony webhooks and media streams in development.

### Production topology

```text
Frontend         → Vercel (Next.js)
Control-plane API→ Render Docker Web Service
Media plane      → Fly.io (or AWS ECS/Fargate) — long-lived stateful processes
Background jobs  → Render Background Worker
Database         → Render Managed PostgreSQL with pgvector
Redis            → Render Key Value
Object storage   → Amazon S3
Billing          → Stripe
```

The media plane cannot go on Vercel, and putting it on a standard autoscaling web service is risky: scale-down must never terminate a process holding a live call. Requirements for the media host:

- Long-lived WebSocket and UDP/RTP connections.
- Region selection close to the telephony provider's media edge.
- Connection-draining deploys that let in-flight calls finish.
- Scaling on concurrent sessions, not HTTP request rate.

Fly.io is the initial choice for regional placement and connection handling. AWS ECS/Fargate is the fallback if Fly's constraints bite. This decision must be validated with a real load test before launch, not assumed.

### Frontend deployment

Host: Vercel. Build `npm run build`, start `npm run start`. Required configuration: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_VOICE_WS_URL`.

### Control-plane deployment

Host: Render, Docker runtime, existing `apps/api/Dockerfile`.

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

No `--reload` in production.

### Media-plane deployment

Separate image, separate deploy, separate scaling policy from the API. Health endpoint reports active session count and capacity. Deploys drain rather than kill.

### Database

Render Postgres, PostgreSQL 16 or a compatible supported version, `CREATE EXTENSION vector;`, automated backups, secure connections, Alembic migrations.

Note that call and transcript volume grows fast. Plan partitioning or archival for `Call`, `TranscriptTurn`, and `TurnMetric` before they become a problem, and keep them out of the same hot path as assistant configuration reads.

### Redis

Render Key Value via `REDIS_URL`. Used for job queues, session/concurrency accounting, rate limiting, and caching.

### Object storage

Amazon S3 from day one, behind a storage abstraction. Recordings are written from the media plane, so the media host needs its own credentials.

```text
AWS_REGION
AWS_S3_BUCKET
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

Prefer IAM roles or workload identity over long-lived keys where the host supports them. Recordings must be served through signed, short-lived URLs — never public objects.

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
NEXT_PUBLIC_VOICE_WS_URL
```

AI:

```text
LLM_PROVIDER
LLM_REALTIME_MODEL
LLM_POSTCALL_MODEL
ANTHROPIC_API_KEY
ANTHROPIC_BASE_URL
OPENAI_API_KEY
EMBEDDING_MODEL
EMBEDDING_DIMENSION
```

Speech:

```text
STT_PROVIDER
DEEPGRAM_API_KEY
TTS_PROVIDER
ELEVENLABS_API_KEY
```

Telephony:

```text
TELEPHONY_PROVIDER
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_WEBHOOK_SIGNING_SECRET
```

Storage and billing:

```text
AWS_REGION
AWS_S3_BUCKET
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
```

Use the settings implementation already present in the repository before adding new variables.

### Migrations

Alembic, executed as a controlled pre-deploy step rather than on application startup. Both the API and the media plane must tolerate running briefly against a newly migrated schema, so migrations are additive and backwards-compatible within a deploy.

### Health checks

```text
GET /api/v1/health
GET /api/v1/health/database
GET /api/v1/health/redis
GET /api/v1/health/providers
GET /api/v1/health/all
```

Media plane exposes its own health and capacity endpoint.

### Domain

```text
norma.ai
app.norma.ai
api.norma.ai
voice.norma.ai
```

Availability not assumed.

### Production security

- HTTPS everywhere; WSS for media signalling.
- Verified telephony webhook signatures — an unauthenticated call webhook is a free-calls exploit.
- Strong `SECRET_KEY`, secure secret handling.
- Restrictive production CORS.
- Rate limiting on auth, API keys, and outbound-call triggers.
- Tenant isolation and authorization on every endpoint, including media-plane session establishment.
- Signed short-lived URLs for recordings.
- Encrypted integration credentials.
- Audit logging.
- Outbound-call abuse controls: per-organization spend caps and destination allow-lists.

### Compliance

- Call recording consent, configurable per assistant and required by jurisdiction.
- Data residency as a configurable deployment property, not a hardcoded region.
- Data processing agreements with every speech and LLM provider.
- Documented retention and deletion behaviour.
- Where the EU is a target market, GDPR and EU AI Act transparency obligations apply, including disclosing to callers that they are speaking to an AI.

### CI/CD

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
Voice pipeline + latency regression tests
 ↓
E2E tests
 ↓
Build (web, api, voice)
 ↓
Migrate
 ↓
Deploy (staged: api → voice → web)
 ↓
Health + synthetic test-call verification
```

A synthetic call against staging after every deploy is the only reliable way to know the product still works.

### Launch gate

The MVP is not shippable until a real phone call to a real number, answered by a real assistant, books a real calendar appointment, and delivers a correct transcript and summary — repeatedly, within the latency budget, from a cold-started production deployment.
