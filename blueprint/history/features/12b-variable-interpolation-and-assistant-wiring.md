# Feature: Variable interpolation and assistant wiring

**From build-plan:** feature 12b
**Status:** not started

## Goal

Two things that make 12a's prompt templates actually usable, decoupled from each other: a
pure `{{namespace.field}}` renderer (no database, no API) that turns a `PromptVersion.content`
template plus a context dict into final text, and the additive `AssistantVersion.
prompt_template_id`/`prompt_version` columns `project-overview.md` already locks - so an
assistant's configuration snapshot can record which prompt template (and which version of it)
it was based on.

## Design reference

None. Backend-only; no UI in this sub-feature (12c's).

## In scope

- **`render_prompt(content: str, context: dict[str, dict[str, Any]]) -> str`** - a pure
  function in a new `app/services/prompt_rendering.py`, replacing every `{{namespace.field}}`
  placeholder in `content` with `context[namespace][field]`. Context is not a fixed schema -
  the renderer does not hardcode "assistant" or "workspace" field names; whatever dict a
  caller passes is what resolves. A value that is `None` renders as an empty string (a real,
  legitimately-unknown value, e.g. a caller's name before it is known). A placeholder naming a
  namespace or field the context does not provide raises a new `PromptRenderError` - a typo'd
  variable name in a template is a bug in the template, not something to silently blank out.
  Text with no placeholders returns unchanged. A lone single-brace `{like_this}` is not a
  placeholder and is left untouched.
- **`AssistantVersion.prompt_template_id`** (nullable UUID FK -> `prompt_templates.id`, no
  `ondelete` cascade - deleting a prompt template, not a capability that exists, must never
  cascade-delete an assistant version that references it) and **`AssistantVersion.
  prompt_version`** (nullable int - the specific version *number* of that template, paired
  with `prompt_template_id` the same way every other version lookup in this codebase resolves
  by `(template_id, version_number)` rather than by a raw version row id). Both land via one
  additive migration on the existing `assistant_versions` table.
- **`POST .../assistants/{id}/versions` accepts both, optional** - `AssistantVersionCreate`
  gets `prompt_template_id: uuid.UUID | None = None`, `prompt_version: int | None = None`,
  with a model validator requiring both-or-neither (a bare `prompt_version` with no template to
  pair it with is meaningless). When both are given, the service resolves the referenced
  `PromptTemplate`/`PromptVersion` through the *caller's own workspace* before saving - the
  same tenant-scoping discipline every other cross-entity reference in this codebase gets, so
  an assistant version can never silently point at another workspace's prompt template.
  `AssistantVersionResponse` exposes both fields.

## Out of scope

- **Anything that actually calls `render_prompt` against a real call.** There is no live call
  path yet (item 20). This sub-feature only builds the function and proves it with unit tests.
- **Validating `prompt_template_id` is `published`, or auto-resolving "the current published
  version" when only `prompt_template_id` is given.** The field pairing is deliberately
  explicit - both or neither - not an implicit "use whatever is live right now."
  Auto-resolution is exactly the kind of behavior that would need re-deciding once item 20
  actually reads this field; inventing it now without a real consumer would be guessing.
- **The editor UI showing a prompt-template picker on the assistant editor.** Not asked for in
  this sub-feature's build-plan line; 12c builds the prompt template's *own* editor, not a
  cross-link from the assistant editor. If a future feature wants that picker, it can consume
  the now-locked `prompt_template_id`/`prompt_version` fields without any further backend work.
- **A closed set of recognized namespaces or fields in `render_prompt`.** See In scope - the
  renderer is intentionally schema-agnostic.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `render_prompt`** - `app/services/prompt_rendering.py` (`render_prompt`, a
  `_PLACEHOLDER` regex `\{\{\s*(\w+)\.(\w+)\s*\}\}`); `app/core/exceptions.py` gets
  `PromptRenderError` (a plain `Exception` subclass, not `PromptTemplateError` - rendering has
  nothing to do with a database lookup failing).
  *Done when:* a new `tests/test_prompt_rendering.py` passes with no database fixture at all -
  single and multiple placeholders substitute correctly; a repeated placeholder substitutes
  every occurrence; `None` renders as `""`; an unknown namespace raises; a known namespace
  with an unknown field raises; text with no placeholders is returned unchanged; a malformed
  single-brace token is left untouched. `ruff check apps/api` clean.

- [x] **Step 2 - `AssistantVersion` wiring** - `app/models/assistant_version.py` gets
  `prompt_template_id`, `prompt_version` columns; one additive migration; `app/schemas/
  assistant_version.py`'s `AssistantVersionCreate` gets both fields (optional, both-or-neither
  model validator) and `AssistantVersionResponse` exposes both; `app/repositories/
  assistant_version.py`'s `create()` gets the two new parameters; `app/services/
  assistant_version.py`'s `create_version` resolves the referenced prompt template/version
  through the caller's workspace when both are given (reusing 12a's
  `prompt_template_service.resolve_prompt_template` and `prompt_version_service.get_version`,
  raising the existing `PromptTemplateNotFound`/`PromptVersionNotFound`); `app/api/v1/
  assistant_versions.py`'s create route maps those exceptions to 404.
  *Done when:* `tests/test_assistant_versions.py` (extended) passes - creating a version with
  neither field still works exactly as before (regression); creating one with both fields set
  to a real prompt template/version in the same workspace succeeds and the response echoes
  them back; a `prompt_template_id` from a sibling workspace 404s; a `prompt_version` that
  doesn't exist for that template 404s; `prompt_version` without `prompt_template_id` (or vice
  versa) is a 422 validation error. Full backend suite green. `ruff check apps/api` clean,
  migration verified `upgrade head` / `downgrade -1` / `upgrade head`.

## Files / areas

**New**
- `apps/api/app/services/prompt_rendering.py`
- `apps/api/tests/test_prompt_rendering.py`
- One Alembic migration (additive columns on `assistant_versions`).

**Modified**
- `apps/api/app/core/exceptions.py` (`PromptRenderError`).
- `apps/api/app/models/assistant_version.py`, `apps/api/app/schemas/assistant_version.py`,
  `apps/api/app/repositories/assistant_version.py`, `apps/api/app/services/
  assistant_version.py`, `apps/api/app/api/v1/assistant_versions.py`.
- `apps/api/tests/test_assistant_versions.py`.

**Unchanged**
- No frontend file. 12a's `PromptTemplate`/`PromptVersion` files are read from, not modified.

## Data / contracts

**`render_prompt(content, context)`** - pure, synchronous, no I/O. Locked signature: 12c and
item 20 both eventually call this with a real context dict; changing the signature later is a
breaking change for both.

**`AssistantVersionCreate`/`AssistantVersionResponse`** gain `prompt_template_id: uuid.UUID |
None`, `prompt_version: int | None`. Both `null` unless explicitly set at creation - there is
no default-resolution behavior (see Out of scope).

## Testing

Step 1 is pure logic with a dedicated unit-test file and no database fixture - the class of
function `coding-standards.md`'s testing gate calls out explicitly. Step 2 extends the existing
API-level test file, matching `test_assistant_versions.py`'s established coverage shape:
success, regression (existing behavior unchanged), cross-tenant 404, and validation-error
paths.

## Notes for the AI

- **`render_prompt` takes no dependencies.** No `AsyncSession`, no provider, nothing async -
  it is deliberately usable in a fast unit test and, later, directly inside the realtime
  turn loop without an I/O detour.
- **Missing value vs. missing variable are different failures.** `None` is a legitimate value
  (e.g., an unknown caller name) and renders blank. A namespace or field the context doesn't
  define at all is an authoring bug in the template and must raise, not blank out - silently
  swallowing a typo'd variable name would ship a broken prompt to a live call.
- **`prompt_template_id`/`prompt_version` are both-or-neither.** Do not accept one without the
  other; do not invent auto-resolution of "the current published version" - that is a decision
  for whichever feature first actually reads this field (most likely item 20).
- **Reuse 12a's resolution helpers, don't duplicate them.** `prompt_template_service.
  resolve_prompt_template` and `prompt_version_service.get_version` already do exactly the
  tenant-scoped lookup this needs.
- Continuing straight through 12c, 13a, 13b after this one completes, per the "entire step 12
  and step 13 in one go" instruction - each still gets its own spec, branch, and merge, just
  without a pause for interactive approval at each merge.
