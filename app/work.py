"""Orchestration Engine + Agent Operations (modules 2 and 3).

Everything here operates on records this system itself keeps: tasks,
routing decisions, executions, and step states in the local database.
Routing is deterministic over registry data only — required capability,
team restriction, then lowest current workload, ties broken by lowest
agent id. No external agent is contacted; assigning work records an
assignment, it does not invoke anything outside this service.
"""

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException

from . import audit, events
from .db import utcnow
from .services import get_agent, get_team

TASK_STATUSES = ("queued", "routed", "running", "blocked", "completed",
                 "failed", "cancelled")
OPEN_TASK_STATUSES = ("routed", "running", "blocked")
EXECUTION_STATUSES = ("running", "completed", "failed", "blocked",
                      "cancelled")
STEP_STATUSES = ("pending", "running", "completed", "failed", "blocked")
PRIORITIES = ("low", "normal", "high", "critical")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _check_data_source(body: dict) -> str:
    data_source = body.get("data_source", "live")
    if data_source not in ("live", "demo"):
        raise HTTPException(422, "data_source must be 'live' or 'demo'")
    return data_source


# ------------------------------------------------------------------ tasks

def _task_detail(conn, task_id: int) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?",
                       (task_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "task not found")
    task = dict(row)
    cap = None
    if task["required_capability_id"]:
        crow = conn.execute(
            "SELECT id, slug, display_name FROM capabilities"
            " WHERE id = ?", (task["required_capability_id"],)).fetchone()
        cap = dict(crow) if crow else None
    task["required_capability"] = cap
    decisions = conn.execute(
        "SELECT * FROM routing_decisions WHERE task_id = ?"
        " ORDER BY id DESC", (task_id,)).fetchall()
    task["routing_decisions"] = [dict(d) for d in decisions]
    execs = conn.execute(
        "SELECT id, agent_id, workflow_id, status, started_at, ended_at,"
        " duration_ms, data_source FROM executions WHERE task_id = ?"
        " ORDER BY id DESC", (task_id,)).fetchall()
    task["executions"] = [dict(e) for e in execs]
    return task


def get_task(conn, task_id: int) -> dict:
    return _task_detail(conn, task_id)


def list_tasks(conn, status: str | None = None,
               agent_id: int | None = None) -> list[dict]:
    query = "SELECT id FROM tasks"
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if agent_id:
        clauses.append("assigned_agent_id = ?")
        params.append(agent_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    return [_task_detail(conn, r["id"])
            for r in conn.execute(query, params)]


def _resolve_capability(conn, body: dict):
    """Accept required_capability_id or required_capability (slug)."""
    cap_id = body.get("required_capability_id")
    if body.get("required_capability"):
        row = conn.execute(
            "SELECT id FROM capabilities WHERE slug = ?",
            (body["required_capability"],)).fetchone()
        if row is None:
            raise HTTPException(
                422, f"unknown capability"
                     f" '{body['required_capability']}'")
        cap_id = row["id"]
    elif cap_id is not None:
        row = conn.execute("SELECT id FROM capabilities WHERE id = ?",
                           (cap_id,)).fetchone()
        if row is None:
            raise HTTPException(422, "unknown required_capability_id")
    return cap_id


def create_task(conn, identity, body: dict) -> dict:
    if not body.get("title"):
        raise HTTPException(422, "title is required")
    priority = body.get("priority", "normal")
    if priority not in PRIORITIES:
        raise HTTPException(422, f"priority must be one of {PRIORITIES}")
    data_source = _check_data_source(body)
    cap_id = _resolve_capability(conn, body)
    agent_id = body.get("assigned_agent_id")
    team_id = body.get("assigned_team_id")
    if agent_id is not None:
        get_agent(conn, agent_id)
    if team_id is not None:
        get_team(conn, team_id)
    status = "routed" if (agent_id or team_id) else "queued"
    reason = ""
    if agent_id:
        reason = "assigned directly at creation"
    elif team_id:
        reason = "assigned to team at creation"
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO tasks (title, description, project_scope,"
        " requested_by, required_capability_id, assigned_agent_id,"
        " assigned_team_id, status, priority, routing_reason, due_at,"
        " data_source, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (body["title"], body.get("description", ""),
         body.get("project_scope", "mq-ecosystem"), identity.actor,
         cap_id, agent_id, team_id, status, priority, reason,
         body.get("due_at"), data_source, now, now),
    )
    task_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "create_task", "task", task_id,
                 {"fields": ["title", "status"],
                  "status": status})
    events.record_event(
        conn, "task_created", "task", task_id,
        detail=f"task '{body['title']}' created ({status})",
        data_source=data_source, source_label="Orchestration Engine")
    conn.commit()
    if body.get("auto_route") and not agent_id:
        return route_task(conn, identity, task_id)
    return _task_detail(conn, task_id)


# ---------------------------------------------------------------- routing

def agent_workload(conn, agent_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE assigned_agent_id = ?"
        " AND status IN ('routed', 'running', 'blocked')",
        (agent_id,)).fetchone()
    running = conn.execute(
        "SELECT COUNT(*) AS n FROM executions WHERE agent_id = ?"
        " AND status = 'running'", (agent_id,)).fetchone()
    return row["n"] + running["n"]


def _eligible_agents(conn, task: dict) -> list[dict]:
    """Active agents, restricted by required capability and team."""
    rows = conn.execute(
        "SELECT * FROM agents WHERE lifecycle_status = 'active'"
        " ORDER BY id").fetchall()
    team_member_ids = None
    if task["assigned_team_id"]:
        team_member_ids = {r["agent_id"] for r in conn.execute(
            "SELECT agent_id FROM team_members WHERE team_id = ?",
            (task["assigned_team_id"],))}
    eligible = []
    for row in rows:
        agent = dict(row)
        reasons = []
        if team_member_ids is not None \
                and agent["id"] not in team_member_ids:
            reasons.append("not a member of the assigned team")
        if task["required_capability_id"]:
            has = conn.execute(
                "SELECT COUNT(*) AS n FROM agent_capabilities"
                " WHERE agent_id = ? AND capability_id = ?",
                (agent["id"], task["required_capability_id"]),
            ).fetchone()["n"]
            if not has:
                reasons.append("lacks the required capability")
        eligible.append({
            "agent_id": agent["id"],
            "slug": agent["slug"],
            "eligible": not reasons,
            "excluded_reason": "; ".join(reasons),
            "workload": agent_workload(conn, agent["id"]),
        })
    return eligible


def route_task(conn, identity, task_id: int) -> dict:
    task = _task_detail(conn, task_id)
    if task["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(
            409, f"task is '{task['status']}' and cannot be routed")
    candidates = _eligible_agents(conn, task)
    pool = [c for c in candidates if c["eligible"]]
    pool.sort(key=lambda c: (c["workload"], c["agent_id"]))
    now = utcnow()
    if pool:
        chosen = pool[0]
        reason = (
            f"agent {chosen['agent_id']} selected: eligible"
            + (f" for capability"
               f" #{task['required_capability_id']}"
               if task["required_capability_id"] else "")
            + f", lowest workload ({chosen['workload']}) among"
              f" {len(pool)} eligible agent(s); ties break by lowest"
              f" agent id")
        conn.execute(
            "UPDATE tasks SET assigned_agent_id = ?, status = 'routed',"
            " routing_reason = ?, updated_at = ? WHERE id = ?",
            (chosen["agent_id"], reason, now, task_id))
        cur = conn.execute(
            "INSERT INTO routing_decisions (task_id, capability_id,"
            " chosen_agent_id, chosen_team_id, outcome, reason,"
            " candidates, decided_by, created_at)"
            " VALUES (?, ?, ?, ?, 'routed', ?, ?, ?, ?)",
            (task_id, task["required_capability_id"],
             chosen["agent_id"], task["assigned_team_id"], reason,
             json.dumps(candidates), identity.actor, now))
        outcome, chosen_agent = "routed", chosen["agent_id"]
    elif task["assigned_team_id"] and not task["required_capability_id"]:
        reason = ("no individual agent selected; task routed to team"
                  f" {task['assigned_team_id']}")
        conn.execute(
            "UPDATE tasks SET status = 'routed', routing_reason = ?,"
            " updated_at = ? WHERE id = ?",
            (reason, now, task_id))
        cur = conn.execute(
            "INSERT INTO routing_decisions (task_id, capability_id,"
            " chosen_agent_id, chosen_team_id, outcome, reason,"
            " candidates, decided_by, created_at)"
            " VALUES (?, ?, NULL, ?, 'routed', ?, ?, ?, ?)",
            (task_id, task["required_capability_id"],
             task["assigned_team_id"], reason,
             json.dumps(candidates), identity.actor, now))
        outcome, chosen_agent = "routed", None
    else:
        blockers = [c["excluded_reason"] for c in candidates
                    if not c["eligible"] and c["excluded_reason"]]
        reason = ("no eligible agent: "
                  + ("; ".join(sorted(set(blockers)))
                     if blockers else "no active agents registered"))
        conn.execute(
            "UPDATE tasks SET routing_reason = ?, updated_at = ?"
            " WHERE id = ?",
            (reason, now, task_id))
        cur = conn.execute(
            "INSERT INTO routing_decisions (task_id, capability_id,"
            " chosen_agent_id, chosen_team_id, outcome, reason,"
            " candidates, decided_by, created_at)"
            " VALUES (?, ?, NULL, ?, 'unrouted', ?, ?, ?, ?)",
            (task_id, task["required_capability_id"],
             task["assigned_team_id"], reason,
             json.dumps(candidates), identity.actor, now))
        outcome, chosen_agent = "unrouted", None
    decision_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "route_task", "task", task_id,
                 {"fields": ["assigned_agent_id", "status"],
                  "outcome": outcome, "chosen_agent_id": chosen_agent})
    events.record_event(
        conn, "task_routed" if outcome == "routed" else "task_unrouted",
        "task", task_id, detail=reason,
        severity="info" if outcome == "routed" else "warning",
        data_source=task["data_source"],
        source_label="Orchestration Engine")
    conn.commit()
    decision = dict(conn.execute(
        "SELECT * FROM routing_decisions WHERE id = ?",
        (decision_id,)).fetchone())
    decision["candidates"] = json.loads(decision["candidates"])
    return {"decision": decision, "task": _task_detail(conn, task_id)}


def set_task_status(conn, identity, task_id: int, body: dict) -> dict:
    task = _task_detail(conn, task_id)
    status = body.get("status", "")
    if status not in TASK_STATUSES:
        raise HTTPException(422, f"status must be one of {TASK_STATUSES}")
    conn.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
        (status, utcnow(), task_id))
    audit.append(conn, identity.actor, "set_task_status", "task",
                 task_id, {"fields": ["status"], "to": status})
    events.record_event(
        conn, "task_status_changed", "task", task_id,
        detail=f"task status -> {status}",
        data_source=task["data_source"],
        source_label="Orchestration Engine")
    conn.commit()
    return _task_detail(conn, task_id)


# -------------------------------------------------------------- executions

def _execution_detail(conn, execution_id: int) -> dict:
    row = conn.execute("SELECT * FROM executions WHERE id = ?",
                       (execution_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "execution not found")
    ex = dict(row)
    steps = conn.execute(
        "SELECT * FROM workflow_steps WHERE execution_id = ?"
        " ORDER BY step_order, id", (execution_id,)).fetchall()
    ex["steps"] = [dict(s) for s in steps]
    return ex


def get_execution(conn, execution_id: int) -> dict:
    return _execution_detail(conn, execution_id)


def list_executions(conn, task_id: int | None = None,
                    agent_id: int | None = None,
                    status: str | None = None) -> list[dict]:
    query = "SELECT id FROM executions"
    clauses, params = [], []
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id)
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    return [_execution_detail(conn, r["id"])
            for r in conn.execute(query, params)]


def _insert_step(conn, execution_id: int, order: int, node_key: str,
                 title: str, now: str) -> None:
    conn.execute(
        "INSERT INTO workflow_steps (execution_id, node_key, title,"
        " step_order, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (execution_id, node_key, title, order, now, now))


def create_execution(conn, identity, body: dict) -> dict:
    if "task_id" not in body:
        raise HTTPException(422, "task_id is required")
    task = _task_detail(conn, body["task_id"])
    agent_id = body.get("agent_id", task["assigned_agent_id"])
    if agent_id is not None:
        get_agent(conn, agent_id)
    workflow_id = body.get("workflow_id")
    workflow_nodes: list[dict] = []
    if workflow_id is not None:
        wrow = conn.execute("SELECT * FROM workflows WHERE id = ?",
                            (workflow_id,)).fetchone()
        if wrow is None:
            raise HTTPException(422, "unknown workflow_id")
        workflow_nodes = json.loads(wrow["definition"]).get("nodes", [])
    data_source = body.get("data_source", task["data_source"])
    if data_source not in ("live", "demo"):
        raise HTTPException(422, "data_source must be 'live' or 'demo'")
    started_at = body.get("started_at") or utcnow()
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO executions (task_id, agent_id, workflow_id,"
        " status, started_at, data_source, created_at)"
        " VALUES (?, ?, ?, 'running', ?, ?, ?)",
        (task["id"], agent_id, workflow_id, started_at, data_source,
         now))
    execution_id = int(cur.lastrowid)
    step_defs = body.get("steps")
    if step_defs is None and workflow_nodes:
        step_defs = [{"node_key": n["key"],
                      "title": n.get("label", n["key"])}
                     for n in workflow_nodes]
    for order, step in enumerate(step_defs or []):
        if not step.get("node_key"):
            raise HTTPException(422, "each step needs a node_key")
        _insert_step(conn, execution_id, order, step["node_key"],
                     step.get("title", step["node_key"]), now)
    if task["status"] in ("queued", "routed"):
        conn.execute(
            "UPDATE tasks SET status = 'running', updated_at = ?"
            " WHERE id = ?", (now, task["id"]))
    audit.append(conn, identity.actor, "start_execution", "execution",
                 execution_id,
                 {"fields": ["task_id", "agent_id", "status"],
                  "task_id": task["id"], "agent_id": agent_id})
    events.record_event(
        conn, "execution_started", "execution", execution_id,
        detail=f"execution started for task {task['id']}",
        data_source=data_source, source_label="Orchestration Engine")
    conn.commit()
    return _execution_detail(conn, execution_id)


def finish_execution(conn, identity, execution_id: int,
                     body: dict) -> dict:
    ex = _execution_detail(conn, execution_id)
    if ex["status"] != "running":
        raise HTTPException(
            409, f"execution is already '{ex['status']}'")
    status = body.get("status", "")
    if status not in ("completed", "failed", "blocked", "cancelled"):
        raise HTTPException(
            422, "status must be one of completed, failed, blocked,"
                 " cancelled")
    ended_at = body.get("ended_at") or utcnow()
    duration_ms = int((_parse_ts(ended_at)
                       - _parse_ts(ex["started_at"])).total_seconds()
                      * 1000)
    if duration_ms < 0:
        raise HTTPException(422, "ended_at precedes started_at")
    conn.execute(
        "UPDATE executions SET status = ?, ended_at = ?,"
        " duration_ms = ?, outcome_summary = ?, error_detail = ?"
        " WHERE id = ?",
        (status, ended_at, duration_ms, body.get("outcome_summary", ""),
         body.get("error_detail"), execution_id))
    task_status = {"completed": "completed", "failed": "failed",
                   "blocked": "blocked",
                   "cancelled": "cancelled"}[status]
    conn.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
        (task_status, utcnow(), ex["task_id"]))
    audit.append(conn, identity.actor, "finish_execution", "execution",
                 execution_id,
                 {"fields": ["status", "duration_ms"], "to": status,
                  "duration_ms": duration_ms})
    events.record_event(
        conn, f"execution_{status}", "execution", execution_id,
        detail=(body.get("error_detail")
                or body.get("outcome_summary")
                or f"execution {status}"),
        severity="warning" if status == "failed" else "info",
        data_source=ex["data_source"],
        source_label="Orchestration Engine")
    if status == "failed" and ex["agent_id"]:
        events.record_event(
            conn, "agent_failure", "agent", ex["agent_id"],
            detail=(body.get("error_detail")
                    or f"execution {execution_id} failed"),
            severity="warning", data_source=ex["data_source"],
            source_label="Agent Operations")
    conn.commit()
    return _execution_detail(conn, execution_id)


def set_step_status(conn, identity, execution_id: int, step_id: int,
                    body: dict) -> dict:
    ex = _execution_detail(conn, execution_id)
    row = conn.execute(
        "SELECT * FROM workflow_steps WHERE id = ? AND execution_id = ?",
        (step_id, execution_id)).fetchone()
    if row is None:
        raise HTTPException(404, "step not found for this execution")
    status = body.get("status", "")
    if status not in STEP_STATUSES:
        raise HTTPException(422, f"status must be one of {STEP_STATUSES}")
    now = body.get("at") or utcnow()
    started_at = row["started_at"]
    ended_at = row["ended_at"]
    if status == "running" and not started_at:
        started_at = now
    if status in ("completed", "failed", "blocked"):
        if not started_at:
            started_at = now
        ended_at = now
    conn.execute(
        "UPDATE workflow_steps SET status = ?, started_at = ?,"
        " ended_at = ?, detail = ?, updated_at = ? WHERE id = ?",
        (status, started_at, ended_at,
         body.get("detail", row["detail"]), utcnow(), step_id))
    audit.append(conn, identity.actor, "set_step_status", "execution",
                 execution_id,
                 {"fields": ["step_status"], "step_id": step_id,
                  "node_key": row["node_key"], "to": status})
    if status in ("failed", "blocked"):
        events.record_event(
            conn, f"step_{status}", "execution", execution_id,
            detail=f"step '{row['node_key']}' {status}",
            severity="warning", data_source=ex["data_source"],
            source_label="Workflow Visualizer")
    conn.commit()
    return _execution_detail(conn, execution_id)


# --------------------------------------------------------- agent operations

def agent_operations(conn, agent_id: int) -> dict:
    """Module 2 surface: one agent's operational picture, from records."""
    agent = get_agent(conn, agent_id)
    open_tasks = [_task_detail(conn, r["id"]) for r in conn.execute(
        "SELECT id FROM tasks WHERE assigned_agent_id = ?"
        " AND status IN ('routed', 'running', 'blocked')"
        " ORDER BY id DESC", (agent_id,))]
    recent_executions = list_executions(conn, agent_id=agent_id)[:10]
    failure_events = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE subject_type = 'agent'"
        " AND subject_id = ? AND event_type IN"
        " ('agent_failure', 'agent_recovery', 'agent_paused',"
        "  'agent_resumed') ORDER BY id DESC LIMIT 20", (agent_id,))]
    failures = sum(1 for e in failure_events
                   if e["event_type"] == "agent_failure")
    recoveries = sum(1 for e in failure_events
                     if e["event_type"] == "agent_recovery")
    return {
        "as_of": utcnow(),
        "agent": agent,
        "workload": agent_workload(conn, agent_id),
        "open_assignments": open_tasks,
        "recent_executions": recent_executions,
        "failure_recovery_events": failure_events,
        "failure_count": failures,
        "recovery_count": recoveries,
    }


def record_agent_event(conn, identity, agent_id: int,
                       body: dict) -> dict:
    agent = get_agent(conn, agent_id)
    event_type = body.get("event_type", "")
    mapping = {"failure": "agent_failure", "recovery": "agent_recovery",
               "note": "agent_note"}
    if event_type not in mapping:
        raise HTTPException(
            422, "event_type must be one of failure, recovery, note")
    event_id = events.record_event(
        conn, mapping[event_type], "agent", agent_id,
        detail=body.get("detail", ""),
        severity=body.get("severity", "info"),
        data_source=agent["data_source"],
        source_label="Agent Operations")
    audit.append(conn, identity.actor, "record_agent_event", "agent",
                 agent_id,
                 {"fields": ["event"], "event_type": mapping[event_type]})
    conn.commit()
    return dict(conn.execute("SELECT * FROM events WHERE id = ?",
                             (event_id,)).fetchone())


def set_agent_lifecycle(conn, identity, agent_id: int,
                        target: str) -> dict:
    """Pause/resume an agent. Deactivation stays approval-gated."""
    agent = get_agent(conn, agent_id)
    if agent["lifecycle_status"] == "deactivated":
        raise HTTPException(
            409, "agent is deactivated; lifecycle changes are closed")
    if target not in ("paused", "active"):
        raise HTTPException(422, "target must be 'paused' or 'active'")
    conn.execute(
        "UPDATE agents SET lifecycle_status = ?, updated_at = ?"
        " WHERE id = ?", (target, utcnow(), agent_id))
    audit.append(conn, identity.actor,
                 "pause_agent" if target == "paused" else "resume_agent",
                 "agent", agent_id, {"fields": ["lifecycle_status"],
                                     "to": target})
    events.record_event(
        conn, "agent_paused" if target == "paused" else "agent_resumed",
        "agent", agent_id,
        detail=f"agent lifecycle -> {target}",
        data_source=agent["data_source"],
        source_label="Agent Operations")
    conn.commit()
    return get_agent(conn, agent_id)
