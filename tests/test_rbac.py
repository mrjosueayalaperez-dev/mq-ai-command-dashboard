"""RBAC over the real login: identity comes from the server-side
session, never from headers. Viewers cannot write; owner-only actions
refuse operators and viewers; no session means 401; X-Role / X-Actor
headers confer nothing."""

from conftest import OPERATOR, OWNER, VIEWER


def test_viewer_cannot_create_agent(client):
    res = client.post("/api/agents", json={"display_name": "Nope"},
                      headers=VIEWER)
    assert res.status_code == 403


def test_unauthenticated_is_refused(raw_client):
    # No session at all: reads and writes alike are 401 (deny-by-default).
    assert raw_client.get("/api/agents").status_code == 401
    assert raw_client.get("/api/overview").status_code == 401
    res = raw_client.post("/api/agents", json={"display_name": "Nope"})
    assert res.status_code == 401


def test_role_headers_confer_nothing(raw_client):
    # The old stand-in is dead: claiming a role by header is not a login.
    res = raw_client.post(
        "/api/agents", json={"display_name": "Nope"},
        headers={"X-Role": "owner", "X-Actor": "spoofed"})
    assert res.status_code == 401
    assert raw_client.get(
        "/api/agents",
        headers={"X-Role": "owner", "X-Actor": "spoofed"},
    ).status_code == 401


def test_spoofed_role_header_does_not_escalate(client, make_agent):
    # A viewer session stays a viewer even with X-Role: owner attached.
    agent = make_agent(display_name="Spoof Agent")
    ap = client.post(
        "/api/approvals",
        json={"action_type": "deactivate_agent",
              "target_id": agent["id"], "reason": "test"},
        headers=OPERATOR,
    )
    assert ap.status_code == 201
    res = client.post(
        f"/api/approvals/{ap.json()['id']}/decide",
        json={"decision": "approved"},
        headers={**VIEWER, "X-Role": "owner", "X-Actor": "spoofed"},
    )
    assert res.status_code == 403


def test_viewer_cannot_patch_or_set_status(client, make_agent):
    agent = make_agent(display_name="RBAC Agent")
    res = client.patch(f"/api/agents/{agent['id']}",
                       json={"role": "x"}, headers=VIEWER)
    assert res.status_code == 403
    res = client.post(f"/api/agents/{agent['id']}/status",
                      json={"status": "configured"}, headers=VIEWER)
    assert res.status_code == 403


def test_viewer_can_read(client, make_agent):
    make_agent(display_name="Readable Agent")
    assert client.get("/api/agents", headers=VIEWER).status_code == 200
    assert client.get("/api/overview", headers=VIEWER).status_code == 200


def test_operator_cannot_decide_approvals(client, make_agent):
    agent = make_agent(display_name="RBAC Approval Agent")
    ap = client.post(
        "/api/approvals",
        json={"action_type": "deactivate_agent",
              "target_id": agent["id"], "reason": "test"},
        headers=OPERATOR,
    )
    assert ap.status_code == 201
    res = client.post(
        f"/api/approvals/{ap.json()['id']}/decide",
        json={"decision": "approved"}, headers=OPERATOR,
    )
    assert res.status_code == 403
    # And the agent is untouched.
    got = client.get(f"/api/agents/{agent['id']}").json()
    assert got["lifecycle_status"] == "active"


def test_operator_cannot_seed_or_wipe_demo(client):
    assert client.post("/api/admin/seed-demo",
                       headers=OPERATOR).status_code == 403
    assert client.post("/api/admin/wipe-demo",
                       headers=OPERATOR).status_code == 403
    assert client.post("/api/admin/seed-demo",
                       headers=VIEWER).status_code == 403


def test_owner_still_works(client):
    assert client.get("/api/users", headers=OWNER).status_code == 200
