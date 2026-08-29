# Feature: Website ingestion

**From build-plan:** feature 15

**Status:** not started

## Goal

`type='website'` knowledge sources: crawl a supplied domain (bounded, same-hostname, small),
extract page text, store one `CrawledPage` row per URL, and support recrawling with
content-hash deduplication - only rewrite a page's stored text when it actually changed.
Builds directly on item 14's `KnowledgeSource`/permission/route foundation; no new UI (still
backend-only, matching 14's precedent).

## Design reference

None. Backend-only.

## In scope

- **Schema gap, reconciled explicitly.** `project-overview.md`'s locked `KnowledgeSource`
  model has no field for "which domain/URL this source crawls" - it predates this feature's
  detailed design. Recrawling needs to remember the starting URL without the caller re-supplying
  it every time, so `KnowledgeSource` gets one additive column: `source_url` (nullable text,
  only ever set for `type='website'` rows). `CrawledPage` is added exactly as locked:
  `knowledge_source_id`, `url`, `fetched_at`, `extracted_text`, `content_hash` - plus a
  `UniqueConstraint(knowledge_source_id, url)`, since a page is one row updated in place across
  recrawls, not an immutable version history.
- **`PageFetcher`** - a small provider-style abstraction (`app/providers/web_crawler.py`):
  `fetch(url) -> str` (raw HTML). `HttpxPageFetcher` is the one real implementation; there is no
  production "choice" the way STT/TTS/storage have real alternatives, so there is no
  `WEB_CRAWLER_PROVIDER` setting or factory - the FastAPI dependency always constructs
  `HttpxPageFetcher`, and tests override it with `MockPageFetcher` (scripted per-URL HTML)
  through `app.dependency_overrides`, exactly like `StorageProvider`'s test injection.
- **SSRF guard, built in from the start, not deferred.** The caller supplies a URL the server
  then fetches - a classic SSRF vector if unguarded. `HttpxPageFetcher` rejects any URL whose
  hostname resolves to a private, loopback, or link-local address (via the stdlib `ipaddress`
  module) before fetching, for the start URL and for every discovered link, and does not follow
  redirects automatically (a redirect response is treated as that page failing to fetch, not
  silently followed into an unvalidated target).
- **The crawl itself** - breadth-first from the start URL, same hostname only (no subdomains,
  no external links), capped at `MAX_PAGES_PER_CRAWL = 20` and `MAX_CRAWL_DEPTH = 2`, each fetch
  timeout-bounded at 10 seconds. HTML → text via `BeautifulSoup` (`html.parser`, stdlib-only
  parser backend - no `lxml` C-dependency needed for this), stripping `<script>`/`<style>`.
  `content_hash` is the SHA-256 hex digest of the extracted text (stdlib `hashlib`).
- **`POST .../knowledge-sources/website`** - body `{url: HttpUrl}` (Pydantic's own scheme/format
  validation). Runs the crawl synchronously within the request - there is no background job
  queue yet (`apps/worker` is still the item-17 heartbeat skeleton, no library wired up), and a
  bounded 20-page/depth-2 crawl keeps the request latency reasonable for a typical small
  business site. Creates `type='website'`, `source_url` set, one `CrawledPage` per fetched page.
  If the *root* URL itself fails to fetch, the source is created with `status='failed'` and
  `error_message` set (still `201` - the operation completed and recorded its own outcome,
  matching how a call's outcome is recorded rather than thrown); if the root succeeds, status is
  `'completed'` even if some discovered sub-pages individually failed to fetch (those are simply
  not recorded as `CrawledPage` rows - a pragmatic MVP partial-success rule, not a fatal error).
  Reuses `CanManageKnowledge` (owner/admin), the same permission item 14 already added - this is
  the same resource type, not a new one.
- **`POST .../knowledge-sources/{id}/recrawl`** - re-runs the crawl using the source's stored
  `source_url`, `404` if the source doesn't exist or isn't `type='website'`. For each URL
  encountered: new URL → insert a `CrawledPage`; known URL with an unchanged `content_hash` →
  leave `extracted_text`/`content_hash` untouched, only refresh `fetched_at` (the actual
  dedup - skip rewriting content that didn't change); known URL with a changed hash → update
  `extracted_text`/`content_hash`/`fetched_at`. `status`/`error_message` follow the same
  root-failure rule as the initial crawl.
- **`KnowledgeSourceResponse` gains `source_url` and `crawled_pages`** (a bounded list - at most
  20 - of `{id, url, fetched_at, content_hash}`; `extracted_text` is not exposed, matching 14's
  `storage_key`-stays-internal precedent - nothing consumes the full text yet).

## Out of scope

- **Any real background job execution.** The synchronous, bounded crawl is a deliberate,
  temporary MVP shape. Moving crawling to a real job queue is a decision for whichever feature
  first builds one (item 17 at the earliest) - not invented here ahead of that infrastructure.
- **Pruning `CrawledPage` rows for pages removed from the site.** A recrawl that no longer finds
  a previously-crawled URL leaves that row alone. Deciding when stale content should stop being
  served is a retrieval-time concern for a later item.
- **Sitemap.xml parsing, JavaScript-rendered pages, or robots.txt handling.** Plain anchor-tag
  link discovery over server-rendered HTML only. None of these are named in the build-plan line;
  each is a real, separate scope decision if a later feature needs it.
- **Exposing `extracted_text` through the API, or any knowledge-review UI.** Same precedent as
  item 14 - backend-only, no UI in this feature.
- **A `WEB_CRAWLER_PROVIDER` setting or multiple real fetcher implementations.** There is exactly
  one real way to fetch a web page; the STT/TTS/storage-style provider-choice pattern doesn't
  apply here, only the test-double-injection half of it.
- **Chunking, embedding, or retrieval over crawled text.** Items 17-19.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `PageFetcher` abstraction** - `app/providers/web_crawler.py` (`PageFetcher`
  protocol, `PageFetchError`, `PageFetchTimeout`, `UnsafeUrlRejected`);
  `app/providers/mock_web_crawler.py` (`MockPageFetcher`, scripted per-URL HTML, matching
  `MockSTT`'s scripted-event style); `app/providers/httpx_web_crawler.py`
  (`HttpxPageFetcher` - hostname-resolves-to-a-public-IP guard, no redirect following, 10s
  timeout, `get_page_fetcher_dependency()`); `app/api/deps.py` gets `PageFetcherDep`;
  `requirements.txt` gets `beautifulsoup4`; `tests/conftest.py`'s `client` fixture overrides
  `get_page_fetcher_dependency` the same way it already overrides storage.
  *Done when:* the API container is rebuilt; a new `tests/test_web_crawler.py` passes -
  `MockPageFetcher` returns scripted HTML per URL and raises for an unregistered one;
  `HttpxPageFetcher` rejects a URL resolving to a loopback/private/link-local address with
  `UnsafeUrlRejected` (stub DNS resolution in the test - no real network call); a redirect
  response is treated as a fetch failure, not followed. Full backend suite still green.
  `ruff check apps/api` clean.

- [x] **Step 2 - `CrawledPage` model and `KnowledgeSource.source_url`** - `app/models/
  crawled_page.py`; `app/models/knowledge_source.py` gets `source_url`; one migration (new
  table + additive column together); registered in `app/db/base.py`.
  *Done when:* `alembic upgrade head` / `downgrade -1` / `upgrade head` all succeed. `ruff check
  apps/api` clean (no route/test yet - Step 3 gives these models their first behavior).

- [x] **Step 3 - Crawl service, recrawl, and endpoints** - `app/repositories/crawled_page.py`
  (`get_by_url`, `list_for_source`, `upsert` - insert if new, update only when the hash
  differs); `app/services/web_crawler.py` (`crawl_website(fetcher, start_url) -> list[
  CrawlResult]`, pure BFS over the fetcher with the same-hostname/page-count/depth bounds - no
  DB access, independently testable against `MockPageFetcher`); `app/services/
  knowledge_source.py` gets `create_website_knowledge_source` (creates the row, runs the crawl,
  reconciles `CrawledPage` rows, sets `status`/`error_message`) and `recrawl_knowledge_source`
  (same reconciliation against the existing source); `app/schemas/knowledge_source.py` gets
  `CrawledPageResponse`, `KnowledgeSourceResponse.source_url`/`crawled_pages`,
  `WebsiteKnowledgeSourceCreate{url: HttpUrl}`; `app/api/v1/knowledge_sources.py` gets the two
  routes.
  *Done when:* a new `tests/test_website_knowledge_sources.py` (plus `test_web_crawler.py`
  additions for the pure `crawl_website` function) passes - crawling a small mocked same-
  hostname link graph discovers and stores every reachable page up to the bounds; an
  unreachable start URL yields `status='failed'` with `error_message` set; recrawling with one
  page's content changed updates only that page's `extracted_text`/`content_hash` and leaves
  unchanged pages' `content_hash` identical (proving dedup, not just re-fetching); recrawling a
  nonexistent or non-website source 404s; owner/admin can crawl/recrawl, member/viewer get 403,
  unauthenticated gets 401; cross-tenant 404s hold; the page-count cap is actually enforced
  against a mocked graph larger than 20 pages. Full backend suite green. `ruff check apps/api`
  clean.

## Files / areas

**New**
- `apps/api/app/providers/web_crawler.py`, `mock_web_crawler.py`, `httpx_web_crawler.py`
- `apps/api/app/models/crawled_page.py`
- `apps/api/app/repositories/crawled_page.py`
- `apps/api/app/services/web_crawler.py` (pure crawl logic)
- `apps/api/tests/test_web_crawler.py`, `apps/api/tests/test_website_knowledge_sources.py`
- One Alembic migration.

**Modified**
- `apps/api/app/models/knowledge_source.py` (`source_url`), `apps/api/app/schemas/
  knowledge_source.py`, `apps/api/app/services/knowledge_source.py`, `apps/api/app/api/v1/
  knowledge_sources.py`, `apps/api/app/api/deps.py`, `apps/api/app/db/base.py`,
  `apps/api/requirements.txt`, `apps/api/tests/conftest.py`.

**Unchanged**
- No frontend file. `app/core/permissions.py`/`app/api/org_deps.py` - `CanManageKnowledge`
  already exists from item 14, reused as-is.

## Data / contracts

**`KnowledgeSourceResponse`** gains `source_url: str | None` and `crawled_pages:
list[CrawledPageResponse] | None`.

**`CrawledPageResponse`** - `{id, url, fetched_at, content_hash}`. No `extracted_text`.

**`crawl_website(fetcher, start_url) -> list[CrawlResult]`** - pure, synchronous-within-one-
call BFS. Locked shape since it is the one piece both the initial-crawl and recrawl code paths
call identically.

## Testing

The backend gate is live - every step ships its tests in the same diff. Step 1's fetcher tests
need no database (pure protocol/mock/guard behavior, no real network - a stubbed DNS resolution
proves the SSRF guard without depending on any specific external hostname). Step 3's
`crawl_website` tests run against `MockPageFetcher` with a small scripted link graph, no
database either; the API-level tests then prove the DB reconciliation and access-control shape
matches every other endpoint in this codebase.

## Notes for the AI

- **The crawl runs synchronously in the request. This is deliberate and temporary**, not an
  oversight - say so again if a later feature needs to change it, rather than quietly leaving
  it as if it were the intended final shape.
- **SSRF guard is not optional polish.** Apply it to the start URL and to every discovered link,
  not just the first fetch.
- **Dedup means "don't rewrite unchanged content," not "don't refetch."** There is no way to
  know a page is unchanged without fetching it and comparing hashes.
- **`crawl_website` (the pure BFS) and the DB reconciliation are separate functions.** Keep them
  that way - it is what makes the crawl algorithm testable without a database.
- **No `WEB_CRAWLER_PROVIDER` setting.** Do not invent a factory/config choice that doesn't
  correspond to a real production alternative - see Out of scope.
- A push, if any, at the end of this feature still needs your explicit go-ahead, matching the
  standing project convention.
