"""Registry, evidence, approval, and overview logic.

Honesty rules enforced here (not by convention):

- A connection status of ``verified`` or ``operational`` requires a
  linked passing evidence record for that agent.
- ``operational`` and agent deactivation are consequential actions:
  they can only be applied by an owner deciding an approval record.
- Demo and live rows are never summed together in the overview.
"""

import re
import sqlite3

from fastapi import HTTPException

from . import audit
from .db import utcnow
from .taxonomy import (
    LIFECYCLE_STATUSES,
    STATUS_SET,
    requires_approval,
    requires_evidence,
)

CONSEQUENTIAL_ACTIONS = ("promote_operational", "deactivate_agent")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "item"


def _row(row) -> dict | None:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------- teams

def create_team(conn, identity, body: dict) -> dict:
    slug = body.get("slug") or slugify(body["display_name"])
    now = utcnow()
    try:
        cur = conn.execute(
            "INSERT INTO teams (slug, display_name, description,"
            " project_scope, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (slug, body["display_name"], body.get("description", ""),
             body.get("project_scope", "mq-ecosystem"), now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"team slug '{slug}' already exists")
    team_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "create_team", "team", team_id,
                 {"fields": ["slug", "display_name"]})
    conn.commit()
    return get_team(conn, team_id)


def get_team(conn, team_id: int) -> dict:
    row = conn.execute("SELECT * FROM teams WHERE id = ?",
                       (team_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "team not found")
    team = dict(row)
    members = conn.execute(
        "SELECT a.id AS agent_id, a.slug, a.display_name,"
        " tm.assignment_role FROM team_members tm"
        " JOIN agents a ON a.id = tm.agent_id"
        " WHERE tm.team_id = ? ORDER BY a.display_name",
        (team_id,),
    ).fetchall()
    team["members"] = [dict(m) for m in members]
    return team


def list_teams(conn) -> list[dict]:
    ids = [r["id"] for r in
           conn.execute("SELECT id FROM teams ORDER BY display_name")]
    return [get_team(conn, i) for i in ids]


def add_team_member(conn, identity, team_id: int, body: dict) -> dict:
    get_team(conn, team_id)
    agent = get_agent(conn, body["agent_id"])
    try:
        conn.execute(
            "INSERT INTO team_members (team_id, agent_id, assignment_role)"
            " VALUES (?, ?, ?)",
            (team_id, agent["id"], body.get("assignment_role", "")),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "agent is already a member of this team")
    audit.append(conn, identity.actor, "add_team_member", "team", team_id,
                 {"fields": ["agent_id"], "agent_id": agent["id"]})
    conn.commit()
    return get_team(conn, team_id)


# --------------------------------------------------------- capabilities

def create_capability(conn, identity, body: dict) -> dict:
    slug = body.get("slug") or slugify(body["display_name"])
    now = utcnow()
    try:
        cur = conn.execute(
            "INSERT INTO capabilities (slug, display_name, description,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (slug, body["display_name"], body.get("description", ""),
             now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"capability slug '{slug}' already exists")
    cap_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "create_capability", "capability",
                 cap_id, {"fields": ["slug", "display_name"]})
    conn.commit()
    return _row(conn.execute("SELECT * FROM capabilities WHERE id = ?",
                             (cap_id,)).fetchone())


def list_capabilities(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM capabilities ORDER BY display_name")]


# --------------------------------------------------------- model configs

def create_model_config(conn, identity, body: dict) -> dict:
    status = body.get("config_status", "demo_simulated")
    _check_status_value(status)
    if requires_evidence(status) or requires_approval(status):
        raise HTTPException(
            422,
            f"a model config cannot be created directly in status"
            f" '{status}'; create it as configured/demo and promote it"
            f" through the evidence and approval rules",
        )
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO model_configs (provider, model_name, display_name,"
        " config_status, cost_input_per_1m, cost_output_per_1m, notes,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (body["provider"], body["model_name"], body["display_name"],
         status, body.get("cost_input_per_1m"),
         body.get("cost_output_per_1m"), body.get("notes", ""), now, now),
    )
    cfg_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "create_model_config",
                 "model_config", cfg_id,
                 {"fields": ["provider", "model_name", "display_name"]})
    conn.commit()
    return _row(conn.execute("SELECT * FROM model_configs WHERE id = ?",
                             (cfg_id,)).fetchone())


def list_model_configs(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM model_configs ORDER BY display_name")]


# ---------------------------------------------------------------- agents

def _check_status_value(status: str) -> None:
    if status not in STATUS_SET:
        raise HTTPException(
            422,
            f"invalid connection_status '{status}'; must be one of"
            f" {sorted(STATUS_SET)}",
        )


def has_passing_evidence(conn, agent_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM evidence_records"
        " WHERE target_type = 'agent' AND target_id = ?"
        " AND result = 'pass'",
        (agent_id,),
    ).fetchone()
    return row["n"] > 0


def _agent_detail(conn, agent_id: int) -> dict:
    row = conn.execute("SELECT * FROM agents WHERE id = ?",
                       (agent_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "agent not found")
    agent = dict(row)
    caps = conn.execute(
        "SELECT c.id, c.slug, c.display_name, ac.proficiency_note"
        " FROM agent_capabilities ac"
        " JOIN capabilities c ON c.id = ac.capability_id"
        " WHERE ac.agent_id = ? ORDER BY c.display_name",
        (agent_id,),
    ).fetchall()
    agent["capabilities"] = [dict(c) for c in caps]
    teams = conn.execute(
        "SELECT t.id, t.slug, t.display_name, tm.assignment_role"
        " FROM team_members tm JOIN teams t ON t.id = tm.team_id"
        " WHERE tm.agent_id = ? ORDER BY t.display_name",
        (agent_id,),
    ).fetchall()
    agent["teams"] = [dict(t) for t in teams]
    models = conn.execute(
        "SELECT mc.id AS model_config_id, mc.provider, mc.model_name,"
        " mc.display_name, ama.purpose FROM agent_model_assignments ama"
        " JOIN model_configs mc ON mc.id = ama.model_config_id"
        " WHERE ama.agent_id = ? ORDER BY mc.display_name",
        (agent_id,),
    ).fetchall()
    agent["model_assignments"] = [dict(m) for m in models]
    return agent


def get_agent(conn, agent_id: int) -> dict:
    return _agent_detail(conn, agent_id)


def list_agents(conn) -> list[dict]:
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM agents ORDER BY display_name")]
    return [_agent_detail(conn, i) for i in ids]


def create_agent(conn, identity, body: dict) -> dict:
    status = body.get("connection_status", "demo_simulated")
    _check_status_value(status)
    if requires_evidence(status) or requires_approval(status):
        raise HTTPException(
            422,
            f"an agent cannot be registered directly in status"
            f" '{status}'; register it as demo_simulated/configured and"
            f" promote it only with evidence (and approval, for"
            f" operational)",
        )
    data_source = body.get("data_source", "live")
    if data_source not in ("live", "demo"):
        raise HTTPException(422, "data_source must be 'live' or 'demo'")
    slug = body.get("slug") or slugify(body["display_name"])
    now = utcnow()
    try:
        cur = conn.execute(
            "INSERT INTO agents (slug, display_name, role, description,"
            " owner_project, lifecycle_status, connection_status,"
            " data_source, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
            (slug, body["display_name"], body.get("role", ""),
             body.get("description", ""),
             body.get("owner_project", "mq-ecosystem"), status,
             data_source, body.get("notes", ""), now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"agent slug '{slug}' already exists")
    agent_id = int(cur.lastrowid)
    for cap_slug in body.get("capabilities", []):
        cap = conn.execute("SELECT id FROM capabilities WHERE slug = ?",
                           (cap_slug,)).fetchone()
        if cap is None:
            raise HTTPException(422, f"unknown capability '{cap_slug}'")
        conn.execute(
            "INSERT OR IGNORE INTO agent_capabilities"
            " (agent_id, capability_id) VALUES (?, ?)",
            (agent_id, cap["id"]),
        )
    if body.get("team_id"):
        team = conn.execute("SELECT id FROM teams WHERE id = ?",
                            (body["team_id"],)).fetchone()
        if team is None:
            raise HTTPException(422, "unknown team_id")
        conn.execute(
            "INSERT OR IGNORE INTO team_members"
            " (team_id, agent_id, assignment_role) VALUES (?, ?, ?)",
            (body["team_id"], agent_id, body.get("assignment_role", "")),
        )
    if body.get("model_config_id"):
        cfg = conn.execute("SELECT id FROM model_configs WHERE id = ?",
                           (body["model_config_id"],)).fetchone()
        if cfg is None:
            raise HTTPException(422, "unknown model_config_id")
        conn.execute(
            "INSERT OR IGNORE INTO agent_model_assignments"
            " (agent_id, model_config_id, purpose) VALUES (?, ?, ?)",
            (agent_id, body["model_config_id"],
             body.get("model_purpose", "primary")),
        )
    audit.append(conn, identity.actor, "create_agent", "agent", agent_id,
                 {"fields": ["slug", "display_name", "connection_status",
                             "data_source"]})
    conn.commit()
    return _agent_detail(conn, agent_id)


EDITABLE_AGENT_FIELDS = (
    "display_name", "role", "description", "owner_project", "notes",
)


def update_agent(conn, identity, agent_id: int, body: dict) -> dict:
    agent = _agent_detail(conn, agent_id)
    if "connection_status" in body:
        raise HTTPException(
            422,
            "connection_status changes go through POST"
            " /api/agents/{id}/status (evidence-gated), not PATCH",
        )
    if "lifecycle_status" in body and \
            body["lifecycle_status"] not in LIFECYCLE_STATUSES:
        raise HTTPException(422, "invalid lifecycle_status")
    if body.get("lifecycle_status") == "deactivated":
        raise HTTPException(
            422,
            "deactivation is a consequential action: request it through"
            " POST /api/approvals (action_type=deactivate_agent)",
        )
    changed = []
    sets = []
    values = []
    for field in EDITABLE_AGENT_FIELDS + ("lifecycle_status",):
        if field in body and body[field] != agent[field]:
            sets.append(f"{field} = ?")
            values.append(body[field])
            changed.append(field)
    if not changed:
        return agent
    sets.append("updated_at = ?")
    values.append(utcnow())
    values.append(agent_id)
    conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = ?",
                 values)
    audit.append(conn, identity.actor, "update_agent", "agent", agent_id,
                 {"fields": changed})
    conn.commit()
    return _agent_detail(conn, agent_id)


def set_agent_status(conn, identity, agent_id: int, body: dict) -> dict:
    agent = _agent_detail(conn, agent_id)
    new_status = body.get("status", "")
    _check_status_value(new_status)
    if requires_approval(new_status):
        raise HTTPException(
            422,
            f"status '{new_status}' is a consequential action: request"
            f" it through POST /api/approvals"
            f" (action_type=promote_operational) and have an owner"
            f" decide it",
        )
    if requires_evidence(new_status) \
            and not has_passing_evidence(conn, agent_id):
        raise HTTPException(
            422,
            f"status '{new_status}' requires a linked passing evidence"
            f" record for this agent; none exists — record one via"
            f" POST /api/evidence first",
        )
    conn.execute(
        "UPDATE agents SET connection_status = ?, updated_at = ?"
        " WHERE id = ?",
        (new_status, utcnow(), agent_id),
    )
    audit.append(conn, identity.actor, "set_agent_status", "agent",
                 agent_id,
                 {"fields": ["connection_status"], "to": new_status})
    conn.commit()
    return _agent_detail(conn, agent_id)


def assign_model(conn, identity, agent_id: int, body: dict) -> dict:
    _agent_detail(conn, agent_id)
    cfg = conn.execute("SELECT id FROM model_configs WHERE id = ?",
                       (body["model_config_id"],)).fetchone()
    if cfg is None:
        raise HTTPException(422, "unknown model_config_id")
    try:
        conn.execute(
            "INSERT INTO agent_model_assignments"
            " (agent_id, model_config_id, purpose) VALUES (?, ?, ?)",
            (agent_id, body["model_config_id"],
             body.get("purpose", "primary")),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "assignment already exists")
    audit.append(conn, identity.actor, "assign_model", "agent", agent_id,
                 {"fields": ["model_config_id"]})
    conn.commit()
    return _agent_detail(conn, agent_id)


# -------------------------------------------------------------- evidence

def create_evidence(conn, identity, body: dict) -> dict:
    agent = _agent_detail(conn, body["target_id"]) \
        if body.get("target_type", "agent") == "agent" else None
    result = body.get("result", "")
    if result not in ("pass", "fail"):
        raise HTTPException(422, "result must be 'pass' or 'fail'")
    cur = conn.execute(
        "INSERT INTO evidence_records (target_type, target_id, kind,"
        " result, detail, recorded_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (body.get("target_type", "agent"), body["target_id"],
         body.get("kind", "manual_test"), result,
         body.get("detail", ""), identity.actor, utcnow()),
    )
    ev_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "record_evidence", "agent",
                 body["target_id"],
                 {"fields": ["evidence"], "evidence_id": ev_id,
                  "result": result})
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM evidence_records WHERE id = ?",
        (ev_id,)).fetchone())


def list_evidence(conn, target_id: int | None) -> list[dict]:
    if target_id is None:
        rows = conn.execute(
            "SELECT * FROM evidence_records ORDER BY id DESC")
    else:
        rows = conn.execute(
            "SELECT * FROM evidence_records WHERE target_id = ?"
            " ORDER BY id DESC", (target_id,))
    return [dict(r) for r in rows]


# -------------------------------------------------------------- approvals

def create_approval(conn, identity, body: dict) -> dict:
    action_type = body.get("action_type", "")
    if action_type not in CONSEQUENTIAL_ACTIONS:
        raise HTTPException(
            422,
            f"action_type must be one of {list(CONSEQUENTIAL_ACTIONS)}",
        )
    _agent_detail(conn, body["target_id"])
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO approvals (action_type, target_type, target_id,"
        " requested_by, reason, status, created_at, updated_at)"
        " VALUES (?, 'agent', ?, ?, ?, 'pending', ?, ?)",
        (action_type, body["target_id"], identity.actor,
         body.get("reason", ""), now, now),
    )
    approval_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "request_approval", "approval",
                 approval_id,
                 {"fields": ["action_type"], "action_type": action_type,
                  "target_id": body["target_id"]})
    conn.commit()
    return dict(conn.execute("SELECT * FROM approvals WHERE id = ?",
                             (approval_id,)).fetchone())


def list_approvals(conn, status: str | None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status = ? ORDER BY id DESC",
            (status,))
    else:
        rows = conn.execute("SELECT * FROM approvals ORDER BY id DESC")
    return [dict(r) for r in rows]


def decide_approval(conn, identity, approval_id: int, body: dict) -> dict:
    row = conn.execute("SELECT * FROM approvals WHERE id = ?",
                       (approval_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "approval not found")
    approval = dict(row)
    if approval["status"] != "pending":
        raise HTTPException(
            409, f"approval is already '{approval['status']}'")
    decision = body.get("decision", "")
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, "decision must be 'approved' or"
                                 " 'rejected'")
    agent_id = approval["target_id"]
    if decision == "approved":
        if approval["action_type"] == "promote_operational":
            if not has_passing_evidence(conn, agent_id):
                raise HTTPException(
                    422,
                    "cannot approve promotion to operational: no"
                    " passing evidence record exists for this agent",
                )
            conn.execute(
                "UPDATE agents SET connection_status = 'operational',"
                " updated_at = ? WHERE id = ?",
                (utcnow(), agent_id),
            )
        elif approval["action_type"] == "deactivate_agent":
            conn.execute(
                "UPDATE agents SET lifecycle_status = 'deactivated',"
                " updated_at = ? WHERE id = ?",
                (utcnow(), agent_id),
            )
    now = utcnow()
    conn.execute(
        "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?,"
        " decision_note = ?, updated_at = ? WHERE id = ?",
        (decision, identity.actor, now, body.get("note", ""), now,
         approval_id),
    )
    audit.append(conn, identity.actor, "decide_approval", "approval",
                 approval_id,
                 {"fields": ["status"], "decision": decision,
                  "action_type": approval["action_type"],
                  "target_id": agent_id, "applied": decision == "approved"})
    conn.commit()
    return dict(conn.execute("SELECT * FROM approvals WHERE id = ?",
                             (approval_id,)).fetchone())


# --------------------------------------------------------------- overview

def overview(conn) -> dict:
    counts = {status: 0 for status in STATUS_SET}
    demo_counts = {status: 0 for status in STATUS_SET}
    live_counts = {status: 0 for status in STATUS_SET}
    for row in conn.execute(
            "SELECT connection_status, data_source, COUNT(*) AS n"
            " FROM agents GROUP BY connection_status, data_source"):
        counts[row["connection_status"]] += row["n"]
        if row["data_source"] == "demo":
            demo_counts[row["connection_status"]] += row["n"]
        else:
            live_counts[row["connection_status"]] += row["n"]
    total = sum(counts.values())
    demo_total = sum(demo_counts.values())
    teams = list_teams(conn)
    pending = list_approvals(conn, "pending")
    recent_audit = [dict(r) for r in conn.execute(
        "SELECT id, actor, action, target_type, target_id, created_at"
        " FROM audit_log ORDER BY id DESC LIMIT 10")]
    return {
        "product": "M&Q AI Command Dashboard",
        "stage": "phase2-full",
        "as_of": utcnow(),
        "data_source": "demo" if total and demo_total == total else "mixed"
                       if demo_total else "live",
        "demo_only": bool(total) and demo_total == total,
        "demo_data_present": demo_total > 0,
        "total_agents": total,
        "counts_by_status": counts,
        "demo_counts_by_status": demo_counts,
        "live_counts_by_status": live_counts,
        "teams": [
            {"id": t["id"], "slug": t["slug"],
             "display_name": t["display_name"],
             "member_count": len(t["members"])}
            for t in teams
        ],
        "pending_approvals": pending,
        "pending_approval_count": len(pending),
        "recent_audit": recent_audit,
    }
