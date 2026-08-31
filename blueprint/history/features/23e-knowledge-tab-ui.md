# Feature: Knowledge tab UI

**From build-plan:** feature 23e

**Status:** complete

## Goal

Replace the Knowledge tab's `"Knowledge management is coming soon."` placeholder in the assistant
editor with real file upload, website ingestion, manual FAQ management, and processing-status
display, wired to the assistant-scoped backend 23d already built. This is the last sub-feature of
build-plan item 23 - once it lands, item 23 itself gets checked off.

## Design reference

None new. Reuses the dark-theme component set and tab shell already locked in 23a
(`components/organizations/ui.tsx`: `Card`, `LoadingState`, `EmptyState`, `ErrorText`, `Button`)
and the local-status-badge precedent already established elsewhere on this same page (a
component-local badge, not a stretch of the shared `StatusBadge`'s narrow tone map).

## Architecture decisions (read before building)

- **Knowledge sources are their own resource, not part of `AssistantVersion`.** Unlike
  General/Technical/Custom Prompt (all one shared `handleSaveVersion` payload), the Knowledge tab
  gets its own independent state, its own `load()`/create/action callbacks, and its own API calls
  - matching this same page's existing Glossary section's pattern (own `useState`, own fetch,
  lazily loaded only when the tab is first viewed, matching Custom Prompt's lazy-fetch precedent
  for its own template list).
- **A real, load-bearing bug must be fixed before any upload works: `lib/api.ts`'s `buildHeaders`
  unconditionally sets `Content-Type: application/json` whenever the caller didn't set one.** A
  multipart file upload must send no `Content-Type` (the browser sets its own
  `multipart/form-data; boundary=...`), so posting a `FormData` body through the existing
  `authorizedJson`/`authorizedSend` helpers as-is would silently send the wrong content type and
  the upload would fail to parse server-side - not an obviously-labeled bug, easy to ship broken
  and not notice without an actual file upload check. Fix: skip the default `Content-Type` when
  `init.body instanceof FormData`. No existing multipart precedent exists anywhere in `apps/web`
  to copy from - this is the first one.
- **A new `apps/web/lib/knowledge-sources.ts` module** holds the API client functions and locked
  TypeScript types, matching the existing per-resource module pattern (`lib/assistants.ts`,
  `lib/prompt-templates.ts` precedent from 23c).
- **Knowledge-source *deletion* has no backend route today** (confirmed - `knowledge_sources.py`
  has create/list/get/process/recrawl only, no `DELETE`). Out of scope for this feature: adding a
  backend delete route is a bigger, separate change than "build the UI for what already exists."
  This is a real, known gap - flagged here rather than silently worked around, matching CLAUDE.md's
  instruction to flag gaps rather than expand scope to paper over them.
- **FAQ entries (inside a `manual_faq`-type source) *do* have full CRUD already** (`faq_entries.py`
  - create/list/update/delete), unlike the parent source itself. The UI reflects this real backend
  asymmetry: a manual-FAQ source's individual Q&A entries can be added/edited/deleted; the source
  itself (like every other source type) cannot be deleted from this UI.
- **Four build steps**, ordered so each leaves the app working and only the next step's own new
  capability depends on it:
  1. API client + `buildHeaders` fix + read-only list view (replaces the placeholder, proves the
     wiring and the status/type display end to end).
  2. File upload + retry-on-failure (file sources only).
  3. Website ingestion + recrawl (website sources only).
  4. Manual FAQ: create a FAQ source, then add/edit/delete its entries.
- **Status and type badges are small local components** (`KnowledgeSourceStatusBadge`,
  a type-label helper), following this same page's own existing precedent of a page-local badge
  component rather than stretching the shared `StatusBadge`'s narrow pending/accepted tone map.
- **Viewing chunk contents is out of scope.** The build-plan line asks for "processing status
  management," not a chunk inspector; `GET .../chunks` stays unused by this feature. A future
  feature can add it against the call-detail-style "why did it say that" need (CLAUDE.md section
  25) if and when that's actually built.

## In scope

- **`apps/web/lib/api.ts`** - `buildHeaders` skips the default `Content-Type` when the request
  body is a `FormData` instance.
- **`apps/web/lib/knowledge-sources.ts`** (new) - locked types (`KnowledgeSource`,
  `KnowledgeSourceDocument`, `KnowledgeSourceCrawledPage`, `FaqEntry`) mirroring the backend
  response shapes exactly; functions: `listKnowledgeSources`, `uploadKnowledgeSourceFile`,
  `createWebsiteKnowledgeSource`, `createManualFaqKnowledgeSource`, `processKnowledgeSource`
  (retry), `recrawlKnowledgeSource`, `listFaqEntries`, `createFaqEntry`, `updateFaqEntry`,
  `deleteFaqEntry` - every call scoped by `organizationId`/`workspaceId` the same way every other
  function in `lib/assistants.ts` already is.
- **`apps/web/app/(app)/assistants/[assistantId]/page.tsx`** - Knowledge tab:
  - Lazy-loads the source list on first view (matching Custom Prompt's lazy-fetch pattern);
    `LoadingState`/`ErrorText`/`EmptyState` for the three non-happy list states.
  - Each row shows type label, name/filename/URL, `KnowledgeSourceStatusBadge`
    (pending/processing/completed/failed), `error_message` when `status === "failed"`, and
    `created_at`.
  - File sources: "Retry" button when `status === "failed"` → `processKnowledgeSource` → refresh.
  - Website sources: "Recrawl" button (any status) → `recrawlKnowledgeSource` → refresh; shows
    crawled-page count from `crawled_pages`.
  - Manual-FAQ sources: expandable inline section (lazy-fetched on first expand) listing its
    `FaqEntry` rows with add/edit/delete forms.
  - Three "add" affordances at the top of the tab: file picker + upload button; URL input + "Add
    website" button; name input + "Add FAQ source" button. Every create call passes the current
    `assistantId` from the page's own route params.
- **New unit tests** for any pure helper extracted out of the page (e.g. "can this source be
  retried/recrawled given its type and status" - real branching logic worth a colocated
  `*.test.ts`, matching `lib/business-hours.test.ts`'s precedent for this class of function).

## Out of scope

- **Deleting a knowledge source.** No backend route exists; not this feature's job to add one.
- **Viewing chunk contents.** `GET .../chunks` is unused by this feature.
- **Drag-and-drop upload, multi-file upload, or upload progress percentage.** A plain `<input
  type="file">` plus a single POST matches this codebase's "boring, reliable engineering"
  preference; nothing here asked for more.
- **Any backend change beyond the one-line `buildHeaders` fix.** The 23d backend is otherwise
  final; this feature only consumes it.

## Build steps

- [x] **Step 1 - API client, `buildHeaders` fix, read-only list view**
  - `apps/web/lib/api.ts`: `buildHeaders` skips default `Content-Type` for a `FormData` body.
  - `apps/web/lib/knowledge-sources.ts` (new): types + all list/create/action functions.
  - Knowledge tab: replaces the placeholder with a real lazy-loaded list (status/type badges,
    error message, created_at) and its own loading/error/empty states. No create/action buttons
    yet - this step only proves the read path end to end.
  *Done when:* `npm run build` passes; a temporary Playwright check confirms the tab loads real
  knowledge sources for a test assistant (seed one via the API directly) and shows the correct
  status badge; deleted after use.

- [x] **Step 2 - file upload and retry**
  - File picker + upload button, calling `uploadKnowledgeSourceFile` with the fixed `FormData`
    path; list refreshes on success; upload errors (422 unsupported type / too large) surface via
    `ErrorText`.
  - "Retry" button on a failed file source → `processKnowledgeSource` → list refreshes.
  *Done when:* `npm run build` passes; a temporary Playwright check uploads a real `.txt` file
  through the UI and confirms it appears with `status: "completed"` - the actual proof the
  `buildHeaders` fix works, not just that the button exists.

- [x] **Step 3 - website ingestion and recrawl**
  - URL input + "Add website" button, calling `createWebsiteKnowledgeSource`; list refreshes.
  - "Recrawl" button on any website source → `recrawlKnowledgeSource` → list refreshes.
  *Done when:* `npm run build` passes; a temporary Playwright check adds a website source through
  the UI and confirms it appears in the list with its crawled-page count.

- [x] **Step 4 - manual FAQ source and entries**
  - Name input + "Add FAQ source" button, calling `createManualFaqKnowledgeSource`.
  - Expandable inline FAQ-entry management per manual-FAQ row: list (lazy-fetched on expand),
    add form, inline edit, delete with confirmation.
  *Done when:* `npm run build` passes; a temporary Playwright check creates a manual-FAQ source,
  adds an entry, edits it, and deletes it, confirming each step through the UI.

## Files / areas

**New**
- `apps/web/lib/knowledge-sources.ts`
- `apps/web/lib/knowledge-sources.test.ts`

**Modified**
- `apps/web/lib/api.ts`
- `apps/web/lib/auth.ts` (`authorizedFetch` threads the request body into `buildHeaders` too)
- `apps/web/app/(app)/assistants/[assistantId]/page.tsx`

**Unchanged**
- `apps/api` - no backend change; 23d already shipped everything the UI needs.
- `apps/voice` - unaffected; retrieval already reads through 23d's backend independently of this
  UI.

## Data / contracts

```ts
type KnowledgeSourceType = "file" | "website" | "manual_faq";
type KnowledgeSourceStatus = "pending" | "processing" | "completed" | "failed";

interface KnowledgeSourceDocument {
  id: string;
  filename: string;
  content_type: string;
  processing_status: string;
  processing_error: string | null;
  created_at: string;
}

interface KnowledgeSourceCrawledPage {
  id: string;
  url: string;
  fetched_at: string;
  content_hash: string;
}

interface KnowledgeSource {
  id: string;
  organization_id: string;
  workspace_id: string;
  assistant_id: string | null;
  type: KnowledgeSourceType;
  status: KnowledgeSourceStatus;
  error_message: string | null;
  owner_user_id: string | null;
  source_url: string | null;
  name: string | null;
  created_at: string;
  document: KnowledgeSourceDocument | null;
  crawled_pages: KnowledgeSourceCrawledPage[] | null;
}

interface FaqEntry {
  id: string;
  knowledge_source_id: string;
  question: string;
  answer: string;
  created_at: string;
}
```

These mirror `KnowledgeSourceResponse`/`DocumentResponse`/`CrawledPageResponse`/`FaqEntryResponse`
in `apps/api` exactly - locked here so the frontend types can't silently drift from the backend.

## Testing

`apps/web` has no server-side logic in this feature to unit-test beyond the pure eligibility-check
helpers extracted into `lib/knowledge-sources.ts` (`canRetryKnowledgeSource`,
`canRecrawlKnowledgeSource`, `knowledgeSourceDisplayName`, `knowledgeSourceTypeLabel`) - all
covered by `lib/knowledge-sources.test.ts`, matching `lib/business-hours.test.ts`'s precedent. The
rest is UI/integration work, verified per step via `npm run build` plus a temporary Playwright
check against the real running stack (written to `apps/web/e2e/`, deleted after use, never
committed).

Final gates: `npm run build` (pass), `npm run lint` (clean), `npm run test` (6 files, 47 tests,
all pass). Each of the four steps was additionally proven against the real running stack with a
temporary Playwright check: Step 1 loaded a seeded knowledge source with the correct status badge;
Step 2 uploaded a real `.txt` file through the UI and confirmed `status: "completed"` (the actual
proof the `buildHeaders`/FormData fix works); Step 3 crawled a real website
(`http://example.com/`) and confirmed the page count and a working recrawl; Step 4 created a
manual-FAQ source and added, edited, and deleted an entry through the UI.

## Notes for the AI

- **The `buildHeaders` fix in Step 1 is the one real landmine in this feature.** Verified with an
  actual file upload in Step 2's manual check, not just by reading the code - confirmed working
  end to end (server logs showed the multipart body parsed correctly, filename and content-type
  intact).
- **`assistant_id` on every create call is the current assistant editor's own `assistantId` route
  param** - already validated by the page's existing access checks; no separate picker needed.
- **Knowledge-source deletion's absence is a real gap, not an oversight quietly fixed here** - left
  exactly as documented in Out of scope; a future build-plan item can add the backend route and
  the UI action together if it's ever actually needed.
- **A recurring environment flake this session, not a code defect**: the first Playwright
  interaction against a route immediately after `docker compose up -d --force-recreate --no-deps
  web` sometimes stalls indefinitely (Turbopack's first lazy-compile of that route/code-path under
  load), even though the backend request completes successfully in under a second. Resolved each
  time by re-running the same check a second time after the route has warmed up - never a real
  bug. Confirmed twice in this feature (once on the initial page load, once on the file-upload
  interaction) by checking API container logs, which showed the request completing fast while the
  browser-side Promise appeared stuck.
- **The dev-only register rate limit (5/hour, IP-keyed) was hit again during manual verification**
  and cleared via `docker compose exec -T redis redis-cli -n 0 DEL
  "ratelimit:register:172.20.0.1"` - dev-only, session-local, never to be done against a real
  Redis instance.
- Build-plan item 23 itself is now fully complete (all of 23a-23e checked off) - this was the
  standing override's final sub-feature from the original `/feature 23` invocation. The next
  `/feature` run should proceed to whatever the first unchecked build-plan item is with no further
  reference to this override.
