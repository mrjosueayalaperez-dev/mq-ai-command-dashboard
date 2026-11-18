# M&Q AI Command Dashboard — Foundation Build Report

**Date:** November 18, 2026
**Build location:** `~/workspace/mq-ai-command-dashboard/` (local project mirroring the future layout of `mrjosueayalaperez-dev/mq-ai-command-dashboard`; built locally because GitHub connector Contents writes are currently HTTP 403-blocked — nothing was pushed anywhere)
**Authorization:** founder approval, November 18, 2026 ("Approved build it")
**Scope:** Phase 1 foundation scope from `MQ-AI-COMMAND-DASHBOARD-BACKLOG-2026-11-18.md` (items 1.1–1.10), per the approved specification and architecture documents.

## Honest status block

**IMPLEMENTED + TESTED locally · NOT INDEPENDENTLY VERIFIED · NOT MERGED (no repository write access yet) · NOT DEPLOYED**

## What was implemented

1. **Project scaffolding (1.1)** — FastAPI + SQLite service (`app/`), static front end with no build step (`web/`), `README.md` stating the product status honestly, `requirements.txt` (pinned: fastapi 0.164.0, uvicorn 0.54.0, httpx 0.28.1, pytest 9.2.0), `.gitignore`, `run.sh` (binds 127.0.0.1 only). Python 3.12.3, venv at `~/.venvs/cmd-dash`.
2. **Status taxonomy enforced in code (1.2)** — `app/taxonomy.py`: the eight states (`demo_simulated, configured, connected, verified, operational, degraded, disconnected, failed`) are the only legal connection-status values for agents and model configs. Promotion to `verified`/`operational` requires a linked **passing evidence record** (`evidence_records` table; `POST /api/evidence`); refusal is a 422 with the reason. Registration directly in a gated status is also refused, and PATCH cannot change `connection_status` at all.
3. **Registry schema + CRUD (1.3, 1.4)** — agents, teams, team memberships, capabilities, model configs, and agent↔model assignments, all registry-driven data (no hard-coded roster, no cap). Agents carry identity, role, description, owner project, lifecycle status, connection status, and a mandatory `data_source` (`live` | `demo`). Deactivation is soft only; hard delete is refused by design.
4. **RBAC (1.5)** — roles `owner | operator | viewer` via `X-Role` / `X-Actor` headers; missing or unknown role ⇒ viewer. Write endpoints refuse viewers (403); approval decisions and demo seed/wipe require owner (403 otherwise). **Documented limit (in code and README): this header mechanism is a local-foundation stand-in, not production auth** — the architecture's session login with argon2id-hashed passwords must replace it before any deployment.
5. **Audit log (1.6)** — append-only, hash-chained (`entry_hash` = SHA-256 of previous hash + canonical entry content), with `GET /api/audit/verify`. Every registry write, evidence record, approval request/decision, status change, and demo seed/wipe is recorded with actor, action, target, and the **names of fields changed** — never secret values (the system stores no secrets).
6. **Governance / approval model (1.7)** — consequential actions (`promote_operational`, `deactivate_agent`) exist only as approval records: requested (pending) → decided by an owner → applied atomically on approval, refused with 422 at decision time if promotion lacks passing evidence. Direct routes (status endpoint, deactivate endpoint, DELETE, PATCH lifecycle) all refuse with an explanation pointing at the approval flow.
7. **Executive Overview, Concept A (1.8)** — read-only page in black + graphite + gold + ivory: agent counts by status, team list with member counts, pending approvals, recent audit events, plus `as_of` freshness and a data-source label on the API payload. Driven by an explicit demo seed (4 simulated agents, 2 teams, 4 capabilities, 1 demo model config — every row `data_source='demo'`, every agent `demo_simulated`). Whenever demo data is present, the page shows the banner **"DEMO DATA — simulated agents, not connected systems"** in the selected language. Demo and live counts are reported in separate fields and never summed in any displayed figure. The seed is idempotent and wipeable in one call.
8. **Four-language shell (1.9)** — catalogs at `web/i18n/{en,es,pt,ht}.json` (33 keys each) covering title, navigation, banner, section headings, totals, and all eight status labels, with a working language switcher. PT/HT strings are authored; **native-speaker review is outstanding** and is required before any production-quality language claim (stated in the README).

## Tests

Command: `~/.venvs/cmd-dash/bin/python -m pytest tests/ -q` (run from the project root)

**Result: 36 passed, 0 failed** (0.72s; one unrelated Starlette deprecation warning about the TestClient).

Coverage by file:

- `test_registry.py` (7) — agent create/get/list/update, duplicate-slug refusal, team CRUD + membership, capability assignment (+ unknown capability refusal), model config + assignment, 404.
- `test_taxonomy.py` (9) — exactly-eight-states, invalid status refused, free statuses settable, **verified refused without evidence**, failing evidence does not unlock, passing evidence unlocks verified, **operational refused on the direct endpoint even with evidence**, no direct registration as verified/operational, PATCH cannot change status.
- `test_rbac.py` (6) — viewer write refusals (create/patch/status), missing header = viewer, viewer reads allowed, operator cannot decide approvals, operator/viewer cannot seed or wipe demo.
- `test_approvals.py` (6) — deactivation only via approval flow (all direct routes refused), rejection applies nothing, operational approval blocked without evidence (record stays pending), operational applied with evidence + owner approval, double-decision refused (409), unknown action type refused.
- `test_audit.py` (3) — writes audited with changed-field names, chain verifies, **tamper detection** (a row rewritten directly in SQLite makes verification fail at that entry).
- `test_i18n.py` (3) — identical key sets across EN/ES/PT/HT, all values non-empty, every catalog labels the demo banner and all eight statuses.
- `test_overview.py` (2) — seeded agents are all `demo` + `demo_simulated`; overview marks demo data (`demo_data_present`, separate demo/live counts that sum to totals, zero demo verified/operational); overview payload shape and full status key set.

## Live smoke (uvicorn on 127.0.0.1:8177, fresh database, then stopped)

- `GET /api/health` → `{"ok": true, "product": "M&Q AI Command Dashboard", "stage": "foundation", ...}`
- `POST /api/admin/seed-demo` (owner) → `{"agents": 4, "teams": 2, "capabilities": 4, "data_source": "demo"}`
- `GET /api/overview` → `demo_only: true`, `data_source: "demo"`, 4 agents all `demo_simulated`, demo/live counts separate.
- `POST /api/agents` as owner → 201, agent created with `connection_status: "configured"`.
- `POST /api/agents` as viewer → **403** (refused, as designed).
- `GET /api/audit/verify` → `{"ok": true, "entries": 2, "broken_at": null}`
- `GET /` → 200 (overview page); `GET /i18n/ht.json` → 200.
- Server stopped after the smoke; nothing is left running.

## Design choices where the documents left room

- **Status value naming:** code uses `demo_simulated` (the build task's spelling) for the taxonomy's first state; the architecture document's shorter `demo` spelling refers to the same state. The `data_source` field keeps the architecture's `live | demo` values.
- **Approvals:** a request and its decision are separate recorded steps even when the same owner performs both — the conservative reading of the governance requirement.
- **Evidence at decision time:** an operational promotion approval cannot be approved while no passing evidence exists; the record stays pending rather than being auto-rejected, so evidence can be recorded and the decision retried.

## What is NOT built (Phase 2 — not started)

Agent Operations · Orchestration Engine · Communication Hub · Workflow Visualizer · Gateway Control · Live Activity · Performance Center · Model Economics · Reliability Center · tasks/executions/workflows data and APIs · session login with hashed passwords (foundation uses the documented header stand-in) · any real agent integration · any hosting or deployment · any GitHub write (blocked; Phase 0 of the backlog remains open on Dependency #1).

No CRM project, CONNECT repository, or other workspace project was touched. No external network calls are made by the app. Total cost: $0.
