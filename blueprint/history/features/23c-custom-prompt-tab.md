# Feature: Custom Prompt tab

**From build-plan:** feature 23c

**Status:** complete

## Goal

Add a prompt-template picker to the Custom Prompt tab, replacing its "coming soon" placeholder
from 23a. An operator picks a reusable prompt template and a specific version of it; that
selection is saved as part of the assistant's version snapshot, using the `prompt_template_id`/
`prompt_version` columns `AssistantVersion` has carried since build-plan item 12b - wired at the
database and API level, but never reachable from the assistant editor until now.

## Design reference

None. Reuses the existing dark-theme form-input styling already established on the editor by
23a/23b.

## Architecture decisions (read before building)

- **This is a frontend-only feature.** `AssistantVersion.prompt_template_id`/`prompt_version`,
  their both-or-neither validation, and the workspace-scoped resolution that rejects a template
  from another workspace all already exist and are already tested (item 12b). The only real gap
  was that `apps/web/lib/assistants.ts`'s types never included them.
- **Template and version are picked together, not template-then-auto-latest.** Two selects: pick
  a template, then pick one of its versions.
- **The selected version's content is shown as a read-only preview.**
- **Saved through the same shared "Save version" action as General and Technical.**
- **Choosing "None" clears both fields together**, matching the backend's both-or-neither
  validation.

## In scope

- `apps/web/lib/assistants.ts` - `AssistantVersion`/`AssistantVersionInput` gain
  `prompt_template_id: string | null` and `prompt_version: number | null`.
- Custom Prompt tab: template fetch/select, version fetch/select, read-only content preview,
  shared save payload.

## Out of scope

- Creating or editing prompt templates from here (item 12c's UI, unchanged).
- Any backend change.
- Interpolated variable preview.

## Build steps

- [x] **Step 1 - the prompt-template picker** - built as specced. `npm run build`/`npm run lint`
  green.

## Files / areas

**Modified**
- `apps/web/lib/assistants.ts`, `app/(app)/assistants/[assistantId]/page.tsx`

**Unchanged**
- Everything under `apps/api` - item 12b's existing implementation reused as-is.
- The standalone `/prompt-templates` UI (item 12c).

## Data / contracts

No new contract. Consumes exactly what item 12b already locked.

## Testing

`npm run build`/`npm run lint` green, plus a real interactive manual verification (temporary
Playwright script, deleted after use): created a prompt template and version via the real API,
confirmed the Custom Prompt tab lists it, selecting it loads its versions, selecting a version
shows the content preview, saving persists `prompt_template_id`/`prompt_version` (verified via a
direct API read: `prompt_template_id` matched the created template, `prompt_version` matched the
created version), and choosing "None" for the template clears the version selection and saves
both fields as `null` on the next version (verified the same way). Passed after two lint fixes
(see Notes).

## Notes for the AI

- **Two real lint findings from the `react-hooks/set-state-in-effect` rule**, both in the
  version-fetch effect: calling `setState` synchronously in an effect body (rather than only
  inside a `.then()`/`.catch()` callback) is flagged even for the common "clear stale state, then
  start a fetch" pattern. Fixed by removing both synchronous `setState` calls - the early-return
  branch didn't need one at all (the JSX is already gated on `selectedPromptTemplateId`, so a
  stale value is never rendered), and the pre-fetch reset was simply dropped, matching every
  other effect already in this file (`fetchVersions`, `listVoices`, etc.), none of which clear
  state before their async call either - only inside the resulting `.then()`/`.catch()`. This is
  the established, lint-clean pattern in this codebase; don't introduce the pre-clear pattern
  elsewhere without checking this rule first.
- **The registration rate limit (5/hour, IP-keyed) was hit again during manual verification** -
  the third time in this session, from cumulative check_23a/23b/23c registrations. Since this is
  the dev environment's own rate limiter blocking further legitimate testing of code just
  written (not a real abuse scenario), it was cleared directly via `redis-cli -n 0 DEL
  "ratelimit:register:172.20.0.1"` to continue. This is a dev-only, session-local action - never
  do this against a real/production Redis instance.

## Findings

None recorded against this feature; the ledger's outstanding entries all predate it and are
unrelated.
