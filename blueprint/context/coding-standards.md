# Coding Standards

> Your conventions. Edit these once to match your stack. The defaults below
> assume Next.js + TypeScript + Tailwind + Prisma; change or trim anything that
> doesn't fit your project.
>
> Run `/onboard` after installing the Blueprint. It tunes this file to the real
> project stack, along with `AGENTS.md`, `CLAUDE.md` when present,
> `ai-interaction.md`, `.gitignore`, and README placement. Review the result
> before `/overview`.

## TypeScript

- Strict mode enabled
- No `any` types - use proper typing or `unknown`
- Define interfaces for all props, API responses, and data models
- Use type inference where obvious, explicit types where helpful

## React

- Functional components only (no class components)
- Use hooks for state and side effects
- Keep components focused - one job per component
- Extract reusable logic into custom hooks

## Next.js

- Server components by default
- Only use `'use client'` when needed (interactivity, hooks, browser APIs)
- Use Server Actions for form submissions and simple mutations
- Use API routes when you need:
  - Webhooks (Clerk, GitHub, etc.)
  - File uploads with progress tracking
  - Long-running operations
  - Specific HTTP status codes or headers
  - Endpoints for future mobile/CLI clients
  - Third-party integrations
- Otherwise, fetch data directly in server components
- Dynamic routes for item/collection pages

## File Organization

- Components: `src/components/[feature]/ComponentName.tsx`
- Pages: `src/app/[route]/page.tsx`
- Server Actions: `src/actions/[feature].ts`
- Types: `src/types/[feature].ts`
- Lib/Utils: `src/lib/[utility].ts`

## Naming

- Components: PascalCase (`ItemCard.tsx`)
- Files: Match component name or kebab-case
- Functions: camelCase
- Constants: SCREAMING_SNAKE_CASE
- Types/Interfaces: PascalCase (no prefix)

## Styling

- Tailwind CSS for all styling
- Tailwind v4: CSS-first config (`@theme` in `globals.css`), no `tailwind.config.js`
- Use shadcn/ui components where applicable
- No inline styles
- Dark mode first, light mode as option

## Database

- Use Prisma ORM for all database operations
- Always use `prisma migrate dev` for schema changes (not `db push`)
- Run `prisma migrate status` before committing to verify migrations are in sync
- Production deployments must run `prisma migrate deploy` before the app starts

## Data Fetching

- Server components fetch directly with Prisma
- Client components use Server Actions
- Validate all inputs with Zod
- Scope every user-owned query by the authenticated Clerk user id (`clerkUserId`); never trust a client-supplied user id

## Error Handling

- Use try/catch in Server Actions
- Return `{ success, data, error }` pattern from actions
- Display user-friendly error messages via toast

## Testing

The backend test runner is already configured and running: **pytest** is the
declared `test` command in the Commands section of `AGENTS.md`, so the gate
below is live for every backend build step, not a future setup task. Frontend
testing is not yet configured - feature 4b (Automated testing foundation) adds
Vitest, React Testing Library, and Playwright; until it lands, frontend steps
ride on screenshot plus build evidence per Browser Verification below.

When `AGENTS.md` declares a `Verify` command, treat it as the umbrella automated
gate. It combines only the checks this project actually has, in this order when
available: typecheck, tests, then build. The command does not enable an absent
test runner or replace focused evidence. It gives local work and optional CI one
exact command to run. `/ci` owns Verify and CI setup. `/tests` adds the real test
command to Verify when it already exists, but never creates CI only because
testing was configured.

**The opt-in switch is one signal: a `test` command in the Commands section of
`AGENTS.md`.** Declare one and **tests become a gate for logic-bearing steps**,
not an optional extra; leave it out and the loop verifies logic with the evidence
it already uses (run it, a screenshot, the build). Adding the runner is itself a
deliberate step, never a silent mid-step install. This is the single definition
of the switch; the skills and `ai-interaction.md` only point back here.

- **What to test (the scope rule):** pure logic where a wrong answer is possible -
  parsers, formatters, validators, id/slug builders, server actions. These have
  assertable inputs and outputs and real edge cases (empty, missing, malformed).
- **What not to test:** UI components and integration-level surfaces (render or
  export routes, anything driving a real browser or external service). Verify those
  with a screenshot and the build, not brittle unit tests.
- **The gate (when a runner is configured):** a build step that adds in-scope logic
  must ship a passing test in the same reviewable diff. The project's test command
  must be green before the step is approved, before any checkpoint commit, and
  before `/complete` merges. UI and integration-only steps are exempt and ride on
  screenshot plus build evidence.
- **When it's named:** the `/feature` spec's Testing section predicts the coverage,
  `/implement` writes the test with the step, and if a step surfaces logic the spec
  didn't foresee, add a focused test then.
- An empty suite should fail, not pass, so "no tests ran" never looks like "passed".
- Backend test files live in `apps/api/tests/`, one file per resource or concern
  (`test_organizations.py`, `test_invitations.py`, ...) - not co-located with
  the source they cover. This is the established Python/pytest convention here,
  unlike the co-located `feature.test.ts` pattern a TypeScript/Vitest setup uses.
- Run them via the project's test command (see Commands in `AGENTS.md`), not a
  hardcoded tool name.

### Backend conventions (pytest)

- Root `pytest.ini` is the single config, so `pytest` works from the repository
  root or from `apps/api` (`testpaths = apps/api/tests`, `pythonpath = apps/api`).
- `apps/api/tests/conftest.py` provides `engine` (session-scoped, creates and
  drops the test schema once), `connection` and `db` (per-test, wrapped in a
  transaction that always rolls back), `redis_client` (a throwaway Redis index,
  flushed before and after each test), and `client` (an `httpx.AsyncClient`
  bound to the app with `get_db`/`get_redis` overridden to the test fixtures).
- Tests run against a separate `TEST_DATABASE_URL`/`TEST_REDIS_URL`, never the
  dev database or Redis index - the fixtures refuse to start if either matches
  the non-test setting.
- Shared test helpers (`_signed_in`, `_org_with_owner`) live in `conftest.py`
  and are imported with `from tests.conftest import ...`. Don't redefine a
  byte-identical helper in a new test file; import the shared one, or extend it
  if the new file's need is a genuine variant.
- **Provider mocks ship with the provider, not before.** Every future provider
  abstraction (`SpeechToTextProvider`, `TextToSpeechProvider`, `LLMProvider`,
  `TelephonyProvider` - items 9, 18, 23) must add its deterministic `Mock*`
  implementation in the same diff that introduces the real one, matching the
  already-decided `MockEmbeddingProvider` precedent. Never invent a mock for a
  provider that doesn't exist yet, and never let a feature's tests depend on a
  live, paid, or rate-limited external API.

Stack binding for the future TypeScript/Vitest setup (feature 4b): `vi.mock()`
for external dependencies, `vi.useFakeTimers()` for time-dependent logic, and
co-located `feature.test.ts` files - update this note once that feature lands.

## Browser Verification

For UI and integration behavior, prefer real browser evidence over reading the
code and assuming it works.

- If Playwright is already installed, or the Commands section of `AGENTS.md`
  declares a Playwright script, use Playwright for browser checks, screenshots,
  console-error checks, and user-flow verification.
- If Playwright is not installed, do not add it silently in the middle of an
  unrelated feature. Use the available dev server, browser screenshots, build
  output, API output, or manual verification evidence instead.
- Add Playwright only when the user asks for it, or when the current spec is
  explicitly about setting up browser automation.
- Browser evidence is especially important for flows that click, type, submit,
  navigate, download files, render complex layouts, or depend on client-side
  state.

## Code Quality

- No commented-out code unless specified
- No unused imports or variables
- Keep functions under 50 lines when possible

## Comments

Write code that explains itself; comment only what the code cannot say.
Over-commenting is a common AI tell, so resist it.

- Comment the **why**, not the **what**. Delete any comment that restates the code.
- No banner/header blocks, section dividers, or step-by-step narration of obvious
  code. A file does not need a comment announcing each region.
- A comment earns its place only when it captures something the code can't: a
  non-obvious decision, a gotcha or workaround, why a value is what it is, or a
  link to a spec or issue.
- Prefer self-documenting names and small functions over explanatory comments.
- Keep doc comments minimal: a one-line purpose on an exported type or function is
  plenty; don't write JSDoc that just repeats the signature.
- When in doubt, leave the comment out.

## Writing

- No em dashes (U+2014) in generated content: docs, comments, commit messages,
  READMEs, specs. They read as AI-generated.
- Use a hyphen for `term - description` separators; rephrase prose with commas,
  parentheses, or a colon. Avoid en dashes and the ellipsis character too.
