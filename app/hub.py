"""Communication Hub (module 4), Workflow Visualizer data (module 5),
and the Intelligence Network graph (Concept C).

Communications are records kept by this system: who said what to whom,
in the context of which task, and when. Recording a message does not
deliver anything to any external agent — no agent is connected. The
workflow graph endpoint returns node/transition data with a computed
layout so the web UI can render SVG without a layout library; per-step
execution states are overlaid from recorded workflow_steps rows, with
failed and blocked steps returned as distinct statuses.
"""

import json
import sqlite3
from collections import defaultdict, deque

from fastapi import HTTPException

from . import audit, events
from .db import utcnow
from .services import get_agent, slugify
from .work import get_task


# ------------------------------------------------------------- communications

def create_communication(conn, identity, body: dict) -> dict:
    task_id = body.get("task_id")
    if task_id is not None:
        get_task(conn, task_id)
    from_agent_id = body.get("from_agent_id")
    to_agent_id = body.get("to_agent_id")
    if from_agent_id is not None:
        get_agent(conn, from_agent_id)
    if to_agent_id is not None:
        get_agent(conn, to_agent_id)
    from_actor = body.get("from_actor") or (
        None if from_agent_id else identity.actor)
    if from_agent_id is None and not from_actor:
        raise HTTPException(
            422, "a sender is required: from_agent_id or from_actor")
    if not (body.get("summary") or body.get("body")):
        raise HTTPException(422, "summary or body is required")
    data_source = body.get("data_source", "live")
    if data_source not in ("live", "demo"):
        raise HTTPException(422, "data_source must be 'live' or 'demo'")
    occurred_at = body.get("occurred_at") or utcnow()
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO communications (task_id, from_agent_id,"
        " to_agent_id, from_actor, channel, summary, body, context_ref,"
        " occurred_at, data_source, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, from_agent_id, to_agent_id, from_actor,
         body.get("channel", "dashboard"), body.get("summary", ""),
         body.get("body", ""), body.get("context_ref", ""), occurred_at,
         data_source, now),
    )
    comm_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "record_communication",
                 "communication", comm_id,
                 {"fields": ["task_id", "from_agent_id", "to_agent_id"],
                  "task_id": task_id, "from_agent_id": from_agent_id,
                  "to_agent_id": to_agent_id})
    events.record_event(
        conn, "communication_recorded", "communication", comm_id,
        detail=body.get("summary", "") or "message recorded",
        data_source=data_source, source_label="Communication Hub")
    conn.commit()
    return _communication_detail(conn, comm_id)


def _communication_detail(conn, comm_id: int) -> dict:
    row = conn.execute("SELECT * FROM communications WHERE id = ?",
                       (comm_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "communication not found")
    comm = dict(row)
    for side in ("from", "to"):
        agent_id = comm[f"{side}_agent_id"]
        if agent_id:
            arow = conn.execute(
                "SELECT slug, display_name FROM agents WHERE id = ?",
                (agent_id,)).fetchone()
            comm[f"{side}_agent"] = dict(arow) if arow else None
        else:
            comm[f"{side}_agent"] = None
    return comm


def list_communications(conn, task_id: int | None = None,
                        agent_id: int | None = None,
                        source: str | None = None) -> list[dict]:
    query = "SELECT id FROM communications"
    clauses, params = [], []
    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    if agent_id is not None:
        clauses.append("(from_agent_id = ? OR to_agent_id = ?)")
        params.extend([agent_id, agent_id])
    if source is not None:
        clauses.append("data_source = ?")
        params.append(source)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    return [_communication_detail(conn, r["id"])
            for r in conn.execute(query, params)]


# ---------------------------------------------------------------- workflows

def _validate_definition(definition) -> dict:
    if not isinstance(definition, dict):
        raise HTTPException(422, "definition must be an object with"
                                 " 'nodes' and 'transitions'")
    nodes = definition.get("nodes")
    transitions = definition.get("transitions", [])
    if not isinstance(nodes, list) or not nodes:
        raise HTTPException(422, "definition.nodes must be a non-empty"
                                 " list")
    keys = []
    for node in nodes:
        if not isinstance(node, dict) or not node.get("key"):
            raise HTTPException(422, "every node needs a 'key'")
        if node["key"] in keys:
            raise HTTPException(
                422, f"duplicate node key '{node['key']}'")
        keys.append(node["key"])
    key_set = set(keys)
    for tr in transitions:
        if not isinstance(tr, dict) \
                or tr.get("from") not in key_set \
                or tr.get("to") not in key_set:
            raise HTTPException(
                422, "every transition must reference existing node"
                     " keys in 'from' and 'to'")
    return {"nodes": nodes, "transitions": transitions}


def create_workflow(conn, identity, body: dict) -> dict:
    if not body.get("display_name"):
        raise HTTPException(422, "display_name is required")
    definition = _validate_definition(body.get("definition"))
    data_source = body.get("data_source", "live")
    if data_source not in ("live", "demo"):
        raise HTTPException(422, "data_source must be 'live' or 'demo'")
    slug = body.get("slug") or slugify(body["display_name"])
    now = utcnow()
    try:
        cur = conn.execute(
            "INSERT INTO workflows (slug, display_name, description,"
            " definition, version, data_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (slug, body["display_name"], body.get("description", ""),
             json.dumps(definition), body.get("version", 1),
             data_source, now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"workflow slug '{slug}' already"
                                 " exists")
    workflow_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "create_workflow", "workflow",
                 workflow_id,
                 {"fields": ["slug", "display_name"],
                  "nodes": len(definition["nodes"])})
    conn.commit()
    return get_workflow(conn, workflow_id)


def get_workflow(conn, workflow_id: int) -> dict:
    row = conn.execute("SELECT * FROM workflows WHERE id = ?",
                       (workflow_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "workflow not found")
    workflow = dict(row)
    workflow["definition"] = json.loads(workflow["definition"])
    return workflow


def list_workflows(conn) -> list[dict]:
    return [get_workflow(conn, r["id"]) for r in conn.execute(
        "SELECT id FROM workflows ORDER BY display_name")]


def _layout(definition: dict) -> dict[str, tuple[int, int]]:
    """Layered layout: x by longest-path depth from a root, y by
    order within the layer. Deterministic; cycles fall back to
    discovery order so a cyclic definition still renders."""
    nodes = definition["nodes"]
    keys = [n["key"] for n in nodes]
    incoming: dict[str, list[str]] = defaultdict(list)
    for tr in definition["transitions"]:
        incoming[tr["to"]].append(tr["from"])
    depth: dict[str, int] = {}
    remaining = list(keys)
    guard = 0
    while remaining and guard <= len(keys):
        progressed = False
        for key in list(remaining):
            preds = incoming.get(key, [])
            if all(p in depth for p in preds):
                depth[key] = (max((depth[p] for p in preds),
                                  default=-1) + 1)
                remaining.remove(key)
                progressed = True
        if not progressed:
            for key in remaining:
                depth[key] = max(depth.values(), default=-1) + 1
            break
        guard += 1
    layers: dict[int, list[str]] = defaultdict(list)
    for key in keys:
        layers[depth[key]].append(key)
    positions = {}
    for d, layer_keys in layers.items():
        for i, key in enumerate(layer_keys):
            positions[key] = (60 + d * 190, 50 + i * 90)
    return positions


def workflow_graph(conn, workflow_id: int,
                   execution_id: int | None = None) -> dict:
    workflow = get_workflow(conn, workflow_id)
    definition = workflow["definition"]
    positions = _layout(definition)
    step_status: dict[str, str] = {}
    if execution_id is not None:
        ex = conn.execute("SELECT id FROM executions WHERE id = ?",
                          (execution_id,)).fetchone()
        if ex is None:
            raise HTTPException(404, "execution not found")
        for row in conn.execute(
                "SELECT node_key, status FROM workflow_steps"
                " WHERE execution_id = ?", (execution_id,)):
            step_status[row["node_key"]] = row["status"]
    nodes = []
    for node in definition["nodes"]:
        x, y = positions[node["key"]]
        nodes.append({
            "key": node["key"],
            "label": node.get("label", node["key"]),
            "kind": node.get("kind", "step"),
            "x": x, "y": y,
            "status": step_status.get(node["key"], "not_started")
            if execution_id is not None else None,
        })
    edges = [{"from": tr["from"], "to": tr["to"],
              "label": tr.get("label", "")}
             for tr in definition["transitions"]]
    width = (max((n["x"] for n in nodes), default=0) + 130)
    height = (max((n["y"] for n in nodes), default=0) + 80)
    return {
        "workflow_id": workflow_id,
        "execution_id": execution_id,
        "data_source": workflow["data_source"],
        "nodes": nodes,
        "edges": edges,
        "width": width,
        "height": height,
    }


# ------------------------------------------------------- intelligence network

def network_graph(conn) -> dict:
    """Concept C data: agents and teams as nodes; memberships and
    recorded communications as edges. Sources stay labeled per node."""
    agents = [dict(r) for r in conn.execute(
        "SELECT id, slug, display_name, connection_status,"
        " lifecycle_status, data_source FROM agents"
        " ORDER BY display_name")]
    teams = [dict(r) for r in conn.execute(
        "SELECT id, slug, display_name, project_scope FROM teams"
        " ORDER BY display_name")]
    memberships = [dict(r) for r in conn.execute(
        "SELECT team_id, agent_id FROM team_members")]
    comm_counts: dict[tuple[int, int], int] = defaultdict(int)
    for row in conn.execute(
            "SELECT from_agent_id, to_agent_id, COUNT(*) AS n"
            " FROM communications"
            " WHERE from_agent_id IS NOT NULL"
            " AND to_agent_id IS NOT NULL"
            " GROUP BY from_agent_id, to_agent_id"):
        comm_counts[(row["from_agent_id"], row["to_agent_id"])] = row["n"]
    return {
        "as_of": utcnow(),
        "agents": agents,
        "teams": teams,
        "memberships": memberships,
        "communication_edges": [
            {"from_agent_id": f, "to_agent_id": t, "count": n}
            for (f, t), n in sorted(comm_counts.items())
        ],
    }
