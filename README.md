# M&Q AI Command Dashboard

**Product status: FULL LOCAL BUILD (all 11 modules) — local only.
IMPLEMENTED + TESTED locally · NOT INDEPENDENTLY VERIFIED · NOT MERGED ·
NOT DEPLOYED.**

An independent product: an intelligence operations platform to coordinate,
supervise, monitor, and manage a growing ecosystem of AI agents. It is **not**
a replacement for M&Q CRM, is not built in the M&Q AI CONNECT repository, and
never reads CRM data. Its future home is the independent repository
`mrjosueayalaperez-dev/mq-ai-command-dashboard`; this local project mirrors
that layout because connector write access to GitHub is currently blocked
(HTTP 403) — nothing here has been pushed anywhere.

Governing principles: **Knowledge Is The Legacy.** · **Technology + People =
Access.** · **Implemented ≠ Tested ≠ Deployed.** · Independent products,
controlled interoperability.

## All 11 modules (Phase 2 extension, Nov 18, 2026)

Every module works over records this system itself keeps. **No external
agent is connected to this system** — assigning work, recording a
message, or registering a connection creates a local record; nothing
is invoked, delivered, or probed outside this service. Demo/simulated
data stays labeled `demo` everywhere and is never blended with live
records in any count or average.

1. **Agent Registry** — below (foundation).
2. **Agent Operations** — per-agent operations view (workload, open
   assignments, recent executions, failure/recovery events), work
   assignment, pause/resume. Pause is a lifecycle change; disable
   (deactivation) stays behind the owner approval flow.
3. **Orchestration Engine** — tasks with deterministic capability
   routing (required capability → team restriction → lowest workload
   → lowest id), every routing decision recorded with its reason and
   the candidate table; multi-step executions with per-step states
   and an append-only execution history.
4. **Communication Hub** — agent-to-agent and human-to-agent message
   records with task context, participants, and timestamps preserved;
   supervision filters by task, agent, and source; every record is in
   the audit trail.
5. **Workflow Visualizer** — workflow definitions as node/transition
   data, a server-computed layered layout, and an SVG rendering in
   the Operations view; per-execution step states are overlaid with
   failed and blocked steps visually distinct.
6. **Gateway Control** — registry of approved model/tool connections
   with recorded health. **No credentials are stored here, ever:**
   configuration is a pointer (`config_ref`) plus written access
   boundaries, and a code-enforced guard refuses secret-shaped values
   (API keys, tokens, private keys) in every text field. Health moves
   only on recorded health checks; `verified` requires a passing
   record, and a pass restores a down connection only to `connected`.
7. **Live Activity** — a feed over the system's own event and audit
   records. Every entry carries its source label and timestamp;
   counts are reported per source (live / demo / system) and never
   blended into a single total.
8. **Performance Center** — durations, success/failure rates,
   workload, utilization, and throughput computed from recorded
   executions only. With no executions recorded, rates are null and
   the module says so — no figure is invented.
9. **Model Economics** — usage records with **estimated and actual
   costs as separate fields and separate totals, never summed**. The
   per-model price table starts empty: with no rate set, estimates
   stay null and are reported as "rate not set". Budgets alert from
   recorded spend, labeled by basis (actual / estimated / mixed).
10. **Governance Center** — the foundation's approvals, audit chain,
    and RBAC presented as one governance surface: approval counts by
    state, audit verification, the gated consequential actions, and
    the project scopes present in the registry.
11. **Reliability Center** — recorded health checks (the service
    checks its own database and audit chain, and reports each
    connection's *recorded* status — a connection with no health
    record is reported degraded with that reason, never probed or
    presumed fine) and an incident lifecycle
    (open → acknowledged → resolved).

The web UI carries all three approved interface concepts: the
**Executive Command Center** (primary), the **Operations Control
Room** (activity, executions, workflow visualizer, performance,
economics, gateway, reliability), and the **Intelligence Network**
(agent/team graph with communication edges). The four-language shell
now covers the new chrome too, including the sign-in screens (64 keys
per catalog, parity-tested; PT/HT authored, native-speaker review
still outstanding).

## What the foundation build contains

- **Agent Registry** — registry-driven CRUD for agents, teams, capabilities,
  and model configurations. No hard-coded roster and no agent cap: adding an
  agent is a data operation, never a code change.
- **Status taxonomy, enforced in code** — the only legal connection statuses
  are `demo_simulated · configured · connected · verified · operational ·
  degraded · disconnected · failed`. Promotion to **verified** or
  **operational** requires a linked passing evidence record; an attempt
  without one is refused. Promotion to **operational** additionally requires
  the governance approval flow.
- **Governance approvals** — consequential actions (promotion to operational,
  agent deactivation) are requested as approval records and take effect only
  when an **owner** approves them. Requests, decisions, and applications are
  all audit-logged.
- **Audit log** — append-only and hash-chained (each entry carries the
  previous entry's hash), with a verification endpoint. Registry writes,
  evidence records, approvals, and status changes are all recorded, with the
  names of the fields changed — never secret values (this system stores no
  secrets).
- **Executive Overview (Concept A)** — a read-only page in the approved
  black + graphite + gold + ivory identity showing agent counts by status,
  teams, pending approvals, and recent audit events, driven by an explicit
  **demo seed**. Every seeded agent is `demo_simulated`, and whenever demo
  data is present the page shows a visible banner: *"DEMO DATA — simulated
  agents, not connected systems."* Nothing in this build is, or can be
  displayed as, a connected or operational real system.
- **Four-language shell** — the overview chrome (title, navigation, banner,
  headings, status labels) renders in English, Spanish, Portuguese, and
  Haitian Creole from catalogs in `web/i18n/`, with a language switcher and a
  test enforcing identical key sets across all four catalogs. The Portuguese
  and Haitian Creole strings are **authored, not yet native-speaker
  reviewed** — no production-quality language claim is made until that
  review happens.

## What is NOT in this build

There are **no real agent integrations** — no external agent, model,
or tool is connected, contacted, or probed by this system; every
module operates on local records, and the taxonomy's evidence rule
means nothing can be labeled verified/operational without a passing
evidence record on file. Also not present: any hosting or deployment,
and any GitHub write (the repository bootstrap remains blocked on
connector Contents access — see the backlog's Dependency #1). The
Android app still speaks the retired header stand-in; its move to the
login token below is a separate, documented step (see
BUILD-REPORT-AUTH-2026-11-18.md).

## Access control — real login (replaced the header stand-in)

Access is a real login, mirroring the M&Q CRM's merged auth design:

- **Accounts** with **argon2id** password hashes (never a stored or
  logged password) and roles `owner | operator | viewer`.
- **Server-side sessions**: 30-minute idle / 12-hour absolute expiry,
  stored as token hashes. Web UI ⇒ HttpOnly cookie with per-session
  **CSRF** token + Origin check on writes; Android / API ⇒
  `Authorization: Bearer <token>` from a `mode: "token"` login.
- **Deny by default**: every `/api` path except `/api/health` and the
  login flow itself refuses unauthenticated calls (401). Client
  `X-Role` / `X-Actor` headers are stripped and confer nothing; the
  audit actor is the session's username.
- **Throttling**: failed logins lock per account and per client
  address with exponential backoff; failure messages are generic.
- **First owner — server shell only** (no default password, no HTTP
  setup route):

  ```bash
  python -m app.auth_cli create-owner --username <name>
  ```

  After that, the owner creates operator/viewer accounts in the app
  (`POST /api/users`). Self-service password change:
  `POST /api/auth/password`. An **owner's** password can never be
  reset through the API — recovery is the server shell only:
  `python -m app.auth_cli reset-password --username <name>`.
- **Two-step login (TOTP)** for the owner: RFC 6238 authenticator-app
  codes with anti-replay (each time step usable once), 10 one-time
  recovery codes, and a 5-minute / 5-attempt login challenge — the
  password alone never yields a session for a two-step account.
  Enroll via `POST /api/auth/totp/enroll` → `/confirm`; lost-phone
  break-glass is `python -m app.auth_cli reset-totp --username <name>
  --yes`. `/api/auth/me` and the server log warn while an owner has
  no two-step enrolled. The TOTP secret is stored AES-256-GCM
  encrypted; the key comes from `MQ_CMD_TOTP_KEY` or the `totp.key`
  file beside the database (back it up with the database).
- The web UI opens on a real sign-in page (four languages); the demo
  seed sits behind the login like everything else.

Environment: `MQ_CMD_DB` (database path), `MQ_CMD_TOTP_KEY` (two-step
key), `MQ_CMD_SESSION_IDLE_MINUTES` / `MQ_CMD_SESSION_MAX_HOURS`
(session bounds, clamped). The service is intended to run on
`127.0.0.1` only until a deployment is separately authorized.

## Running it

```bash
python3 -m venv ~/.venvs/cmd-dash
~/.venvs/cmd-dash/bin/pip install -r requirements.txt
./run.sh            # serves http://127.0.0.1:8000
```

Create the first owner (one time, server shell), then sign in:

```bash
~/.venvs/cmd-dash/bin/python -m app.auth_cli create-owner --username <name>
```

Seed or clear the demo data (signed in as the owner; token shown):

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<name>","password":"<password>","mode":"token"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["token"])')
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/admin/seed-demo
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/api/admin/wipe-demo
# or offline:  ~/.venvs/cmd-dash/bin/python -m app.seed
```

The database is a single SQLite file at `data/dashboard.db` (override with
the `MQ_CMD_DB` environment variable).

## Tests

```bash
~/.venvs/cmd-dash/bin/python -m pytest tests/ -q
```

The suite (116 tests) covers registry CRUD, taxonomy enforcement
(including evidence-gated promotion refusal), RBAC refusals over real
sessions, the approval flow, audit-chain verification and tamper
detection, four-language catalog parity, demo-data labeling,
capability routing correctness, execution/step lifecycles,
message-context integrity, workflow graph state data, the gateway
secret guard, activity-feed source separation, performance arithmetic
and honest empty states, estimated-vs-actual cost separation and
budget alerts, and the incident lifecycle — plus the real login:
token/cookie sessions, idle + absolute expiry, lockout, CSRF refusal,
header-spoofing refusal, the owner CLI bootstrap and break-glass,
password change with session revocation, user-admin rules, and the
full TOTP flow (wrong code, replayed code, recovery code, fresh
code).

## Layout

```
app/    FastAPI service — services.py (registry/approvals/overview),
        work.py (tasks, routing, executions, agent operations),
        hub.py (communications, workflows, network graph),
        gateway.py (connections + secret guard), events.py (activity
        feed), economics.py (usage, prices, budgets, performance),
        reliability.py (health checks, incidents),
        governance.py (governance surface), audit.py, taxonomy.py,
        rbac.py (identity from the session), auth.py (accounts,
        sessions, throttling), totp.py (two-step login),
        auth_cli.py (owner bootstrap / break-glass), seed.py
web/    static front end (no build step): three views (Command
        Center / Operations Control Room / Intelligence Network)
        + i18n catalogs (EN/ES/PT/HT)
tests/  pytest suite, synthetic data only
```
