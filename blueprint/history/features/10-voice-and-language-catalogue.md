# Feature: Voice and language catalogue

**From build-plan:** feature 10
**Status:** not started

## Goal

Let a signed-in user browse the configured TTS provider's voice catalogue - name, language,
gender - and hear each voice before item 11 (Assistant foundation) exists to attach a voice to.
This is the first real consumer of 9a/9b's speech providers outside their own test suite, so it
also locks how a FastAPI route obtains a provider via dependency injection - a pattern items 11d,
20, and beyond will reuse.

## Design reference

None. No mockups or reference images exist for this project; the page follows the established
`PageShell`/`Card`/`LoadingState`/`EmptyState`/`ErrorText` primitives and list layout already used
by `/organizations` and `/settings`.

## In scope

- `Voice.preview_url: str | None` added to 9a's locked `Voice` contract in `speech.py`, mapped from
  ElevenLabs' real `preview_url` field (verified against ElevenLabs' own `GET /v2/voices`
  documentation while writing this spec - it is a direct, publicly playable audio file URL, the
  same mechanism ElevenLabs' own public voice library preview button uses).
- `MockTTS.list_voices()` extended to respect the already-existing injected `failure`, matching
  `synthesize()`'s existing behavior - needed to test this feature's error-mapping without a real
  provider.
- A FastAPI dependency-injection entry point for the TTS provider (`TtsProvider`, alongside the
  existing `CurrentUser`/`DbSession` aliases in `app/api/deps.py`), the pattern every future route
  needing a speech provider will reuse.
- `GET /api/v1/voices` - authenticated (any signed-in user; no organization/workspace scoping,
  since a provider's voice catalogue is not tenant-owned data), returns the configured provider's
  voices, HTTP error mapping for a provider timeout or outage.
- A `/voices` page inside the authenticated `(app)` shell: list of voices showing name, language,
  and gender, with a per-voice "Play preview" control; only one preview plays at a time.
- Loading, empty (no voices - the real state for the default mock provider), and error states,
  matching the primitives every other page already uses.

## Out of scope

- **A left-nav entry for `/voices`.** The plan's own Assistant Editor description lists "voice"
  as a *section within* the future assistant editor (item 11d), not a top-level nav destination -
  the same way prompt templates (12) and glossary (13) aren't top-level pages either. This page
  exists now, reachable directly at `/voices`, so the capability is real and testable before item
  11 exists; item 11d will link to it or absorb its logic into the editor later. Flagging this
  choice for review - say so if a nav entry is wanted now instead.
- **Search or filtering the catalogue.** "Browsable ... with language and gender metadata" means
  the metadata is visibly displayed, not that a filter UI is required. A real catalogue is
  typically dozens of voices; add filtering later if it proves necessary.
- **Assigning a voice to anything.** There is no `Assistant` yet (item 11). This page is read-only
  browsing; no `onSelect`/picker behavior. Don't build a reusable "voice picker" component ahead
  of item 11d actually needing one - every other page in this codebase inlines its content
  directly rather than extracting a speculative shared component, and this follows that precedent.
- **Caching the catalogue.** Every request calls the provider directly. This isn't the audio hot
  path (CLAUDE.md's latency budget doesn't apply to a control-plane browsing page), and a
  catalogue this size costs one HTTP round trip. Add caching later if real usage shows it's
  needed - not speculatively now.
- **Voice design / cloning** (ElevenLabs' `/v1/text-to-voice/design` and similar). Out of MVP
  scope entirely per `project-overview.md`.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `Voice.preview_url` contract + mock/adapter updates** - add
  `preview_url: str | None = None` to `Voice` in `app/providers/speech.py`; map it in
  `elevenlabs_speech.py`'s `_map_voice` from ElevenLabs' `preview_url` field; extend
  `MockTTS.list_voices()` to raise `self._failure` when set (mirroring `synthesize()`), so tests
  can exercise a failing catalogue without a real provider.
  *Done when:* `pytest apps/api/tests/test_speech_providers.py apps/api/tests/test_elevenlabs_speech.py`
  passes, including new tests proving: `_map_voice` reads `preview_url` when present and is `None`
  when absent; `MockTTS.list_voices()` raises the injected failure when one is set, and still
  returns the configured voices when none is set. `ruff check apps/api` clean.

- [x] **Step 2 - `GET /api/v1/voices`** - `app/schemas/voice.py` (`VoiceResponse`); a
  `get_tts_provider_dependency()` zero-argument wrapper in `app/providers/factory.py` around the
  existing `get_tts_provider()` (a raw `Depends(get_tts_provider)` would let FastAPI treat its
  optional `name` parameter as an undocumented, unauthenticated `?name=` query override - the
  wrapper closes that off); `TtsProvider = Annotated[TextToSpeechProvider, Depends(...)]` in
  `app/api/deps.py`; the route itself in `app/api/v1/voices.py`, requiring `CurrentUser`, mapping
  `SpeechProviderTimeout` to 504 and any other `SpeechProviderError` to 503 with an
  operator-facing message (never the raw exception); registered in `main.py`.
  *Done when:* `pytest apps/api/tests/test_voices.py` (new) passes, proving: 401 with no token;
  200 with the mapped voice list when the provider has voices (via dependency override); 200 with
  an empty list when it has none; a `SpeechProviderTimeout` maps to 504; a `SpeechProviderError`
  maps to 503, with the response body containing no raw exception text. `ruff check apps/api`
  clean.

- [x] **Step 3 - `lib/voices.ts`** - `Voice` type (snake_case fields, matching every other
  frontend type in this codebase: `id`, `name`, `language`, `gender`, `preview_url`) and
  `listVoices(): Promise<Voice[]>` via `authorizedJson`, matching `listOrganizations`'s exact
  shape.
  *Done when:* `npx tsc --noEmit` passes; `npm run lint` clean.

- [x] **Step 4 - `/voices` page** - `app/(app)/voices/page.tsx`: fetch-on-mount using the
  fetch-then-`.then()`-apply-with-`cancelled`-guard shape this session's own lint fix just
  established as this codebase's pattern for exactly this case (see
  `blueprint/history/fixes/set-state-in-effect-lint.md`) - do not reintroduce the violation that
  fix just repaired. Render `LoadingState`/`ErrorText`/`EmptyState` per the existing convention;
  once loaded, list each voice's name, language, and gender, with a "Play preview" button that is
  disabled when `preview_url` is null. Clicking a preview stops whichever preview is currently
  playing (if any) before starting the new one - only one plays at a time.
  *Done when:* `npm run build` succeeds; a manual/Playwright browser check confirms the list
  renders with real (mock-provider) data, the empty state shows when the catalogue is empty
  (today's default), and starting a second preview stops the first (verifiable by checking only
  one `<audio>` element is playing/unpaused at a time). No unit test - this is UI/integration
  surface per `coding-standards.md`'s testing scope rule, not pure logic.

- [x] **Step 5 - full verification** - confirm nothing regressed.
  *Done when:* full backend `pytest` passes; `npm run lint`, `npm run test`, `npm run build`, and
  `npx playwright test` all pass; a manual browser check of `/voices` (reached by direct URL, per
  the nav decision above) confirms the end-to-end flow.

## Files / areas

**New**
- `apps/api/app/schemas/voice.py`
- `apps/api/app/api/v1/voices.py`
- `apps/api/tests/test_voices.py`
- `apps/web/lib/voices.ts`
- `apps/web/app/(app)/voices/page.tsx`

**Modified**
- `apps/api/app/providers/speech.py` - adds `Voice.preview_url`.
- `apps/api/app/providers/elevenlabs_speech.py` - `_map_voice` maps `preview_url`.
- `apps/api/app/providers/mock_speech.py` - `MockTTS.list_voices()` respects injected `failure`.
- `apps/api/app/providers/factory.py` - adds `get_tts_provider_dependency()`.
- `apps/api/app/api/deps.py` - adds the `TtsProvider` alias.
- `apps/api/app/main.py` - registers the voices router.
- `apps/api/tests/test_speech_providers.py`, `apps/api/tests/test_elevenlabs_speech.py` - new
  assertions for Step 1.

**Unchanged**
- No database model, migration, or tenant-scoping change - this endpoint touches no organization
  or workspace data. `app/api/org_deps.py` / `workspace_deps.py` are not involved.
- `Assistant`/`AssistantVersion` - still item 11's; nothing here references them.

## Data / contracts

**`Voice.preview_url: str | None = None`** - additive to 9a's locked `Voice` dataclass; every
existing construction site (`Voice(id=..., name=..., language=..., gender=...)`) keeps working
unchanged since the field defaults to `None`.

**`GET /api/v1/voices` -> `200 list[VoiceResponse]`**, where `VoiceResponse` is
`{id: str, name: str, language: str, gender: str | None, preview_url: str | None}`. No pagination,
no query parameters - the real ElevenLabs list is already paginated internally by 9b's
`list_voices()` before this route ever sees it.

**Error responses** - `504` (`SpeechProviderTimeout`) and `503` (any other `SpeechProviderError`),
both with a plain operator-facing `detail` string, never the raw exception. No new error type is
introduced; this only adds HTTP mapping on top of 9a/9b's existing hierarchy.

**`TtsProvider` DI alias** - load-bearing for later features. Every future route needing a speech
provider (11d's preview inside the editor, anything under item 20 that isn't itself the media
plane) should use this same `Annotated[..., Depends(get_tts_provider_dependency)]` pattern, not a
bare call to `get_tts_provider()` inside route logic.

## Testing

The backend gate is live - every step ships its tests in the same diff.

**In-scope logic needing tests:** `_map_voice`'s `preview_url` mapping (pure, Step 1),
`MockTTS.list_voices()`'s failure injection (Step 1), the route's success/empty/error-mapping
behavior via dependency override (Step 2, API-level, not unit tests - there's no pure logic here
beyond what Step 1 already covers).

**No cross-tenant test needed.** This endpoint returns identical data to every authenticated
user regardless of organization or workspace - there is no tenant boundary to test, unlike every
other resource this codebase has added so far. State this explicitly rather than silently
skipping the pattern.

**Frontend:** Step 3's `lib/voices.ts` is a thin fetch wrapper with no branching logic - matches
`listOrganizations`'s precedent, which also has no dedicated unit test. Step 4's page is
integration/UI surface, verified by build + manual/Playwright check per
`coding-standards.md`, not a unit test.

## Notes for the AI

- **Reuse this session's own just-established pattern for the mount-fetch effect** (Step 4) -
  pure fetch, apply via an inline `.then()` callback, `cancelled` flag set in cleanup. Do not
  write `useEffect(() => { load(); }, [load])` where `load` itself sets state; that is exactly
  the lint violation fixed earlier this session (`blueprint/history/fixes/set-state-in-effect-lint.md`).
- **Do not add a nav entry** for `/voices` without flagging it - see Out of scope. If review
  decides a nav entry is wanted now, that's a one-line addition to the shell's nav list, not a
  reason to restructure this spec.
- **`preview_url` is untrusted, provider-supplied data landing in an `<audio src>`.** It is not
  user input and carries no injection risk in an `src` attribute, but never render it as HTML or
  interpolate it into anything executed - treat it like any other external URL.
- **Don't build a reusable voice-picker component now.** See Out of scope; item 11d is the real
  second consumer, and it doesn't exist yet.
- **`get_tts_provider_dependency()` must take zero arguments.** Passing `get_tts_provider` (which
  has an optional `name: str | None = None` parameter) directly to `Depends()` would let FastAPI
  resolve `name` as a request query parameter - an unintended, unauthenticated way to switch
  providers per-request. The wrapper is not boilerplate; it closes a real hole.
- **Except-clause order matters in the route.** `except SpeechProviderTimeout` must come before
  `except SpeechProviderError` (`SpeechProviderTimeout` is a subclass) - reversing them silently
  swallows the timeout-specific 504 into the generic 503 branch. This is the same class of mistake
  this session's F-39 repair (`elevenlabs_speech.py`) was about; don't repeat it here.
- No database model, migration, or tenant-scoping dependency is touched. If a step seems to need
  one, it has drifted from this spec.
