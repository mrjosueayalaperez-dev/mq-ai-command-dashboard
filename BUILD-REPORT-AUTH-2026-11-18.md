# BUILD REPORT — Real Login (Auth) · November 18, 2026

> **Correction note — 2026-11-25 (documentation only; the body of this
> report is unchanged history).** Three statements in this report are
> superseded by later facts and are corrected here so no reader is
> misled:
>
> 1. **Language-catalog count.** The Web UI section below states the
>    four catalogs hold "64 keys each." The catalogs in
>    `web/i18n/` (EN/ES/PT/HT) now contain **69 keys each**, verified
>    by direct count on 2026-11-25. The figure in the body reflects
>    the catalogs at the time of writing and is marked inline.
> 2. **HTTP first-run bootstrap.** The `app/auth_cli.py` section below
>    states "There is no HTTP setup route." That is no longer true:
>    Batch 010 (Nov 18, 2026) implemented an HTTP first-run bootstrap —
>    `GET /api/auth/bootstrap` (owner-exists status) and
>    `POST /api/auth/bootstrap` (creates the first owner) in
>    `app/main.py`, backed by `auth.bootstrap_owner_web` in
>    `app/auth.py`. It creates the first owner only while none exists
>    and refuses (403) permanently after. This report's CLI-only
>    description was incomplete documentation — the endpoint was
>    implemented, not missing.
> 3. **Publication.** The closing status below says nothing was pushed
>    anywhere; that was accurate at the time of writing. The repository
>    has since been published to GitHub
>    (`mrjosueayalaperez-dev/mq-ai-command-dashboard`).

**Scope:** replace the Phase 1/2 header stand-in (`X-Role` / `X-Actor`)
with real login authentication, mirroring the M&Q CRM's merged auth
design (its Batch 009/009b), adapted to this codebase's audit log and
role names (`owner | operator | viewer`). Founder direction: the
product must be fully functioning, not a test version.

## What was built

**`app/auth.py`** — password accounts and server-side sessions:
- argon2id password hashing (RFC 9106 second-recommended parameters:
  64 MiB, 3 passes, 2 lanes); only argon2id hashes are ever accepted.
  Password policy: 12–256 chars, common-password refusal, must not
  contain the username. Unknown-user logins burn an equivalent verify
  so timing does not reveal which usernames exist.
- Sessions: random 256-bit tokens, only the SHA-256 is stored;
  30-minute idle / 12-hour absolute expiry (env-tunable within clamps:
  `MQ_CMD_SESSION_IDLE_MINUTES`, `MQ_CMD_SESSION_MAX_HOURS`); expired
  sessions revoked on sight; role/permission changes, disable,
  password change/reset, and two-step enable all revoke sessions.
- Throttling: per-account (5) and per-address (20) failure limits in a
  15-minute window, exponential lockout 60s → 1h cap; throttle keys
  are stored hashed (no raw IP). Failure responses are one generic
  message for unknown user / wrong password / disabled account.
- Every auth event is written to the product's hash-chained audit log
  with the session username as actor and reason codes only — never a
  password, token, or code.

**`app/totp.py`** — two-step login (TOTP) for the owner, same design
as the CRM's:
- RFC 6238 (HMAC-SHA1, 6 digits, 30s steps, ±1 step skew) with
  **anti-replay**: the last accepted step is stored; a code at or
  before it is refused — including the code spent confirming
  enrollment.
- Enroll → confirm → enabled, issuing **10 one-time recovery codes**
  (salted SHA-256 at rest, shown once). The secret is stored
  AES-256-GCM-encrypted, key from `MQ_CMD_TOTP_KEY` or the `totp.key`
  file beside the database; a wrong/missing key fails **closed**.
- Login for a two-step account: password ⇒ short-lived **challenge**
  (5 min, 5 attempts, hashed at rest), never a session; a TOTP code
  or a recovery code exchanges it for the session. Wrong codes feed
  the same lockout as wrong passwords, and a correct password does
  not reset a two-step account's failure counter.
- Disable via API requires password + a valid code; lost-phone
  break-glass is CLI-only.

**`app/auth_cli.py`** — server-shell only:
- `create-owner --username <name>` — one-time bootstrap of the first
  owner; password from a no-echo prompt (twice) or `--password-stdin`;
  never on the command line, never printed/logged; refuses once any
  owner exists. There is no HTTP setup route and no default password.
  *(Superseded in part — see the 2026-11-25 correction note at the top
  of this report: an HTTP first-run bootstrap, GET/POST
  `/api/auth/bootstrap`, was implemented in Batch 010. There is still
  no default password.)*
- `reset-password --username <name>` — recovery incl. owners: policy
  applied, all sessions + pending challenges revoked, lockout cleared.
- `reset-totp --username <name> --yes` — break-glass factor removal.

**Middleware + RBAC (`app/main.py`, `app/rbac.py`)**:
- `_AuthMiddleware` guards every `/api` path: strips client
  `X-Role`/`X-Actor` (any case), resolves the session from
  `Authorization: Bearer` or the HttpOnly cookie (a token of the wrong
  kind is refused), enforces CSRF (per-session token + Origin check)
  on cookie-session writes, and answers **401 for everything else**
  except the open paths `/api/health`, `/api/auth/login`,
  `/api/auth/login/verify` — deny by default, demo data included.
- `Identity` is now resolved from the session; the audit actor is the
  session's username. Guards answer 401 unauthenticated / 403
  wrong-role. Handlers and services were otherwise untouched.
- Endpoints: `POST /api/auth/login` (`mode: "cookie" | "token"`),
  `POST /api/auth/login/verify`, `POST /api/auth/logout`,
  `GET /api/auth/me` (returns the CSRF token to same-origin JS and a
  `owner_two_step_not_enrolled` warning), `GET /api/auth/totp`,
  `POST /api/auth/totp/enroll|confirm|disable`,
  `POST /api/auth/password` (self-service change; other sessions
  signed out), and owner-only user admin: `GET/POST /api/users`,
  `POST /api/users/{id}/role|disable|enable|password` — an **owner's**
  password is refused over the API (CLI recovery only). The last
  active owner can neither be demoted nor disabled.
- Startup logs a warning while no owner exists or an owner has no
  two-step enrolled.

**Web UI**: a real sign-in page in the product identity (black /
graphite / gold / ivory) with the two-step step (authenticator code or
recovery code), a signed-in user chip with sign-out, CSRF tokens on
all state-changing calls, and a return to the sign-in gate on any 401.
14 auth keys were added to all four catalogs (EN/ES/PT/HT, **64 keys
each at the time of writing — corrected 2026-11-25: the catalogs now
contain 69 keys each; see the correction note at the top of this
report**, parity-tested; PT/HT authored, native-speaker review still
outstanding — no production-quality language claim).

**Schema**: `users`, `sessions`, `auth_throttle`, `totp_factors`,
`recovery_codes`, `login_challenges` added to `app/db.py`
(`MQ_CMD_DB` unchanged). Dependencies pinned: `argon2-cffi==25.1.0`,
`cryptography==50.0.2`.

## Tests — 116 passed, 0 failed

`~/.venvs/cmd-dash/bin/python -m pytest tests/ -q` → **116 passed**.

- **All 82 pre-existing tests migrated and passing.** The fixtures now
  authenticate for real: conftest bootstraps owner/operator/viewer
  accounts in the database and logs them in (token mode); the default
  client is pre-authenticated as the owner so header-less module calls
  keep working, and per-request bearer headers override it. No test
  was weakened — the old "missing role header ⇒ viewer" case is now
  "no session ⇒ 401", and `test_rbac.py` was rewritten for the session
  model (9 tests, incl. header-spoofing conferring nothing and a
  viewer + `X-Role: owner` header failing to escalate).
- **34 new auth tests** (`tests/test_auth.py` + the rbac additions):
  token-mode login + `/me`; generic failure for wrong password and
  unknown user; **lockout** at the account limit (429, correct
  password still refused, recovery after the window); disabled account
  refused; **idle expiry** (401 after 30 min) and **absolute expiry**
  (401 after 12 h) via the replaceable auth clock; cookie login with
  **CSRF refusal** (403 without token, 201 with) and **kind
  separation** (cookie token as Bearer ⇒ 401, bearer token as cookie
  ⇒ 401); logout revoking the session; **CLI bootstrap** (subprocess:
  owner created, stored hash is `$argon2id$…`, second bootstrap
  refused, CLI password recovery verifies); password change (wrong
  current ⇒ 403; other sessions revoked; old password dead, new one
  works); user-admin rules (operator refused, weak password and
  duplicate refused, owner password reset via API ⇒ 403, non-owner
  reset works); **full TOTP flow** — enroll, confirm, sign out,
  password ⇒ challenge with no session, wrong code ⇒ 401, **enrollment
  code replayed ⇒ 401**, recovery code ⇒ session, same recovery code
  again ⇒ 401, fresh next-step code ⇒ session; TOTP enrollment refused
  for non-owner roles; **unauthenticated 401 spot-checks across 18
  endpoints spanning all 11 modules** (health stays open); audit actor
  equals the session user on a `login_success` entry.

## Live smoke — 17/17 checks passed

Fresh database → `auth_cli create-owner` → uvicorn on 127.0.0.1:8123:
unauthenticated overview 401; health open; cookie login 200 (with the
owner-two-step warning); `/me`; overview 200; cookie write without
CSRF 403, with CSRF 201; TOTP enroll + confirm (10 recovery codes);
logout; old session 401; password-only login returns a challenge and
**sets no session cookie**; enrollment-code replay 401; recovery code
completes sign-in; overview 200; `/me` shows two-step enabled and the
warning gone. Server stopped afterward; nothing left running.

## Android app — what it must send (next step, mechanical)

The app currently sends `X-Role` / `X-Actor` headers, which the server
now strips and ignores — the app gets 401 until it adopts the login:

1. `POST /api/auth/login` with JSON `{"username", "password",
   "mode": "token"}`.
   - Plain account ⇒ `200 {"token", "token_type": "bearer", "user":
     {username, display_name, role}, "idle_minutes",
     "absolute_expires_at"}`. Store the token (existing sealed
     storage) and send `Authorization: Bearer <token>` on every call.
     No CSRF header is needed (or accepted) for bearer sessions.
   - Two-step account ⇒ `200 {"two_step_required": true, "challenge",
     "challenge_expires_in": 300, "methods": ["totp",
     "recovery_code"]}` and **no token**. Then
     `POST /api/auth/login/verify` with `{"challenge", "code"}` **or**
     `{"challenge", "recovery_code"}` (exactly one) ⇒ the same token
     response as above. This is the same challenge shape the CRM app
     already implements.
   - Errors: `401 {"detail": "invalid username or password"}` for bad
     credentials; `429` with a `Retry-After` header when locked out.
2. `GET /api/auth/me` with the bearer token ⇒ user + session info
   (use it at startup to validate a stored token; 401 ⇒ signed out).
3. `POST /api/auth/logout` with the bearer token ⇒ revokes the
   session; drop the stored token.
4. Sessions idle out after 30 minutes and die at 12 hours: any 401
   ⇒ return to the sign-in screen. There is no refresh token.

## Honest status

**IMPLEMENTED + TESTED locally · NOT INDEPENDENTLY VERIFIED · NOT
MERGED · NOT DEPLOYED.** $0 spent; no external network calls from the
app; no other project touched; nothing pushed anywhere. Remaining
known limits: the Android app still speaks the retired header
stand-in (integration note above); PT/HT strings are authored, not
native-speaker reviewed; the service remains a local build until a
deployment is separately authorized.
