"""Workflow Visualizer: definitions are validated, the graph endpoint
returns layout data, and per-execution step states — failed and
blocked kept distinct — are reflected in the graph."""

from conftest import OPERATOR

DEFINITION = {
    "nodes": [{"key": "plan", "label": "Plan"},
              {"key": "build", "label": "Build"},
              {"key": "review", "label": "Review"}],
    "transitions": [{"from": "plan", "to": "build"},
                    {"from": "build", "to": "review"}],
}


def _workflow(client, name="Visual Workflow", definition=None):
    res = client.post(
        "/api/workflows",
        json={"display_name": name,
              "definition": definition or DEFINITION},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def test_graph_layout_data(client):
    workflow = _workflow(client)
    graph = client.get(f"/api/workflows/{workflow['id']}/graph").json()
    nodes = {n["key"]: n for n in graph["nodes"]}
    assert set(nodes) == {"plan", "build", "review"}
    # Layered layout: later steps sit further right.
    assert nodes["plan"]["x"] < nodes["build"]["x"] \
        < nodes["review"]["x"]
    assert graph["edges"] == [
        {"from": "plan", "to": "build", "label": ""},
        {"from": "build", "to": "review", "label": ""},
    ]
    assert all(n["status"] is None for n in graph["nodes"])
    assert graph["width"] > nodes["review"]["x"]
    assert graph["height"] > 0


def test_definition_validation(client):
    bad_transition = {
        "nodes": [{"key": "a"}],
        "transitions": [{"from": "a", "to": "nowhere"}],
    }
    res = client.post("/api/workflows",
                      json={"display_name": "Bad Graph",
                            "definition": bad_transition},
                      headers=OPERATOR)
    assert res.status_code == 422
    duplicate = {
        "nodes": [{"key": "a"}, {"key": "a"}],
        "transitions": [],
    }
    res = client.post("/api/workflows",
                      json={"display_name": "Dup Graph",
                            "definition": duplicate},
                      headers=OPERATOR)
    assert res.status_code == 422
    empty = client.post("/api/workflows",
                        json={"display_name": "Empty Graph",
                              "definition": {"nodes": []}},
                        headers=OPERATOR)
    assert empty.status_code == 422


def test_duplicate_slug_refused(client):
    _workflow(client, "Graph Once", DEFINITION)
    res = client.post("/api/workflows",
                      json={"display_name": "Graph Once",
                            "definition": DEFINITION},
                      headers=OPERATOR)
    assert res.status_code == 409


def test_graph_reflects_execution_step_states(client):
    workflow = _workflow(client, "Stateful Workflow")
    agent = client.post("/api/agents",
                        json={"display_name": "Graph Agent"},
                        headers=OPERATOR).json()
    task = client.post(
        "/api/tasks",
        json={"title": "Graph task", "assigned_agent_id": agent["id"]},
        headers=OPERATOR).json()
    execution = client.post(
        "/api/executions",
        json={"task_id": task["id"], "workflow_id": workflow["id"]},
        headers=OPERATOR).json()
    # Steps were created from the workflow definition.
    assert [s["node_key"] for s in execution["steps"]] == [
        "plan", "build", "review"]
    steps = {s["node_key"]: s for s in execution["steps"]}
    client.post(
        f"/api/executions/{execution['id']}/steps/{steps['plan']['id']}",
        json={"status": "completed"}, headers=OPERATOR)
    client.post(
        f"/api/executions/{execution['id']}/steps/{steps['build']['id']}",
        json={"status": "failed"}, headers=OPERATOR)
    client.post(
        f"/api/executions/{execution['id']}/steps/{steps['review']['id']}",
        json={"status": "blocked"}, headers=OPERATOR)
    graph = client.get(
        f"/api/workflows/{workflow['id']}/graph"
        f"?execution_id={execution['id']}").json()
    states = {n["key"]: n["status"] for n in graph["nodes"]}
    assert states == {"plan": "completed", "build": "failed",
                      "review": "blocked"}
    # Failed and blocked are distinct states, not one lumped bucket.
    assert states["build"] != states["review"]
