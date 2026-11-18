# M&Q AI Command Dashboard — Phase 2 Build Report (All 11 Modules)

**Date:** November 18, 2026
**Build location:** `~/workspace/mq-ai-command-dashboard/` (local project mirroring the future layout of `mrjosueayalaperez-dev/mq-ai-command-dashboard`; built locally because GitHub connector Contents writes are currently HTTP 403-blocked — nothing was pushed anywhere)
**Authorization:** founder order, November 18, 2026 — "Command Dashboard — all 11 modules" (full version approved)
**Scope:** Backlog Phase 2 (items 2.1–2.9) plus completion of the Governance Center module surface (1.7 machinery, presented as a module), extending the Phase 1 foundation (36 tests) without rebuilding it.

## Honest status block

**IMPLEMENTED + TESTED locally · NOT INDEPENDENTLY VERIFIED · NOT MERGED (GitHub write access still blocked) · NOT DEPLOYED**

No external agent, model, or tool is connected to this system. Every module operates on records the system itself keeps in its local SQLite database. Assigning work records an assignment; recording a message records a message; registering a connection records a configuration pointer. Nothing is invoked, delivered, or probed outside the service, and demo/simulated data is labeled `demo` in every row, feed entry, and aggregate.

## Per-module status

| # | Module | Implemented | Tested | Notes |
|---|---|---|---|---|
| 1 | Agent Registry | ✅ (Phase 1) | ✅ | Unchanged; extended by demo seed only. |
| 2 | Agent Operations | ✅ | ✅ | `GET /api/operations/agents/{id}` inspection (workload, open assignments, recent executions, failure/recovery events); `POST /api/operations/assignments`; pause/resume endpoints (lifecycle); failure/recovery event records (`POST /api/agents/{id}/events`). Failed executions automatically record an `agent_failure` event. Deactivation remains approval-gated from Phase 1. |
| 3 | Orchestration Engine | ✅ | ✅ | Tasks (`queued/routed/running/blocked/completed/failed/cancelled`), deterministic capability routing: required capability → assigned-team restriction → lowest workload (open tasks + running executions) → lowest agent id. Every attempt writes a `routing_decisions` row with outcome, human-readable reason, and the full candidate table (eligibility, exclusion reasons, workloads). Unrouted tasks stay `queued` with the reason recorded. Multi-step executions with per-step states; history is append-only (finishing is one-way, 409 on re-finish). |
| 4 | Communication Hub | ✅ | ✅ | Message records with task context, from/to agents or a named human sender, channel, body, context pointer, and timestamps — all preserved verbatim on read. Supervision filters by task, agent (either direction), and source. Every record is audit-logged and appears as an activity event. Recording a message delivers nothing anywhere. |
| 5 | Workflow Visualizer | ✅ | ✅ | Workflow definitions stored as node/transition JSON, validated (unique keys, transitions must reference real nodes, non-empty). `GET /api/workflows/{id}/graph` returns a deterministic layered layout (x by longest-path depth, y by in-layer order) and, with `execution_id`, each node's recorded step status — `failed` and `blocked` are distinct values end to end. The Operations view renders this as SVG with failed (red) and blocked (dashed gold) styling. |
| 6 | Gateway Control | ✅ | ✅ | Connection registry (kind, provider, `config_ref` pointer, config notes, written access boundary, taxonomy health status). **Secret guard enforced in code:** create and PATCH scan every text field against credential patterns (OpenAI/Anthropic/GitHub/AWS/Slack/Google key shapes, `Bearer` tokens, `key=`/`secret=`/`token=`/`password=` assignments, PEM private-key markers) and refuse with 422; pointers and prose pass. Health moves only on recorded health checks: fail → `failed`, pass restores a down connection only to `connected`; `verified` requires a passing health/evidence record; `operational` is refused (the approval flow covers agents in this build — stated in the refusal text). Summary endpoint states the no-secrets policy on the record. |
| 7 | Live Activity | ✅ | ✅ | `GET /api/activity` merges `events` rows and audit-log rows, newest first. Every entry carries `data_source` (`live`/`demo`/`system` for audit) and a `source_label`. Counts are returned per source only — there is deliberately no blended total field. Source filter supported. |
| 8 | Performance Center | ✅ | ✅ | Computed from execution records: counts by state, success/failure rates over decided executions, average duration, total busy time, throughput by day, and per-agent workload/utilization (busy-time share). Empty state is honest: zero executions → rates `null` plus the explicit note "no executions recorded yet". Demo and live blocks are computed and returned separately; an `agent_id` scope is supported. |
| 9 | Model Economics | ✅ | ✅ | Usage records (tokens in/out, agent, task, execution, model, project) with `estimated_cost_minor` and `actual_cost_minor` as separate columns, separate summary totals, never summed. The editable price table (`model_prices`, per-1M rates in minor units) starts empty: unrated records keep a null estimate, are counted as `unrated_records`, and carry the note "rate not set — no estimate computed". When a full rate is set, estimates are computed (half-up rounding) at record time. Budgets (agent/task/project/global scopes) compute spend from recorded actuals first, estimates second, labeled by basis (`actual`/`estimated`/`mixed`/`none`), with `ok`/`warning`/`exceeded` status against limit and alert threshold. |
| 10 | Governance Center | ✅ completed as a module surface | ✅ | `GET /api/governance`: approvals by status, pending count, the gated consequential actions, the role model with its stated limits, live audit-chain verification, and the project scopes present in the registry with the isolation rule stated. Machinery (approvals, hash-chained audit, RBAC) is the Phase 1 build, unchanged. |
| 11 | Reliability Center | ✅ | ✅ | `POST /api/reliability/run-checks` performs the checks the service can truthfully run — its own database query and a full audit-chain recomputation — and reports each registered connection's *recorded* status: a connection with no health record is stored as `degraded` with the detail "no health record has ever been recorded for this connection; it has not been tested". Incidents run open → acknowledged → resolved with actor/timestamp/note on each transition (409 on invalid re-transitions) and appear in the activity feed and the reliability summary (service status from the latest database self-check, check pass rate, incident counts). |

## Interface concepts

- **Executive Command Center (Concept A)** remains the primary, default view — unchanged from Phase 1.
- **Operations Control Room (Concept B)** — new view: Live Activity feed with per-source counts, executions list, Workflow Visualizer (SVG from the graph endpoint, overlaying the latest execution's step states), Performance and Model Economics cards (per source, estimated vs. actual labeled), Gateway connections with taxonomy status labels, and Reliability status + incidents.
- **Intelligence Network (Concept C)** — new view: SVG graph from `GET /api/network-graph` — teams and agents as nodes (demo agents dashed red), membership edges, and recorded communication edges, with an edge list (sender → receiver · count).
- **Four languages:** catalogs extended from 33 to **50 keys each** (EN/ES/PT/HT) covering the new chrome; the existing parity test enforces identical key sets across all four. PT/HT strings are authored; **native-speaker review remains outstanding** and no production-quality language claim is made.

## Demo seed (extended)

The explicit demo seed now also creates, all with `data_source='demo'`: a 3-node demo workflow; two demo tasks; two demo executions (one completed at 120000 ms with all steps completed, one failed at 60000 ms with steps completed/failed/blocked); three demo communications (two agent-to-agent, one human supervision note); two demo events; a demo gateway connection (`demo_simulated`); two demo usage records (one unrated, one with an operator-recorded estimate of 42 and no actual); and one resolved demo incident. `wipe_demo` removes all Phase 2 demo rows in FK-safe order (verified by test, including a re-seed cycle).

## Tests

Command: `~/.venvs/cmd-dash/bin/python -m pytest tests/ -q` (run from the project root)

**Result: 82 passed, 0 failed** (2.3s; one unrelated Starlette deprecation warning about the TestClient). All 36 Phase 1 foundation tests pass unchanged.

Phase 2 coverage (46 new tests):

- `test_orchestration.py` (8) — routing picks the capable agent with the lowest workload; a capable agent is chosen over an incapable one regardless of workload; no eligible agent → `unrouted`, task stays queued, reason + decision recorded; team restriction beats workload; manual assignment is routed at creation; execution lifecycle with exact duration (2500 ms) and task-status coupling; re-finish refused (409); append-only history (failed attempt preserved after a successful retry); step transitions with timestamps and invalid-status refusal.
- `test_operations.py` (6) — operations view aggregates workload/assignments/executions; failed execution counts as a failure event; manual failure/recovery events counted; paused agent excluded from routing, resume restores; assignment endpoint requires a target (422); viewer refused on pause/events/tasks (403).
- `test_communications.py` (3) — context integrity: task id, both participants (with slugs), channel, context ref, and the exact `occurred_at` survive the round trip; supervision filter by task; audit trail contains the record; human sender (`from_actor`) supported; empty message, unknown task, and unknown agent refused; viewer refused.
- `test_workflows.py` (4) — graph layout data (depth ordering, edges, canvas size); definition validation (bad transition, duplicate key, empty nodes all 422); duplicate slug 409; graph with an execution overlay returns exactly {completed, failed, blocked} as three distinct node states.
- `test_gateway.py` (7) — secret guard rejects eight credential shapes across five fields (incl. PATCH) with a "credential" explanation; pointers/prose accepted (incl. "password manager" prose and env-name references); health fail → `failed`, pass → `connected` (never straight to verified); `verified` refused without a passing record and granted with one; `operational` refused; direct registration as verified refused; summary counts and the stated secrets policy; viewer refused.
- `test_activity.py` (4) — feed entries all carry source + label + timestamp; counts keyed exactly {live, demo, system} with no blended total; source filters return only that source (system = audit entries); seeded Phase 2 rows all carry `data_source='demo'` (tasks, executions, communications, gateway connection at `demo_simulated`); wipe removes all Phase 2 demo rows and re-seed restores them.
- `test_performance.py` (3) — honest empty state (null rates + note); exact arithmetic on three timed executions (success rate 2/3, avg duration 4000 ms, busy 12000 ms, utilization 100% in scope, throughput by day); demo block exact (2 executions, 0.5 success, 90000 ms avg) with no leakage into the live average.
- `test_economics.py` (5) — rate-not-set → null estimate, `rate_set: false`, unrated counted; estimate computed exactly from a set rate (2000 minor on 500k/250k tokens); actual (777) and estimated (100) reported as separate totals; budget warning at 850/1000 (basis `actual`) and exceeded at 1050 with negative remaining; validation refusals (negative rate, zero limit, unknown scope).
- `test_reliability.py` (4) — run-checks stores passing database and audit-chain rows; summary service status `pass`; an unchecked connection is reported `degraded` with the "no health record" reason; incident lifecycle open → acknowledged → resolved with 409s on re-acknowledge/re-resolve, resolution note stored, incident visible in summary and activity feed; viewer refused on run-checks and incident creation.
- `test_governance.py` (2) — governance summary shape (approval buckets, gated actions, roles, audit block, scopes, isolation statement); network graph built from registry + communications (nodes carry status and source, membership edge present, communication edge count = 2).

Two assertions were deliberately shaped by a Phase 1 artifact: the foundation's tamper test corrupts the shared test database's audit chain on purpose, so Phase 2 tests assert the audit-chain check is *recorded with its true result* rather than asserting `pass` in full-suite order (chain-verifies-clean remains covered by `test_audit.py`, which runs before the tamper test). The reliability summary's `service_status` is defined as the latest recorded **database** self-check; audit integrity is reported by the Governance Center.

## Live smoke (uvicorn on 127.0.0.1:8178, fresh database, then stopped)

A scripted smoke exercised every module against the running service — **28 checks, all passed**:

- Health reports stage `phase2-full`; demo seed returns agents 4, tasks 2, executions 2, communications 3.
- The served page contains all three view containers plus the workflow and network graph mounts; `/app.js`, `/style.css`, and `/i18n/ht.json` (with the new keys) serve 200.
- Overview over demo data reports `demo_only: true`; operations view for the demo builder returns its assignments.
- A live capability was created, an agent registered with it, a task routed to exactly that agent, an execution started and finished with a computed duration; a communication recorded against the task reads back by task filter.
- The demo workflow graph returns 3 nodes / 2 edges, and with the failed demo execution overlaid returns step states {plan: completed, build: failed, review: blocked}; the network graph returns the seeded agents plus communication edges.
- Gateway: the demo connection is listed; posting a connection whose `config_ref` is an `sk-…` key is refused 422 by the secret guard; the summary states the no-credentials policy.
- Activity feed returns the three source buckets; performance demo block is exactly 2 executions at 0.5 success; economics summary keeps estimated/actual as separate fields and all prices report `rate_set: false` by default; governance returns gated actions and a non-empty audit; reliability run-checks pass and the summary reports service status `pass` with the demo incident present; a viewer write to the new task endpoint is refused 403.
- Server stopped after the smoke; nothing is left running.

## Design choices where the documents left room

- **Routing determinism:** ties and ordering are fully deterministic (workload, then lowest agent id) and every candidate's eligibility, exclusion reason, and workload is stored on the decision row, so any routing outcome can be explained from records alone.
- **Executions update their task's status** (running on start; completed/failed/blocked/cancelled on finish) — the conservative reading of "monitor execution": the task queue always reflects the latest recorded attempt, while the attempts themselves are never overwritten.
- **Connection `operational`:** refused in this build because the governance approval flow's targets are agents; promoting a connection to `verified` on passing health evidence is the highest connection status available, and the refusal says so. Extending approvals to connections is a founder decision, not an assumption.
- **Budget spend basis:** actual costs are preferred per record, estimates fill gaps, and the basis is labeled (`actual`/`estimated`/`mixed`/`none`) rather than blended silently.
- **Audit rows in the activity feed** are labeled source `system`, not `live`: the audit trail is a governance record, not telemetry, and the feed's honesty rule forbids dressing it as live operational data.
- **Reliability self-check scope:** the module checks only what it can reach (database, audit chain, recorded connection status). It does not probe external systems, and says so in the stored check detail.

## What is NOT built

Backlog item 2.10 (a first real integration) is **not** started — it requires a founder decision on which system, a written access boundary, and evidence; no agent or system is connected. Session login with hashed passwords remains future work (the header RBAC stand-in and its limits are unchanged and documented). No hosting, no deployment, no GitHub write (Phase 0 remains blocked on the connector Contents 403, Dependency #1). No CRM project, CONNECT repository, or other workspace project was touched. No external network calls are made by the app. Total cost: $0.
