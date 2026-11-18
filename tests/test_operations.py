"""Agent Operations: inspection view, assignments, pause/resume via
lifecycle, and failure/recovery event records."""

from conftest import OPERATOR, VIEWER


def _agent(client, name, capabilities=None):
    body = {"display_name": name}
    if capabilities:
        body["capabilities"] = capabilities
    res = client.post("/api/agents", json=body, headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def test_operations_view_aggregates_records(client):
    agent = _agent(client, "Ops Inspected")
    task = client.post(
        "/api/tasks",
        json={"title": "Ops task", "assigned_agent_id": agent["id"]},
        headers=OPERATOR).json()
    execution = client.post(
        "/api/executions", json={"task_id": task["id"]},
        headers=OPERATOR).json()
    view = client.get(f"/api/operations/agents/{agent['id']}").json()
    assert view["agent"]["id"] == agent["id"]
    assert view["workload"] >= 2  # open task + running execution
    assert [t["id"] for t in view["open_assignments"]] == [task["id"]]
    assert view["recent_executions"][0]["id"] == execution["id"]
    client.post(f"/api/executions/{execution['id']}/finish",
                json={"status": "completed"}, headers=OPERATOR)


def test_failed_execution_counts_as_failure(client):
    agent = _agent(client, "Ops Failing")
    task = client.post(
        "/api/tasks",
        json={"title": "Will fail", "assigned_agent_id": agent["id"]},
        headers=OPERATOR).json()
    execution = client.post(
        "/api/executions", json={"task_id": task["id"]},
        headers=OPERATOR).json()
    client.post(f"/api/executions/{execution['id']}/finish",
                json={"status": "failed",
                      "error_detail": "simulated failure"},
                headers=OPERATOR)
    view = client.get(f"/api/operations/agents/{agent['id']}").json()
    assert view["failure_count"] >= 1
    assert view["failure_recovery_events"][0]["event_type"] \
        == "agent_failure"


def test_failure_and_recovery_events_recorded(client):
    agent = _agent(client, "Ops Recovering")
    res = client.post(f"/api/agents/{agent['id']}/events",
                      json={"event_type": "failure",
                            "detail": "disk full (simulated)",
                            "severity": "warning"},
                      headers=OPERATOR)
    assert res.status_code == 201, res.text
    res = client.post(f"/api/agents/{agent['id']}/events",
                      json={"event_type": "recovery",
                            "detail": "restarted (simulated)"},
                      headers=OPERATOR)
    assert res.status_code == 201
    view = client.get(f"/api/operations/agents/{agent['id']}").json()
    assert view["failure_count"] == 1
    assert view["recovery_count"] == 1
    bad = client.post(f"/api/agents/{agent['id']}/events",
                      json={"event_type": "explosion"},
                      headers=OPERATOR)
    assert bad.status_code == 422


def test_pause_excludes_agent_from_routing_then_resume(client):
    client.post("/api/capabilities",
                json={"display_name": "Pause Skill",
                      "slug": "pause-skill"},
                headers=OPERATOR)
    pausable = _agent(client, "Pausable Agent", ["pause-skill"])
    standby = _agent(client, "Standby Agent", ["pause-skill"])
    res = client.post(f"/api/agents/{pausable['id']}/pause",
                      headers=OPERATOR)
    assert res.status_code == 200
    assert res.json()["lifecycle_status"] == "paused"
    task = client.post(
        "/api/tasks",
        json={"title": "Routed while paused",
              "required_capability": "pause-skill"},
        headers=OPERATOR).json()
    payload = client.post(f"/api/tasks/{task['id']}/route",
                          headers=OPERATOR).json()
    assert payload["decision"]["chosen_agent_id"] == standby["id"]
    res = client.post(f"/api/agents/{pausable['id']}/resume",
                      headers=OPERATOR)
    assert res.json()["lifecycle_status"] == "active"


def test_assignment_endpoint(client):
    agent = _agent(client, "Assignment Target")
    res = client.post(
        "/api/operations/assignments",
        json={"title": "Assigned through operations",
              "assigned_agent_id": agent["id"]},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "routed"
    missing = client.post("/api/operations/assignments",
                          json={"title": "Nobody assigned"},
                          headers=OPERATOR)
    assert missing.status_code == 422


def test_viewer_cannot_operate(client):
    agent = _agent(client, "Viewer Blocked")
    assert client.post(f"/api/agents/{agent['id']}/pause",
                       headers=VIEWER).status_code == 403
    assert client.post(f"/api/agents/{agent['id']}/events",
                       json={"event_type": "failure"},
                       headers=VIEWER).status_code == 403
    assert client.post("/api/tasks", json={"title": "x"},
                       headers=VIEWER).status_code == 403
