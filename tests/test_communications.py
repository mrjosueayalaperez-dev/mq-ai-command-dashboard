"""Communication Hub: message records keep their task context,
participants, and timestamps; supervision filters work; every record
lands in the audit trail."""

from conftest import OPERATOR, VIEWER


def _agent(client, name):
    res = client.post("/api/agents", json={"display_name": name},
                      headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def test_message_context_integrity(client):
    sender = _agent(client, "Hub Sender")
    receiver = _agent(client, "Hub Receiver")
    task = client.post(
        "/api/tasks",
        json={"title": "Discussed task",
              "assigned_agent_id": receiver["id"]},
        headers=OPERATOR).json()
    res = client.post(
        "/api/communications",
        json={"task_id": task["id"], "from_agent_id": sender["id"],
              "to_agent_id": receiver["id"], "channel": "dashboard",
              "summary": "Status check",
              "body": "How is the discussed task going?",
              "context_ref": "thread://discussed-task",
              "occurred_at": "2026-11-18T12:00:00+00:00"},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    comm = res.json()
    assert comm["task_id"] == task["id"]
    assert comm["from_agent_id"] == sender["id"]
    assert comm["to_agent_id"] == receiver["id"]
    assert comm["from_agent"]["slug"] == sender["slug"]
    assert comm["to_agent"]["slug"] == receiver["slug"]
    assert comm["occurred_at"] == "2026-11-18T12:00:00+00:00"
    assert comm["context_ref"] == "thread://discussed-task"
    # Supervision view: the same record, found by its task context.
    by_task = client.get(
        f"/api/communications?task_id={task['id']}").json()
    assert [c["id"] for c in by_task] == [comm["id"]]
    # And it is in the audit trail.
    audit = client.get("/api/audit?limit=100").json()
    assert any(e["action"] == "record_communication"
               and e["target_id"] == comm["id"] for e in audit)


def test_human_sender_and_agent_filter(client):
    receiver = _agent(client, "Hub Human Target")
    res = client.post(
        "/api/communications",
        json={"to_agent_id": receiver["id"], "from_actor": "founder",
              "summary": "Human instruction"},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    comm = res.json()
    assert comm["from_agent_id"] is None
    assert comm["from_actor"] == "founder"
    by_agent = client.get(
        f"/api/communications?agent_id={receiver['id']}").json()
    assert any(c["id"] == comm["id"] for c in by_agent)


def test_communication_validation(client):
    receiver = _agent(client, "Hub Validated")
    empty = client.post("/api/communications",
                        json={"to_agent_id": receiver["id"]},
                        headers=OPERATOR)
    assert empty.status_code == 422
    bad_task = client.post(
        "/api/communications",
        json={"task_id": 999999, "to_agent_id": receiver["id"],
              "summary": "lost"},
        headers=OPERATOR)
    assert bad_task.status_code == 404
    bad_agent = client.post(
        "/api/communications",
        json={"from_agent_id": 999999, "summary": "ghost"},
        headers=OPERATOR)
    assert bad_agent.status_code == 404
    assert client.post(
        "/api/communications",
        json={"to_agent_id": receiver["id"], "summary": "hi"},
        headers=VIEWER).status_code == 403
