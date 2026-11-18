"""Explicit demo seed for the foundation.

Every row created here carries ``data_source = 'demo'`` and every agent
carries ``connection_status = 'demo_simulated'``. Nothing in this seed
represents a connected system. The seed is idempotent (it wipes existing
demo rows first) and demo rows can be removed in one operation via
:func:`wipe_demo`.
"""

import sqlite3

from .db import utcnow

DEMO_TEAMS = [
    ("demo-operations", "Demo Operations Team"),
    ("demo-research", "Demo Research Team"),
]

DEMO_CAPABILITIES = [
    ("demo-coordination", "Demo Coordination"),
    ("demo-coding", "Demo Coding"),
    ("demo-review", "Demo Review"),
    ("demo-research-skill", "Demo Research"),
]

DEMO_AGENTS = [
    # slug, display_name, role, team slug, capability slugs
    ("demo-coordinator", "Demo Coordinator", "Coordination (simulated)",
     "demo-operations", ["demo-coordination"]),
    ("demo-builder", "Demo Builder", "Software building (simulated)",
     "demo-operations", ["demo-coding"]),
    ("demo-reviewer", "Demo Reviewer", "Review (simulated)",
     "demo-research", ["demo-review"]),
    ("demo-researcher", "Demo Researcher", "Research (simulated)",
     "demo-research", ["demo-research-skill"]),
]


def wipe_demo(conn: sqlite3.Connection) -> None:
    # Phase 2 demo rows first, in dependency order.
    demo_task_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM tasks WHERE data_source = 'demo'")]
    demo_execution_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM executions WHERE data_source = 'demo'")]
    for task_id in demo_task_ids:
        demo_execution_ids.extend(
            r["id"] for r in conn.execute(
                "SELECT id FROM executions WHERE task_id = ?",
                (task_id,)))
    # Usage records reference tasks and executions; they go first.
    conn.execute("DELETE FROM usage_records WHERE data_source"
                 " = 'demo'")
    for task_id in demo_task_ids:
        conn.execute("DELETE FROM usage_records WHERE task_id = ?",
                     (task_id,))
    for execution_id in set(demo_execution_ids):
        conn.execute("DELETE FROM usage_records WHERE execution_id"
                     " = ?", (execution_id,))
    for execution_id in set(demo_execution_ids):
        conn.execute("DELETE FROM workflow_steps WHERE execution_id"
                     " = ?", (execution_id,))
        conn.execute("DELETE FROM executions WHERE id = ?",
                     (execution_id,))
    for task_id in demo_task_ids:
        conn.execute("DELETE FROM routing_decisions WHERE task_id"
                     " = ?", (task_id,))
        conn.execute("DELETE FROM communications WHERE task_id = ?",
                     (task_id,))
        conn.execute("DELETE FROM usage_records WHERE task_id = ?",
                     (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.execute("DELETE FROM communications WHERE data_source"
                 " = 'demo'")
    conn.execute("DELETE FROM events WHERE data_source = 'demo'")
    conn.execute("DELETE FROM usage_records WHERE data_source"
                 " = 'demo'")
    conn.execute("DELETE FROM workflows WHERE slug LIKE 'demo-%'")
    conn.execute("DELETE FROM connection_health_records"
                 " WHERE data_source = 'demo'")
    conn.execute("DELETE FROM connections WHERE slug LIKE 'demo-%'")
    conn.execute("DELETE FROM health_checks WHERE data_source"
                 " = 'demo'")
    conn.execute("DELETE FROM incidents WHERE data_source = 'demo'")
    demo_agent_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM agents WHERE data_source = 'demo'")]
    for agent_id in demo_agent_ids:
        conn.execute("DELETE FROM team_members WHERE agent_id = ?",
                     (agent_id,))
        conn.execute("DELETE FROM agent_capabilities WHERE agent_id = ?",
                     (agent_id,))
        conn.execute(
            "DELETE FROM agent_model_assignments WHERE agent_id = ?",
            (agent_id,))
    conn.execute("DELETE FROM agents WHERE data_source = 'demo'")
    conn.execute(
        "DELETE FROM teams WHERE slug LIKE 'demo-%'")
    conn.execute(
        "DELETE FROM capabilities WHERE slug LIKE 'demo-%'")
    conn.execute(
        "DELETE FROM model_configs WHERE provider = 'demo-provider'")
    conn.commit()


def seed_demo(conn: sqlite3.Connection) -> dict:
    wipe_demo(conn)
    now = utcnow()
    team_ids = {}
    for slug, name in DEMO_TEAMS:
        cur = conn.execute(
            "INSERT INTO teams (slug, display_name, description,"
            " project_scope, created_at, updated_at)"
            " VALUES (?, ?, ?, 'demo', ?, ?)",
            (slug, name, "Simulated team for foundation demonstration.",
             now, now),
        )
        team_ids[slug] = int(cur.lastrowid)
    cap_ids = {}
    for slug, name in DEMO_CAPABILITIES:
        cur = conn.execute(
            "INSERT INTO capabilities (slug, display_name, description,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (slug, name, "Simulated capability.", now, now),
        )
        cap_ids[slug] = int(cur.lastrowid)
    cur = conn.execute(
        "INSERT INTO model_configs (provider, model_name, display_name,"
        " config_status, notes, created_at, updated_at)"
        " VALUES ('demo-provider', 'demo-model', 'Demo Model (simulated)',"
        " 'demo_simulated', 'Placeholder only; no real model is connected.',"
        " ?, ?)",
        (now, now),
    )
    demo_model_id = int(cur.lastrowid)
    agent_ids = []
    for slug, name, role, team_slug, cap_slugs in DEMO_AGENTS:
        cur = conn.execute(
            "INSERT INTO agents (slug, display_name, role, description,"
            " owner_project, lifecycle_status, connection_status,"
            " data_source, notes, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'demo', 'active', 'demo_simulated',"
            " 'demo', 'Simulated agent. Not a connected system.', ?, ?)",
            (slug, name, role,
             "Simulated agent for foundation demonstration.", now, now),
        )
        agent_id = int(cur.lastrowid)
        agent_ids.append(agent_id)
        conn.execute(
            "INSERT INTO team_members (team_id, agent_id,"
            " assignment_role) VALUES (?, ?, 'member')",
            (team_ids[team_slug], agent_id),
        )
        for cap_slug in cap_slugs:
            conn.execute(
                "INSERT INTO agent_capabilities (agent_id,"
                " capability_id) VALUES (?, ?)",
                (agent_id, cap_ids[cap_slug]),
            )
        conn.execute(
            "INSERT INTO agent_model_assignments (agent_id,"
            " model_config_id, purpose) VALUES (?, ?, 'primary')",
            (agent_id, demo_model_id),
        )
    # ---- Phase 2 demo records (all data_source='demo') ----
    agent_by_slug = {}
    for slug, *_rest in DEMO_AGENTS:
        row = conn.execute("SELECT id FROM agents WHERE slug = ?",
                           (slug,)).fetchone()
        agent_by_slug[slug] = row["id"]
    cap_by_slug = {slug: cap_ids[slug] for slug, _ in DEMO_CAPABILITIES}

    cur = conn.execute(
        "INSERT INTO workflows (slug, display_name, description,"
        " definition, version, data_source, created_at, updated_at)"
        " VALUES ('demo-workflow', 'Demo Workflow (simulated)',"
        " 'Simulated three-step workflow for demonstration.',"
        " ?, 1, 'demo', ?, ?)",
        ('{"nodes": [{"key": "plan", "label": "Plan"},'
         ' {"key": "build", "label": "Build"},'
         ' {"key": "review", "label": "Review"}],'
         ' "transitions": [{"from": "plan", "to": "build"},'
         ' {"from": "build", "to": "review"}]}', now, now),
    )
    demo_workflow_id = int(cur.lastrowid)

    def _demo_task(title, agent_slug, capability_slug=None):
        cur = conn.execute(
            "INSERT INTO tasks (title, description, project_scope,"
            " requested_by, required_capability_id, assigned_agent_id,"
            " status, priority, routing_reason, data_source,"
            " created_at, updated_at) VALUES (?, ?, 'demo',"
            " 'demo-seed', ?, ?, 'routed', 'normal',"
            " 'assigned by the demo seed', 'demo', ?, ?)",
            (title, "Simulated task for demonstration.",
             cap_by_slug[capability_slug] if capability_slug else None,
             agent_by_slug[agent_slug], now, now),
        )
        return int(cur.lastrowid)

    def _demo_execution(task_id, agent_slug, status, started, ended,
                        duration_ms, error=None):
        cur = conn.execute(
            "INSERT INTO executions (task_id, agent_id, workflow_id,"
            " status, started_at, ended_at, duration_ms,"
            " outcome_summary, error_detail, data_source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo', ?)",
            (task_id, agent_by_slug[agent_slug], demo_workflow_id,
             status, started, ended, duration_ms,
             "Simulated execution." if status == "completed" else "",
             error, now),
        )
        return int(cur.lastrowid)

    def _demo_step(execution_id, order, key, title, status):
        conn.execute(
            "INSERT INTO workflow_steps (execution_id, node_key,"
            " title, step_order, status, started_at, ended_at, detail,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?,"
            " '', ?, ?)",
            (execution_id, key, title, order, status,
             "2026-11-18T10:00:00+00:00" if status != "pending"
             else None,
             "2026-11-18T10:01:00+00:00"
             if status in ("completed", "failed") else None,
             now, now),
        )

    task_a = _demo_task("Demo build task", "demo-builder",
                        "demo-coding")
    exec_a = _demo_execution(
        task_a, "demo-builder", "completed",
        "2026-11-18T10:00:00+00:00", "2026-11-18T10:02:00+00:00",
        120000)
    for order, (key, title) in enumerate(
            (("plan", "Plan"), ("build", "Build"),
             ("review", "Review"))):
        _demo_step(exec_a, order, key, title, "completed")
    conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?",
                 (task_a,))

    task_b = _demo_task("Demo review task", "demo-reviewer",
                        "demo-review")
    exec_b = _demo_execution(
        task_b, "demo-reviewer", "failed",
        "2026-11-18T11:00:00+00:00", "2026-11-18T11:01:00+00:00",
        60000, error="Simulated failure for demonstration.")
    _demo_step(exec_b, 0, "plan", "Plan", "completed")
    _demo_step(exec_b, 1, "build", "Build", "failed")
    _demo_step(exec_b, 2, "review", "Review", "blocked")
    conn.execute("UPDATE tasks SET status = 'failed' WHERE id = ?",
                 (task_b,))

    for from_slug, to_slug, summary in (
            ("demo-coordinator", "demo-builder",
             "Demo: please pick up the simulated build task."),
            ("demo-builder", "demo-coordinator",
             "Demo: simulated build finished.")):
        conn.execute(
            "INSERT INTO communications (task_id, from_agent_id,"
            " to_agent_id, from_actor, channel, summary, body,"
            " context_ref, occurred_at, data_source, created_at)"
            " VALUES (?, ?, ?, NULL, 'dashboard', ?, ?, '', ?,"
            " 'demo', ?)",
            (task_a, agent_by_slug[from_slug], agent_by_slug[to_slug],
             summary, summary, "2026-11-18T10:00:30+00:00", now),
        )
    conn.execute(
        "INSERT INTO communications (task_id, from_agent_id,"
        " to_agent_id, from_actor, channel, summary, body,"
        " context_ref, occurred_at, data_source, created_at)"
        " VALUES (NULL, NULL, ?, 'demo-operator', 'dashboard',"
        " 'Demo: human supervision note.', '', '',"
        " '2026-11-18T09:55:00+00:00', 'demo', ?)",
        (agent_by_slug["demo-coordinator"], now),
    )
    for event_type, subject_type, subject_id, detail in (
            ("task_routed", "task", task_a,
             "Demo: task routed to the simulated builder."),
            ("execution_failed", "execution", exec_b,
             "Demo: simulated execution failed.")):
        conn.execute(
            "INSERT INTO events (event_type, subject_type, subject_id,"
            " detail, severity, occurred_at, data_source,"
            " source_label, created_at) VALUES (?, ?, ?, ?, 'info',"
            " '2026-11-18T11:01:00+00:00', 'demo', 'Demo seed', ?)",
            (event_type, subject_type, subject_id, detail, now),
        )
    conn.execute(
        "INSERT INTO connections (slug, display_name, kind, provider,"
        " config_ref, config_notes, access_boundary, health_status,"
        " data_source, created_at, updated_at) VALUES"
        " ('demo-gateway', 'Demo Gateway (simulated)', 'model',"
        " 'demo-provider', 'demo://no-real-connection',"
        " 'Placeholder only; nothing is connected.',"
        " 'May not touch any real system.', 'demo_simulated',"
        " 'demo', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO usage_records (agent_id, task_id, execution_id,"
        " model_config_id, project_scope, input_tokens, output_tokens,"
        " estimated_cost_minor, actual_cost_minor, occurred_at,"
        " data_source, created_at) VALUES (?, ?, ?, ?, 'demo', 12000,"
        " 3000, NULL, NULL, '2026-11-18T10:02:00+00:00', 'demo', ?)",
        (agent_by_slug["demo-builder"], task_a, exec_a, demo_model_id,
         now),
    )
    conn.execute(
        "INSERT INTO usage_records (agent_id, task_id, execution_id,"
        " model_config_id, project_scope, input_tokens, output_tokens,"
        " estimated_cost_minor, actual_cost_minor, occurred_at,"
        " data_source, created_at) VALUES (?, ?, ?, ?, 'demo', 4000,"
        " 1000, 42, NULL, '2026-11-18T11:01:00+00:00', 'demo', ?)",
        (agent_by_slug["demo-reviewer"], task_b, exec_b, demo_model_id,
         now),
    )
    conn.execute(
        "INSERT INTO incidents (subject_type, subject_id, title,"
        " detail, severity, status, opened_by, opened_at, resolved_by,"
        " resolved_at, resolution_note, data_source, created_at,"
        " updated_at) VALUES ('system', NULL,"
        " 'Demo incident (simulated)', 'Simulated incident used to"
        " demonstrate the reliability lifecycle.', 'warning',"
        " 'resolved', 'demo-seed', '2026-11-18T08:00:00+00:00',"
        " 'demo-seed', '2026-11-18T08:30:00+00:00',"
        " 'Simulated recovery completed.', 'demo', ?, ?)",
        (now, now),
    )
    conn.commit()
    return {"agents": len(agent_ids), "teams": len(team_ids),
            "capabilities": len(cap_ids), "tasks": 2, "executions": 2,
            "communications": 3, "data_source": "demo"}


if __name__ == "__main__":
    from .db import connect, init_db

    connection = connect()
    init_db(connection)
    print(seed_demo(connection))
