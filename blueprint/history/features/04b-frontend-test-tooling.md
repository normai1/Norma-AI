# Feature: Frontend test tooling

**From build-plan:** feature 4b (under 4. Automated testing foundation)
**Status:** not started

## Goal

Install and wire the frontend test stack the plans already decided on - Vitest, React Testing
Library, and Playwright - and turn on the frontend test gate in `AGENTS.md`, so future frontend
build steps get the same automated gate backend steps have had since feature 1. Today there is no
frontend test command at all, so every UI-behavioral check has relied on manual curl calls,
screenshots, or eyeballing the dev server.

## In scope

- Install and configure Vitest + React Testing Library, with one example pure-logic test and one
  example component-render smoke test proving the pipeline is wired correctly.
- Install and configure Playwright, with one example E2E smoke test.
- Turn on the frontend test gate: add the real test commands to `AGENTS.md`'s Commands section and
  update its Verify paragraph, which currently says the frontend gate is `npm run build` alone.

## Out of scope

- **A CI pipeline that runs these automatically.** That's `/ci` (item 59) - this feature only makes
  the commands exist and work locally.
- **Retrofitting existing components or pages with new test coverage.** Only the two setup examples
  ship now. Every feature from here on adds its own tests for its own new logic, per the now-active
  gate - going back and testing everything already built is not this feature's job.
- **A full multi-browser or mobile-viewport Playwright matrix.** One `chromium` project is enough to
  prove the tool works. Expand it when a real cross-browser bug or the release-readiness item (61)
  calls for it.
- **Redefining "what to test."** `coding-standards.md` says UI components ride on screenshot plus
  build evidence, not unit tests - the React Testing Library example is a smoke test proving the
  render pipeline (jsdom, matchers, path aliases) works, not a new blanket policy to unit-test every
  component. That rule is untouched.
- **A full login-flow E2E regression test against the real backend.** The example Playwright test is
  a rendering-only smoke check (the login page loads with its expected fields) with no backend
  dependency, so it stays fast and deterministic. A real authenticated-flow E2E test is scope for
  whichever future feature specifically needs that regression coverage.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Vitest + React Testing Library, unit-test gate on** - add `vitest`,
  `@vitejs/plugin-react`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, and
  `@testing-library/user-event` as devDependencies. Add `vitest.config.ts` (React plugin, `jsdom`
  environment, the `@/*` path alias matching `tsconfig.json`, `setupFiles` pointing at
  `vitest.setup.ts`, which imports `@testing-library/jest-dom`, and `test.exclude` covering the
  default excludes plus `e2e/**` - Step 2 adds Playwright spec files there, and without this
  exclusion Vitest would try to collect them too and fail on the unrelated `@playwright/test` API). Add `test`/`test:watch` scripts to
  `package.json`. Ship `lib/organizations.test.ts` (pure-logic example: `canManage` for every role)
  and `components/organizations/ui.test.tsx` (render-pipeline smoke test: `RoleBadge` renders its
  role text). Add `Test: npm run test` to `AGENTS.md`'s Frontend Commands, and update the Verify
  paragraph, which currently claims the frontend gate is `npm run build` alone. *Done when:*
  `npm run test` passes both example tests, and `npm run lint` and `npm run build` stay clean.

- [x] **Step 2 - Playwright, one example E2E test** - add `@playwright/test` as a devDependency and
  run the one-time browser install. Add `playwright.config.ts` (`testDir: "e2e"`,
  `baseURL: "http://localhost:3000"`, a single `chromium` project, `webServer` configured to reuse
  the already-running dev server locally via `reuseExistingServer: !process.env.CI`). Add a
  `test:e2e` script. Ship `e2e/login.spec.ts`: navigate to `/login`, assert the page renders its
  heading and both the email and password fields. Add `Test (E2E): npx playwright test` to
  `AGENTS.md`'s Frontend Commands, noting it needs the dev server running at `localhost:3000`.
  *Done when:* `npx playwright test` passes against the running dev server.

## Files / areas

| Path | Change |
| --- | --- |
| `apps/web/package.json` | edit - devDependencies, `test`/`test:watch`/`test:e2e` scripts |
| `apps/web/vitest.config.ts` | new |
| `apps/web/vitest.setup.ts` | new |
| `apps/web/lib/organizations.test.ts` | new |
| `apps/web/components/organizations/ui.test.tsx` | new |
| `apps/web/playwright.config.ts` | new |
| `apps/web/e2e/login.spec.ts` | new |
| `AGENTS.md` | edit - Frontend Commands, Verify paragraph |

## Data / contracts

None. This feature touches no production schema, API, or stored shape - build tooling only.

## Testing

This feature *is* test tooling, so each step ships and proves its own example instead of a separate
section restating it:

| Step | Coverage |
| --- | --- |
| 1 | `lib/organizations.test.ts` (pure logic), `components/organizations/ui.test.tsx` (RTL smoke) |
| 2 | `e2e/login.spec.ts` (Playwright smoke, requires the dev server running) |

## Notes for the AI

- **This changes future Browser Verification.** `coding-standards.md` already says "If Playwright is
  already installed, or the Commands section of `AGENTS.md` declares a Playwright script, use
  Playwright for browser checks." Once Step 2 lands, future UI-behavioral done-whens should prefer
  it over manual curl calls or eyeballing the dev server - the kind of verification gap noted during
  the discarded profile-settings work earlier this session.
- **Don't silently expand scope.** `npx playwright install` downloads real browser binaries; if
  network access is restricted in this environment, say so explicitly in the step's evidence rather
  than skipping the verification quietly.
- **Match the existing layering and style**: two blank lines between top-level definitions in
  Python; existing frontend conventions (function components, `@/*` imports, Tailwind utility
  classes) for anything new in TypeScript. No em dashes.
- **`coding-standards.md`'s Testing section was just tuned in feature 4a** - read it before writing
  the Vitest config; it already names this feature by number as the thing that fills in the frontend
  half.
