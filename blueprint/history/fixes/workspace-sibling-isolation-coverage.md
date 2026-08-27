# Fix: Workspace sibling-isolation test coverage

**Type:** Fix
**Fixes:** F-31

## The problem

`require_workspace_access` and `workspace_repo.list_for_user` both scope the `WorkspaceMember`
check to the exact `workspace_id` in play, so a member granted access to workspace A should never
reach or see workspace B in the same organization. The logic is correct on inspection, but
`apps/api/tests/test_workspaces.py` never tests it - existing coverage only proves "zero
memberships" and "the one membership that matches," never "a membership that exists but doesn't
match the workspace being requested."

## The fix

Add a test that creates two workspaces in the same organization, grants a member access to only
one of them (inserting the `WorkspaceMember` row directly through the `db` fixture, the same
technique the existing `test_get_succeeds_for_an_explicit_member` already uses, since there is no
member-add endpoint yet), and asserts:

- `GET` on the workspace they were *not* granted returns 404.
- `GET` on the workspace they *were* granted still returns 200 (proves the negative case isn't
  masking a broken positive case).
- `list` for that member includes only the granted workspace, not the sibling.

Must not touch application code - `require_workspace_access` and `list_for_user` are already
correct; this only proves it.

## Build steps

- [x] **Step 1 - Add the sibling-isolation test** - add
  `test_member_access_to_one_workspace_does_not_reach_a_sibling` to `test_workspaces.py`. *Done
  when:* the test passes, and temporarily changing `workspace_member_repo.get`'s filter to drop
  the `workspace_id` condition (leaving only `user_id`) makes it fail - proving the test actually
  catches the regression it exists to catch, then revert that temporary change.

## Verify

`pytest apps/api/tests/test_workspaces.py -q` passes, and the full backend suite
(`pytest apps/api/tests`) stays green.
