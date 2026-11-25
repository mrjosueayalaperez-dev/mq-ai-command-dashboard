# SECURITY VERIFICATION REPORT — M&Q AI Command Dashboard

**Prepared:** 2026-11-25
**Type:** Documentation / verification only. No production code was
changed for this report; the only edits on this branch are
documentation corrections (README and the November 18 build reports).
**Basis:** Source code and test suite of the repository
`mrjosueayalaperez-dev/mq-ai-command-dashboard` at main commit
`1e46d09`, read directly, plus a full local test-suite run (below).
**Status vocabulary:** VERIFIED = the control is present in code and
covered by passing tests. PARTIALLY VERIFIED = present in code with a
stated gap in evidence or scope. NOT VERIFIED = no code/test evidence
found. A control is never marked verified from documentation alone.

## Test suite (executed 2026-11-25)

Command: `~/.venvs/cmd-dash/bin/python -m pytest tests/ -q`
(proxy variables unset, `no_proxy=localhost,127.0.0.1`).

- Collected: **129**
- Passed: **129**
- Failed: **0**
- Skipped: **0**

(One Starlette deprecation warning, unrelated to any assertion.)

## 1. Authentication — VERIFIED

- Password hashing is argon2id only. `app/auth.py` builds
  `PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)`
  (RFC 9106 second-recommended parameters); `hash_password()` raises
  unless the result starts with `$argon2id$`, and `verify_password()`
  returns `False` for any stored value that is not an argon2id hash —
  no other hash format is ever accepted.
- Password policy (`validate_password`, `app/auth.py`): 12–256
  characters, a common-password refusal list, at least 5 distinct
  characters, and the password must not contain the username.
- Login (`authenticate`, `app/auth.py`): one generic failure message
  (`"invalid username or password"`) for unknown user, wrong
  password, and disabled account; unknown usernames burn an
  equivalent argon2 verify (`_burn_verify`) so timing does not reveal
  which usernames exist.
- Throttling: per-account (5) and per-address (20) failure limits in
  a 15-minute window, exponential lockout from 60 s doubling to a
  3,600 s cap (`_record_failure`, `_locked_for`); throttle keys are
  stored as SHA-256 hashes — no raw IP address is stored.
- First-run bootstrap (Batch 010): `GET /api/auth/bootstrap` returns
  only `{"owner_exists": bool}` (`app/main.py`); `POST
  /api/auth/bootstrap` calls `auth.bootstrap_owner_web`
  (`app/auth.py`), which serializes check-and-create under a lock
  plus a write transaction and refuses permanently once any owner
  exists; the route maps that refusal to HTTP 403 (`app/main.py`).
  The CLI path (`auth.bootstrap_owner`, `app/auth_cli.py`) applies
  the same one-time rule. There is no default password anywhere.
- Test evidence: `tests/test_auth.py` (14 tests),
  `tests/test_bootstrap.py` (10 tests) — all passing.

## 2. Sessions — VERIFIED

- `create_session` (`app/auth.py`): 256-bit random tokens
  (`secrets.token_urlsafe(32)`); only the SHA-256 of the token is
  stored. Cookie sessions additionally get a per-session CSRF token.
- Expiry: 30-minute idle / 12-hour absolute by default, env-tunable
  within clamps (`MQ_CMD_SESSION_IDLE_MINUTES` 5–240,
  `MQ_CMD_SESSION_MAX_HOURS` 1–72), so configuration cannot create a
  never-expiring session. `resolve_session` revokes expired sessions
  on sight (idle or absolute) and refuses disabled users' sessions.
- Revocation: logout revokes the presented session
  (`auth.logout`); password change revokes all other sessions;
  password reset, role change, account disable, and two-step
  enable/disable all revoke the user's sessions
  (`revoke_user_sessions` call sites in `app/auth.py` and
  `app/totp.py`).
- Kind separation: a cookie session presented as a bearer token (or
  vice versa) is refused — enforced in both the middleware
  (`app/main.py`, `_AuthMiddleware`) and `app/rbac.py`
  (`resolve_identity`).
- The web session cookie is set HttpOnly, `Secure`, `SameSite=strict`
  (`_login_response`, `app/main.py`).
- Test evidence: session, expiry, revocation, and kind-separation
  cases in `tests/test_auth.py` — passing.

## 3. CSRF protection — VERIFIED

- `_AuthMiddleware.__call__` (`app/main.py`): for cookie sessions,
  every non-safe method (not GET/HEAD/OPTIONS) must (a) pass an
  Origin check — when an `Origin` header is present, its host part
  must equal the `Host` header — and (b) carry the session's CSRF
  token in the `X-CSRF-Token` header, compared with
  `hmac.compare_digest` (`auth.csrf_ok`, `app/auth.py`). Failures
  return 403 before routing.
- Bearer-token sessions are exempt by design: browsers do not attach
  `Authorization` headers automatically, so they are not CSRF-able
  (stated in the middleware docstring and consistent with the code).
- The middleware also strips client-supplied `X-Role` / `X-Actor`
  headers (any case) from every request before routing.
- Test evidence: cookie write without a CSRF token refused (403) and
  with the token accepted (201), in `tests/test_auth.py` — passing.

## 4. TOTP (two-step login) — VERIFIED

- Implementation: `app/totp.py` — RFC 6238 TOTP (HMAC-SHA1, 6 digits,
  30-second steps, ±1 step skew) in `hotp()` / `_match_step()`,
  standard library only.
- Anti-replay: the last accepted time step is stored per factor; a
  code at or before it is refused, and the step advance is an atomic
  conditional `UPDATE ... WHERE last_used_step < ?` whose rowcount
  must be exactly 1 — including the code spent confirming enrollment.
- Secret at rest: AES-256-GCM (`AESGCM`) with associated data bound
  to the user id (`_seal` / `_open`); the key comes from
  `MQ_CMD_TOTP_KEY` or a `totp.key` file (mode 0600) beside the
  database; a per-row key-check value detects a wrong key, and a
  missing/wrong key fails closed (503) — it never skips the second
  step.
- Recovery codes: 10 one-time codes of 80 random bits, stored as
  salted SHA-256, consumed by a conditional `UPDATE ... WHERE
  used_at IS NULL` (single use enforced in the write itself).
- Login challenge: a correct password for a two-step account yields
  only a pending challenge (5 minutes, 5 attempts, token stored
  hashed) — never a session (`begin_login` / `complete_login`).
  Wrong codes feed the same account/address lockout as wrong
  passwords, and a correct password does not reset a two-step
  account's failure counter.
- Enrollment is owner-role only (`ENROLL_ROLES = ("owner",)`);
  disabling via the API requires password + a valid code; lost-phone
  break-glass is CLI-only (`totp.cli_reset`, `app/auth_cli.py`).
- Test evidence: full flow in `tests/test_auth.py` — wrong code,
  replayed enrollment code, recovery code single use, fresh code —
  passing.

## 5. RBAC — VERIFIED

- Roles are exactly `owner | operator | viewer` (`auth.ROLES`).
  Identity comes only from the server-side session:
  `app/rbac.py` `resolve_identity()` builds `Identity` from
  `auth.resolve_session`; the audit actor is the session's username,
  never client input. Client role headers confer nothing (stripped
  by the middleware, never read by `rbac.py`).
- Guards: `require_write` (401 unauthenticated, 403 for viewer) and
  `require_owner` (401 / 403) in `app/rbac.py`, applied per route in
  `app/main.py` — e.g. user administration and approval decisions
  are owner-only (see §6); registry and module writes require
  write role.
- Deny by default: the middleware refuses every `/api` path without
  a live session (401) except the open set `/api/health`,
  `/api/auth/login`, `/api/auth/login/verify`, `/api/auth/bootstrap`
  (`OPEN_PATHS`, `app/main.py`).
- Last-owner protection: the last active owner can be neither
  demoted (`auth.set_role`) nor disabled (`auth.set_disabled`).
- An owner's password cannot be reset through the API
  (`auth.reset_password` raises for owners); owner recovery is the
  server shell only (`auth.cli_reset_password`).
- Test evidence: `tests/test_rbac.py` (9 tests, including
  header-spoofing conferring nothing and viewer escalation refusal)
  — passing.

## 6. Approvals — VERIFIED

- Consequential actions are exactly `promote_operational` and
  `deactivate_agent` (`CONSEQUENTIAL_ACTIONS`, `app/services.py`).
  They cannot be applied directly: `create_agent` /
  `create_model_config` refuse registration in an evidence- or
  approval-gated status, `set_agent_status` refuses an
  approval-gated status change, and `update_agent` directs
  deactivation to the approval route (`app/services.py`).
- Flow: `create_approval` records a pending request (any write role
  may request); `decide_approval` applies or rejects it. The decide
  route is owner-only (`require_owner`, `app/main.py`).
- Integrity rules in `decide_approval`: a non-pending approval
  cannot be decided again (409 — no double-decision); approving
  `promote_operational` without a linked passing evidence record is
  refused (422) and the record stays pending; a rejection applies
  nothing. Requests, decisions, and applications are all written to
  the audit log.
- Status gating behind approvals: `verified` / `operational`
  require passing evidence, and `operational` additionally requires
  the approval flow (`app/taxonomy.py`: `EVIDENCE_GATED`,
  `APPROVAL_GATED`; `services.has_passing_evidence`).
- Test evidence: `tests/test_approvals.py` (6 tests: direct routes
  refused, rejection applies nothing, operational approval blocked
  without evidence, applied with evidence + owner approval,
  double-decision 409, unknown action type refused) and
  `tests/test_taxonomy.py` (9 tests) — passing.

## 7. Audit integrity — VERIFIED (tamper-evidence; see limitation)

- `app/audit.py`: append-only log; each entry's `entry_hash` is the
  SHA-256 of the previous entry's hash plus the entry's canonical
  content, chained from a `GENESIS` hash. `append()` is the only
  write function in the module — there is no update or delete path
  in code.
- `verify_chain()` recomputes the entire chain and reports
  `{ok, entries, broken_at}`; exposed read-only at
  `GET /api/audit/verify` (`app/main.py`) and surfaced by the
  Governance Center (`app/governance.py`).
- Content discipline: entries record which fields changed, never
  secret values; auth events are audited with usernames and reason
  codes only (`auth._audit`, `app/auth.py`).
- Test evidence: `tests/test_audit.py` (3 tests, including tamper
  detection) — passing.
- **Limitation (stated, not hidden):** the chain is tamper-EVIDENT,
  not tamper-proof. Anyone with write access to the SQLite file
  could recompute the whole chain from scratch; nothing in the code
  anchors the chain externally (no signing key, no external
  witness). Detection of edits or deletions of individual
  historical rows — the control as designed — is verified.

## 8. Application module inventory — VERIFIED from code

The product's 11 functional modules (numbering as in the README and
the Phase 2 build report), mapped to the code that implements them:

| # | Module | Code | What it does (from code) |
|---|--------|------|--------------------------|
| 1 | Agent Registry | `app/services.py` | CRUD for agents, teams, capabilities, model configurations; evidence records; executive overview aggregation. |
| 2 | Agent Operations | `app/work.py` | Per-agent operations view (workload, assignments, executions, failure/recovery events), work assignment, pause/resume. |
| 3 | Orchestration Engine | `app/work.py` | Tasks with deterministic capability routing (capability → team restriction → lowest workload → lowest id); every routing decision recorded with reason and candidate table; multi-step executions with append-only history. |
| 4 | Communication Hub | `app/hub.py` | Agent-to-agent and human-to-agent message records with task context; records only — nothing is delivered externally. |
| 5 | Workflow Visualizer | `app/hub.py` | Workflow definitions as node/transition data, server-computed layered layout, per-execution step-state overlay. |
| 6 | Gateway Control | `app/gateway.py` | Registry of approved model/tool connections as configuration pointers (`config_ref`); `assert_no_secret()` refuses secret-shaped values in guarded text fields; health moves only on recorded checks. |
| 7 | Live Activity | `app/events.py` | Feed over the system's own event and audit records, with per-source counts (live / demo / system) never blended. |
| 8 | Performance Center | `app/economics.py` | Durations, success/failure rates, workload, utilization, throughput computed from recorded executions only; null rates when no executions exist. |
| 9 | Model Economics | `app/economics.py` | Usage records with estimated and actual costs as separate fields and totals, never summed; per-model price table starts empty ("rate not set"); budget alerts labeled by basis. |
| 10 | Governance Center | `app/governance.py` | One surface over approvals (counts by state), audit-chain verification, the gated consequential actions, and project scopes present in the registry. |
| 11 | Reliability Center | `app/reliability.py` | Recorded health checks (own database and audit chain; connections reported from recorded status only, never probed) and an incident lifecycle (open → acknowledged → resolved). |

Foundation / support files (not counted in the 11):
`app/main.py` (FastAPI application, auth middleware, all HTTP
routes), `app/db.py` (SQLite storage and schema), `app/auth.py`
(accounts, sessions, throttling), `app/totp.py` (two-step login),
`app/rbac.py` (session-derived identity and guards),
`app/audit.py` (hash-chained audit log), `app/taxonomy.py`
(connection-status taxonomy and gating sets), `app/auth_cli.py`
(server-shell owner bootstrap and break-glass), `app/seed.py`
(explicit demo seed, all rows labeled `demo`).

**Observation (documentation staleness inside code output):**
`governance_summary()` in `app/governance.py` returns a `role_model`
string describing the role model as a "(header-based foundation
mechanism — see README for its stated limits)". The header mechanism
was replaced by the session login verified in §5; the string is
stale descriptive text in an API response. It is not a security
defect — enforcement is session-based — but the string should be
corrected in a future code change (out of scope for this
documentation-only branch).

## 9. What was NOT verified

- **No deployed instance was tested.** All evidence is source code
  and the local test suite at main `1e46d09`. Nothing here verifies
  any running service, hosting configuration, TLS, or the
  PythonAnywhere deployment.
- **No penetration test or independent review was performed.** This
  report is a code-and-test verification by the build team, not an
  independent audit.
- **The Android client** still speaks the retired header stand-in
  per the README and the auth build report; its migration to token
  login was not verified (no client code in this repository).
- **PT/HT catalog strings** are authored, not native-speaker
  reviewed (README, unchanged by this report) — a language-quality
  gap, not a security finding.

## Summary

| Area | Status |
|------|--------|
| Authentication | VERIFIED |
| Sessions | VERIFIED |
| CSRF protection | VERIFIED |
| TOTP (two-step login) | VERIFIED |
| RBAC | VERIFIED |
| Approvals | VERIFIED |
| Audit integrity | VERIFIED (tamper-evidence; limitation in §7) |
| 11-module inventory | VERIFIED from code |
| Deployed-instance security | NOT VERIFIED (out of scope, §9) |

**KNOWLEDGE IS THE LEGACY.**
