"""Approval flow: consequential actions take effect only through a
pending approval decided by an owner."""

from conftest import OPERATOR, OWNER


def _request(client, action_type, agent_id, headers=OPERATOR):
    res = client.post(
        "/api/approvals",
        json={"action_type": action_type, "target_id": agent_id,
              "reason": "foundation test"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _decide(client, approval_id, decision, headers=OWNER):
    return client.post(
        f"/api/approvals/{approval_id}/decide",
        json={"decision": decision}, headers=headers,
    )


def test_deactivation_requires_approval_flow(client, make_agent):
    agent = make_agent(display_name="Approval Agent A")
    # Direct routes refuse.
    res = client.post(f"/api/agents/{agent['id']}/deactivate",
                      headers=OWNER)
    assert res.status_code == 422
    res = client.delete(f"/api/agents/{agent['id']}", headers=OWNER)
    assert res.status_code == 422
    res = client.patch(f"/api/agents/{agent['id']}",
                       json={"lifecycle_status": "deactivated"},
                       headers=OWNER)
    assert res.status_code == 422
    # Request creates a pending record; nothing is applied yet.
    ap = _request(client, "deactivate_agent", agent["id"])
    assert ap["status"] == "pending"
    got = client.get(f"/api/agents/{agent['id']}").json()
    assert got["lifecycle_status"] == "active"
    # Owner approval applies it.
    decided = _decide(client, ap["id"], "approved")
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    assert decided.json()["decided_by"] == "test-owner"
    got = client.get(f"/api/agents/{agent['id']}").json()
    assert got["lifecycle_status"] == "deactivated"


def test_rejected_approval_applies_nothing(client, make_agent):
    agent = make_agent(display_name="Approval Agent B")
    ap = _request(client, "deactivate_agent", agent["id"])
    decided = _decide(client, ap["id"], "rejected")
    assert decided.status_code == 200
    got = client.get(f"/api/agents/{agent['id']}").json()
    assert got["lifecycle_status"] == "active"


def test_promote_operational_blocked_without_evidence(client, make_agent):
    agent = make_agent(display_name="Approval Agent C")
    ap = _request(client, "promote_operational", agent["id"])
    res = _decide(client, ap["id"], "approved")
    assert res.status_code == 422
    assert "evidence" in res.json()["detail"].lower()
    # Approval remains pending and the agent is unchanged.
    pending = client.get("/api/approvals?status=pending").json()
    assert any(a["id"] == ap["id"] for a in pending)
    got = client.get(f"/api/agents/{agent['id']}").json()
    assert got["connection_status"] == "demo_simulated"


def test_promote_operational_with_evidence_and_approval(client, make_agent):
    agent = make_agent(display_name="Approval Agent D")
    ev = client.post(
        "/api/evidence",
        json={"target_id": agent["id"], "result": "pass",
              "detail": "end-to-end check passed"},
        headers=OPERATOR,
    )
    assert ev.status_code == 201
    ap = _request(client, "promote_operational", agent["id"])
    decided = _decide(client, ap["id"], "approved")
    assert decided.status_code == 200
    got = client.get(f"/api/agents/{agent['id']}").json()
    assert got["connection_status"] == "operational"


def test_decided_approval_cannot_be_decided_again(client, make_agent):
    agent = make_agent(display_name="Approval Agent E")
    ap = _request(client, "deactivate_agent", agent["id"])
    assert _decide(client, ap["id"], "rejected").status_code == 200
    assert _decide(client, ap["id"], "approved").status_code == 409


def test_unknown_action_type_refused(client, make_agent):
    agent = make_agent(display_name="Approval Agent F")
    res = client.post(
        "/api/approvals",
        json={"action_type": "delete_everything",
              "target_id": agent["id"]},
        headers=OPERATOR,
    )
    assert res.status_code == 422
