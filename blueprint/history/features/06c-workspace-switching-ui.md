# Feature: Workspace switching UI

**From build-plan:** feature 6c (under 6. Workspaces)

## Goal

Give operators a way to see, create, and switch between workspaces, and manage who has access to
each one - the frontend for the `require_workspace_access` backend 6a and 6b already built. The
backend is fully done; this feature only wires UI to existing endpoints.

## In scope

- `lib/workspaces.ts` - typed API client functions for every existing workspace endpoint.
- A workspace list page under an organization: shows every workspace the caller can see (already
  scoped server-side - a manager sees all, others see only what they're granted), and a
  create-workspace form for managers.
- A workspace detail page: shows the roster of who has access, with a manager-only add-member
  (picked from the organization's existing member roster) and remove-member control.
- A link from the organization detail page to its workspaces list.

## Out of scope

- **A persistent nav "switcher" widget.** There is no application shell yet (item 7, not started).
  The workspace list page itself is the switcher, the same way the existing organizations list
  page already serves as the de facto organization switcher - a dedicated widget is item 7's job
  once there is a shell to put it in.
- **Renaming a workspace or editing its settings.** The backend `PATCH` endpoint exists, but the
  organization detail page has the identical gap today (no edit-name UI), so this isn't a new
  inconsistency - just not built yet anywhere in the app. A future settings feature covers both
  together.
- **Any backend change.** 6a and 6b already built and tested every endpoint this feature calls.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - `lib/workspaces.ts`** - add types (`Workspace`, `WorkspaceMember`) and
  `listWorkspaces`, `createWorkspace`, `getWorkspace`, `listWorkspaceMembers`,
  `addWorkspaceMember`, `removeWorkspaceMember`, matching `lib/organizations.ts`'s exact
  `authorizedJson`/`authorizedEmpty` call shape - no new fetch abstraction. *Done when:*
  `npm run lint` and `npm run build` are clean.

- [x] **Step 2 - Workspace list and create** - add `app/organizations/[organizationId]/workspaces/page.tsx`:
  auth-guarded like the existing organization pages, fetches the organization first (to read the
  caller's role via the existing `canManage` helper from `lib/organizations.ts` - workspace
  creation is manager-only, unlike organization creation, so the create form is conditionally
  shown, not unconditional like `organizations/page.tsx`'s), lists workspaces as links to their
  detail page, an `EmptyState` when the list is empty (matching `organizations/page.tsx`'s
  no-organizations copy, reworded for workspaces), and a create-workspace form for managers only
  with a `required` name input (client-side, backstopping the existing 422 the API already
  returns for an empty name). Add a link to this page from
  `app/organizations/[organizationId]/page.tsx`, and a "back to workspaces" link on this page
  pointing at the parent organization. Reuse `PageShell`/`Card`/`EmptyState`/`ErrorText`/`Button`
  from `components/organizations/ui.tsx` - no new component set. *Done when:* `npm run lint` and
  `npm run build` are clean, and the live dev server shows: the empty state with zero workspaces,
  a created workspace appearing in the list immediately after submit, and the create form absent
  entirely for a non-manager (verified via `/check` or a manual walkthrough, since this is
  UI/integration behavior).

- [x] **Step 3 - Workspace member management** - add
  `app/organizations/[organizationId]/workspaces/[workspaceId]/page.tsx`: shows the workspace name,
  a "back to workspaces" link, and its member roster (`EmptyState` when nobody has access yet);
  for managers, an add-member control populated from the organization's member list (`listMembers`
  from `lib/organizations.ts`) filtered to exclude anyone already on the workspace roster - disabled
  with a short message when that filtered list is empty, rather than an empty unusable dropdown -
  and a remove button per member; non-managers see the roster read-only, matching the existing
  `EmptyState` pattern for "only owners and admins can manage members." *Done when:* `npm run lint`
  and `npm run build` are clean, and the live dev server shows: the empty roster state, a full
  add-then-remove cycle actually working end to end, and the add control correctly disabled once
  every organization member already has access (verified via `/check` or a manual walkthrough).

## Files / areas

| Path | Change |
| --- | --- |
| `apps/web/lib/workspaces.ts` | new |
| `apps/web/app/organizations/[organizationId]/workspaces/page.tsx` | new |
| `apps/web/app/organizations/[organizationId]/workspaces/[workspaceId]/page.tsx` | new |
| `apps/web/app/organizations/[organizationId]/page.tsx` | edit - link to workspaces |

## Data / contracts

None new. This feature consumes the `Workspace`/`WorkspaceMember` API contracts 6a and 6b already
locked; it does not define any new shape.

## Testing

UI and integration-only, per `coding-standards.md`'s scope rule - no unit-test coverage predicted;
verified by `npm run lint`, `npm run build`, and live dev-server evidence (`/check` or a manual
walkthrough) per step, the same gate every prior frontend feature in this project has used.

## Notes for the AI

- **Match `lib/organizations.ts` and the two existing organization pages exactly** - same
  `authorizedJson`/`authorizedEmpty` call shape, same `PageShell`/`Card`/`Button`/`EmptyState`/
  `ErrorText` reuse, same auth-guard-via-`fetchCurrentUser` pattern, same `run(action)` error-handling
  helper for mutations. Do not invent a new pattern for workspaces.
- **Workspace creation is manager-only; organization creation is not.** Don't copy
  `organizations/page.tsx`'s unconditional create form - gate it on `canManage(organization.role)`,
  fetched from the parent organization first.
- **No em dashes.** Match the existing frontend's TypeScript/React conventions (function
  components, `@/*` imports, Tailwind utility classes).
