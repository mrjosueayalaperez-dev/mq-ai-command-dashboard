"""Orchestration Engine: capability routing, routing-decision records,
executions, step states, and append-only execution history."""

from conftest import OPERATOR, OWNER


def _capability(client, slug, name):
    res = client.post("/api/capabilities",
                      json={"display_name": name, "slug": slug},
                      headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def _agent(client, name, capabilities=None):
    body = {"display_name": name}
    if capabilities:
        body["capabilities"] = capabilities
    res = client.post("/api/agents", json=body, headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def _task(client, title, **overrides):
    body = {"title": title}
    body.update(overrides)
    res = client.post("/api/tasks", json=body, headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def test_route_picks_capable_agent_with_lowest_workload(client):
    _capability(client, "route-skill", "Route Skill")
    busy = _agent(client, "Busy Capable", ["route-skill"])
    free = _agent(client, "Free Capable", ["route-skill"])
    _task(client, "Workload filler", assigned_agent_id=busy["id"])
    task = _task(client, "Needs routing",
                 required_capability="route-skill")
    assert task["status"] == "queued"
    res = client.post(f"/api/tasks/{task['id']}/route",
                      headers=OPERATOR)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["decision"]["outcome"] == "routed"
    assert payload["decision"]["chosen_agent_id"] == free["id"]
    assert "workload" in payload["decision"]["reason"]
    assert payload["task"]["status"] == "routed"
    assert payload["task"]["assigned_agent_id"] == free["id"]
    # The decision is preserved on the task as history.
    assert len(payload["task"]["routing_decisions"]) == 1
    candidates = payload["decision"]["candidates"]
    by_id = {c["agent_id"]: c for c in candidates
             if c["agent_id"] in (busy["id"], free["id"])}
    assert by_id[busy["id"]]["workload"] == 1
    assert by_id[free["id"]]["workload"] == 0


def test_route_never_picks_agent_without_capability(client):
    _capability(client, "rare-skill", "Rare Skill")
    capable = _agent(client, "Only Capable", ["rare-skill"])
    _agent(client, "Not Capable")
    for i in range(3):
        _task(client, f"Filler {i}", assigned_agent_id=capable["id"])
    task = _task(client, "Needs rare skill",
                 required_capability="rare-skill")
    payload = client.post(f"/api/tasks/{task['id']}/route",
                          headers=OPERATOR).json()
    assert payload["decision"]["chosen_agent_id"] == capable["id"]


def test_route_with_no_eligible_agent_stays_queued(client):
    _capability(client, "orphan-skill", "Orphan Skill")
    _agent(client, "Unskilled Agent")
    task = _task(client, "Impossible task",
                 required_capability="orphan-skill")
    payload = client.post(f"/api/tasks/{task['id']}/route",
                          headers=OPERATOR).json()
    assert payload["decision"]["outcome"] == "unrouted"
    assert payload["decision"]["chosen_agent_id"] is None
    assert "no eligible agent" in payload["decision"]["reason"]
    assert payload["task"]["status"] == "queued"
    assert len(payload["task"]["routing_decisions"]) == 1


def test_route_respects_team_restriction(client):
    _capability(client, "team-skill", "Team Skill")
    member = _agent(client, "Team Member Agent", ["team-skill"])
    outsider = _agent(client, "Outside Agent", ["team-skill"])
    team = client.post("/api/teams",
                       json={"display_name": "Routing Team"},
                       headers=OWNER).json()
    client.post(f"/api/teams/{team['id']}/members",
                json={"agent_id": member["id"]}, headers=OPERATOR)
    _task(client, "Member filler", assigned_agent_id=member["id"])
    task = _task(client, "Team task", required_capability="team-skill",
                 assigned_team_id=team["id"])
    payload = client.post(f"/api/tasks/{task['id']}/route",
                          headers=OPERATOR).json()
    assert payload["decision"]["chosen_agent_id"] == member["id"]
    assert payload["decision"]["chosen_agent_id"] != outsider["id"]


def test_manual_assignment_is_routed_at_creation(client):
    agent = _agent(client, "Direct Assignee")
    task = _task(client, "Direct work", assigned_agent_id=agent["id"])
    assert task["status"] == "routed"
    assert task["assigned_agent_id"] == agent["id"]


def test_execution_lifecycle_duration_and_task_status(client):
    agent = _agent(client, "Executor Agent")
    task = _task(client, "Timed task", assigned_agent_id=agent["id"])
    res = client.post(
        "/api/executions",
        json={"task_id": task["id"],
              "started_at": "2026-11-18T09:00:00+00:00",
              "steps": [{"node_key": "start", "title": "Start"},
                        {"node_key": "end", "title": "End"}]},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    execution = res.json()
    assert execution["status"] == "running"
    assert execution["agent_id"] == agent["id"]
    assert [s["node_key"] for s in execution["steps"]] == ["start",
                                                          "end"]
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] \
        == "running"
    res = client.post(
        f"/api/executions/{execution['id']}/finish",
        json={"status": "completed",
              "ended_at": "2026-11-18T09:00:02.500+00:00",
              "outcome_summary": "done"},
        headers=OPERATOR)
    assert res.status_code == 200, res.text
    finished = res.json()
    assert finished["duration_ms"] == 2500
    assert finished["status"] == "completed"
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] \
        == "completed"
    again = client.post(
        f"/api/executions/{execution['id']}/finish",
        json={"status": "completed"}, headers=OPERATOR)
    assert again.status_code == 409


def test_execution_history_is_append_only(client):
    agent = _agent(client, "Retry Agent")
    task = _task(client, "Flaky task", assigned_agent_id=agent["id"])
    first = client.post("/api/executions", json={"task_id": task["id"]},
                        headers=OPERATOR).json()
    client.post(f"/api/executions/{first['id']}/finish",
                json={"status": "failed",
                      "error_detail": "simulated boom"},
                headers=OPERATOR)
    second = client.post("/api/executions",
                         json={"task_id": task["id"]},
                         headers=OPERATOR).json()
    client.post(f"/api/executions/{second['id']}/finish",
                json={"status": "completed"}, headers=OPERATOR)
    history = client.get(f"/api/executions?task_id={task['id']}").json()
    assert [e["id"] for e in history] == [second["id"], first["id"]]
    assert history[1]["status"] == "failed"
    assert history[1]["error_detail"] == "simulated boom"


def test_step_status_transitions(client):
    agent = _agent(client, "Step Agent")
    task = _task(client, "Stepped task", assigned_agent_id=agent["id"])
    execution = client.post(
        "/api/executions",
        json={"task_id": task["id"],
              "steps": [{"node_key": "one"}, {"node_key": "two"}]},
        headers=OPERATOR).json()
    step_one, step_two = execution["steps"]
    res = client.post(
        f"/api/executions/{execution['id']}/steps/{step_one['id']}",
        json={"status": "running"}, headers=OPERATOR)
    assert res.status_code == 200
    res = client.post(
        f"/api/executions/{execution['id']}/steps/{step_one['id']}",
        json={"status": "failed", "detail": "broke"},
        headers=OPERATOR)
    updated = {s["node_key"]: s for s in res.json()["steps"]}
    assert updated["one"]["status"] == "failed"
    assert updated["one"]["detail"] == "broke"
    assert updated["one"]["started_at"] is not None
    assert updated["one"]["ended_at"] is not None
    assert updated["two"]["status"] == "pending"
    bad = client.post(
        f"/api/executions/{execution['id']}/steps/{step_two['id']}",
        json={"status": "sideways"}, headers=OPERATOR)
    assert bad.status_code == 422
