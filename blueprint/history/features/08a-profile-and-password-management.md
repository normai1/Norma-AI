# Feature: Profile and password management

**From build-plan:** feature 8a
**Status:** not started

## Goal

Let a signed-in user maintain their own account: update their name and avatar, and
change their password with the security a credential change demands — verify the
current password first, then revoke every existing session so a stolen refresh token
dies with the change, and hand the caller a fresh token pair so they stay signed in on
the device they just used.

Backend only. The settings screens that consume these endpoints are 8c.

## Design reference

None. No UI ships in this feature.

## In scope

- `user_repo.update(db, user, *, ...)` — partial update of `full_name` and `avatar_url`.
- `PATCH /api/v1/auth/me` — update the signed-in user's own profile, returns
  `UserResponse`.
- `POST /api/v1/auth/me/password` — change the signed-in user's own password, returns
  `AuthResponse` (a fresh token pair).
- New schemas: `ProfileUpdate`, `PasswordChangeRequest`.
- A `PASSWORD_CHANGE_RATE_LIMIT` rule, applied per user id.
- New `AuthError` subclasses for the password-change failure modes.
- pytest coverage for all of the above in `tests/test_auth_profile.py`.

## Out of scope

- **Avatar file upload.** `avatar_url` accepts a URL string only. Real upload needs
  object storage and a processing path, which arrives with items 14 and 20 — not here.
- **Email change.** The email is the login identifier, so changing it needs a
  verification round-trip to the new address plus uniqueness handling on a live
  credential. That is its own feature, not a field on a profile PATCH.
- **Password reset / forgot password.** An unauthenticated flow needing email delivery
  and a single-use token. Different threat model, different endpoints.
- **Account deletion or self-deactivation.** Needs a decision on what happens to the
  user's organizations (especially a sole owner) before it can be specced.
- **Two-factor authentication.**
- **All UI.** That is 8c.
- **Organization and workspace settings.** That is 8b.

## Build loop

Build one step at a time, never the whole feature at once.

1. Plan mode lays out the step before any code.
2. The AI implements just that step.
3. It shows the diff (not full files); you read it and understand it.
4. You approve, then choose whether to commit a checkpoint or roll straight on.
   Checkpoints are optional; `/complete` makes the real feature-level commit at the end.

Never accept a step you haven't read. If a diff is too big to review, the step was too big, so split it.

## Build steps

- [x] **Step 1 - Profile update** - add `user_repo.update`, a `ProfileUpdate` schema, and
  `PATCH /api/v1/auth/me`. Use `model_fields_set` / `exclude_unset` so an omitted field
  is untouched while an explicit `null` clears the column (see Data / contracts — this
  deliberately diverges from the existing repo convention). Validate `avatar_url` as an
  absolute http(s) URL.
  *Done when:* `pytest` passes with tests proving: setting `full_name` persists and is
  reflected by `GET /api/v1/auth/me`; setting `avatar_url` persists; an explicit
  `"full_name": null` clears it to NULL; omitting `avatar_url` leaves the stored value
  untouched; a non-URL `avatar_url` is rejected 422; an over-length `full_name` is
  rejected 422; and an unauthenticated request is rejected 401.

- [x] **Step 2 - Password change** - add `PasswordChangeRequest`, the `AuthError`
  subclasses, an `auth_service.change_password` function, and
  `POST /api/v1/auth/me/password`. The service verifies the current password, rejects a
  no-op change, hashes the new one, revokes **all** sessions via
  `session_repo.revoke_all_for_user`, then issues a fresh pair via
  `auth_service.issue_tokens`.
  *Done when:* `pytest` passes with tests proving: a correct current password returns
  200 with a working new refresh token; a wrong current password returns 401 and leaves
  the password unchanged (the old one still logs in); a new password identical to the
  current one returns 400; a new password under 8 characters returns 422; **every
  refresh token issued before the change is rejected afterwards** (proving revocation);
  the user can log in with the new password and cannot with the old; and an
  unauthenticated request is rejected 401.

- [x] **Step 3 - Rate-limit the password endpoint** - add `PASSWORD_CHANGE_RATE_LIMIT`
  to `app/core/rate_limit.py` and apply it in the route via the existing `_rate_limit`
  helper, keyed on the authenticated user id (`password_change:{user_id}`), not the IP.
  *Done when:* `pytest` passes with a test proving that attempts beyond the limit within
  the window return 429 with a `Retry-After` header, following the existing conventions
  in `tests/test_rate_limit.py`; and that a successful change still works within the
  limit.

## Files / areas

**New**
- `apps/api/tests/test_auth_profile.py`

**Modified**
- `apps/api/app/repositories/user.py` — adds `update`.
- `apps/api/app/schemas/auth.py` — adds `ProfileUpdate`, `PasswordChangeRequest`.
- `apps/api/app/services/auth.py` — adds `change_password`.
- `apps/api/app/api/v1/auth.py` — adds the two routes.
- `apps/api/app/core/exceptions.py` — adds the password-change `AuthError` subclasses.
- `apps/api/app/core/rate_limit.py` — adds `PASSWORD_CHANGE_RATE_LIMIT` (Step 3).

**Unchanged**
- `apps/api/app/models/user.py` — `full_name` and `avatar_url` already exist and are
  nullable. **No migration is needed in this feature.**
- `apps/api/app/core/tokens.py` — the access-token payload is not extended; see the
  session-revocation note in Data / contracts for why that stays unnecessary.
- Everything under `apps/web/` — no frontend change until 8c.

## Data / contracts

No schema change, no migration. Three contracts are locked here.

**1. Password change returns a fresh `AuthResponse` and kills every other session.**
The access token carries only `sub` (the user id) — there is no session identifier in
it, so the API cannot tell *which* session the caller is using and therefore cannot
spare it selectively. Rather than extend the token payload, the endpoint revokes
**all** sessions and immediately issues a new pair in the response. Net effect: the
current device stays signed in (it has new tokens), every other device is signed out.
This is the stronger security posture and needs no token-shape change.

**Load-bearing for 8c:** the client *must* store the returned tokens, exactly as it does
after login. Ignoring the response body will silently sign the user out on their next
request.

**2. PATCH uses `exclude_unset`, not `None`-means-untouched.**
The existing repo convention (`organization_repo.update`, `workspace_repo.update`)
treats `None` as "leave it alone". That works there because neither `name` nor
`settings` is nullable. `full_name` and `avatar_url` *are* nullable, so "clear this
field" is a legitimate operation that the existing convention cannot express. This
endpoint therefore distinguishes an omitted key from an explicit `null`:

```
{}                          -> nothing changes
{"full_name": "Jane Doe"}   -> full_name set, avatar_url untouched
{"full_name": null}         -> full_name cleared to NULL
```

This divergence is deliberate and confined to profile updates. Do not "fix" the
organization/workspace repositories to match — they have no clearable field.

**3. Both endpoints operate strictly on the authenticated user.**
There is no `user_id` path parameter, query parameter, or body field on either route.
The target is always `CurrentUser`. A client-supplied user id must never be able to
select whose profile or password is changed.

**Schemas:**

```python
class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    avatar_url: HttpUrl | None = None   # stored as str(...)

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
```

`new_password` mirrors `RegisterRequest.password` exactly. `current_password` uses
`min_length=1` like `LoginRequest.password` — it is being checked, not set, so the
strength rule does not apply to it (an account created before a rule change may hold a
shorter one).

## Testing

The backend gate is live — `pytest` is declared in `AGENTS.md`, so every step ships its
tests in the same diff.

**In-scope logic needing tests:**
- `user_repo.update`'s partial/clear semantics (Step 1).
- `auth_service.change_password`: current-password verification, no-op rejection, hash
  replacement, session revocation, token reissue (Step 2).
- The rate-limit rule application (Step 3).

**Test file:** `tests/test_auth_profile.py`, covering both profile and password change —
matching the existing granularity where `test_auth_session.py` covers both refresh and
logout. Reuse the shared `_signed_in` helper from `conftest.py` (moved there in feature
4a); do not define a sixth local copy.

**Not tested:** nothing meaningful is deferred. There is no UI in this feature, so no
browser verification applies.

**Manual path:** with the API running, `PATCH /api/v1/auth/me` with a bearer token and a
new `full_name`, then `GET /api/v1/auth/me` to see it; then
`POST /api/v1/auth/me/password` and confirm the old refresh token is rejected by
`POST /api/v1/auth/refresh` while the newly returned one works.

## Notes for the AI

- **Follow the layering:** route → service → repository. The password-change logic
  (verify, revoke, reissue) belongs in `auth_service.change_password`, not inline in the
  route. The route only translates domain exceptions into `HTTPException`s, using the
  small module-level constants this file already uses.
- **Reuse, do not reimplement:** `verify_password` and `hash_password`
  (`app/core/security.py`), `session_repo.revoke_all_for_user`, and
  `auth_service.issue_tokens` all already exist and do exactly what is needed.
  `issue_tokens` also stamps `last_login_at` — that is acceptable and arguably correct
  here, but call it out in the step summary rather than letting it pass unnoticed.
- **A wrong current password is 401, not 403.** It is a credential failure, matching
  `_INVALID_CREDENTIALS` in this same route file. A no-op change (new == current) is
  400 — the request is understood and authorized, just pointless and destructive
  (it would revoke every session for nothing).
- **Never log the passwords or the issued tokens** (CLAUDE.md §27). The existing routes
  log neither; match that.
- **Rate-limit key is the user id, not the IP.** The endpoint is authenticated, so the
  user is the right subject. An IP key would let one user on a shared NAT lock out
  another.
- **Known accepted interleaving:** two simultaneous password changes for the same user
  can each revoke the other's freshly issued session, leaving one caller's returned
  refresh token already dead. The outcome is "re-log in on the losing tab", no invariant
  is violated, and the window is tiny. Do not add locking for this; it is recorded here
  so it is not mistaken for a bug later.
- **`HttpUrl` normalizes.** Pydantic v2 may append a trailing slash to a bare origin.
  Store `str(payload.avatar_url)` and assert against the normalized form in tests rather
  than fighting it.
- No migration, no model change, no frontend change. If a step seems to need one, the
  step has drifted from the spec — stop and reconcile.
