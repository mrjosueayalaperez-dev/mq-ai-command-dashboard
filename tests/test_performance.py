"""Performance Center: figures are real arithmetic over recorded
executions; empty states are honest; demo and live never blend."""

from conftest import OPERATOR, OWNER


def _agent(client, name):
    res = client.post("/api/agents", json={"display_name": name},
                      headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def _run(client, agent_id, title, status, started, ended):
    task = client.post(
        "/api/tasks",
        json={"title": title, "assigned_agent_id": agent_id},
        headers=OPERATOR).json()
    execution = client.post(
        "/api/executions",
        json={"task_id": task["id"], "started_at": started},
        headers=OPERATOR).json()
    res = client.post(
        f"/api/executions/{execution['id']}/finish",
        json={"status": status, "ended_at": ended},
        headers=OPERATOR)
    assert res.status_code == 200, res.text
    return res.json()


def test_empty_state_is_honest(client):
    agent = _agent(client, "Perf Nobody")
    perf = client.get(
        f"/api/performance?agent_id={agent['id']}").json()
    block = perf["by_source"]["live"]
    assert block["executions_recorded"] == 0
    assert block["success_rate"] is None
    assert block["failure_rate"] is None
    assert block["avg_duration_ms"] is None
    assert block["note"] and "no executions recorded yet" \
        in block["note"]


def test_performance_arithmetic(client):
    agent = _agent(client, "Perf Measured")
    _run(client, agent["id"], "P1", "completed",
         "2026-11-18T09:00:00+00:00", "2026-11-18T09:00:02+00:00")
    _run(client, agent["id"], "P2", "failed",
         "2026-11-18T09:10:00+00:00", "2026-11-18T09:10:04+00:00")
    _run(client, agent["id"], "P3", "completed",
         "2026-11-18T09:20:00+00:00", "2026-11-18T09:20:06+00:00")
    perf = client.get(
        f"/api/performance?agent_id={agent['id']}").json()
    block = perf["by_source"]["live"]
    assert block["executions_recorded"] == 3
    assert block["completed"] == 2
    assert block["failed"] == 1
    assert block["success_rate"] == round(2 / 3, 4)
    assert block["failure_rate"] == round(1 / 3, 4)
    assert block["avg_duration_ms"] == 4000.0
    assert block["throughput_by_day"] == {"2026-11-18": 3}
    per_agent = block["per_agent"]
    assert len(per_agent) == 1
    entry = per_agent[0]
    assert entry["agent_id"] == agent["id"]
    assert entry["busy_ms"] == 12000
    assert entry["utilization_pct"] == 100.0
    assert entry["open_tasks"] == 0
    assert entry["workload"] == 0


def test_demo_and_live_performance_never_blend(client):
    client.post("/api/admin/seed-demo", headers=OWNER)
    perf = client.get("/api/performance").json()
    demo = perf["by_source"]["demo"]
    # The demo seed carries exactly one completed and one failed
    # execution, 120000 ms and 60000 ms.
    assert demo["executions_recorded"] == 2
    assert demo["completed"] == 1
    assert demo["failed"] == 1
    assert demo["success_rate"] == 0.5
    assert demo["avg_duration_ms"] == 90000.0
    live = perf["by_source"]["live"]
    # Demo durations must not leak into the live average: any live
    # executions recorded by other tests are short, timed ones.
    if live["avg_duration_ms"] is not None:
        assert live["avg_duration_ms"] < 90000.0
