"""Taxonomy enforcement: the eight states are the only legal values, and
promotion to verified/operational is evidence-gated."""

from conftest import OPERATOR, OWNER
from app.taxonomy import STATUSES


def _set_status(client, agent_id, status, headers=OPERATOR):
    return client.post(
        f"/api/agents/{agent_id}/status",
        json={"status": status}, headers=headers,
    )


def _add_evidence(client, agent_id, result, headers=OPERATOR):
    return client.post(
        "/api/evidence",
        json={"target_id": agent_id, "result": result,
              "kind": "manual_test", "detail": "test evidence"},
        headers=headers,
    )


def test_taxonomy_has_exactly_the_eight_states():
    assert set(STATUSES) == {
        "demo_simulated", "configured", "connected", "verified",
        "operational", "degraded", "disconnected", "failed",
    }


def test_invalid_status_refused(client, make_agent):
    agent = make_agent(display_name="Taxonomy Agent A")
    res = _set_status(client, agent["id"], "sort_of_working")
    assert res.status_code == 422


def test_free_statuses_can_be_set(client, make_agent):
    agent = make_agent(display_name="Taxonomy Agent B")
    for status in ("configured", "connected", "degraded",
                   "disconnected", "failed", "demo_simulated"):
        res = _set_status(client, agent["id"], status)
        assert res.status_code == 200, (status, res.text)
        assert res.json()["connection_status"] == status


def test_verified_refused_without_evidence(client, make_agent):
    agent = make_agent(display_name="Taxonomy Agent C")
    res = _set_status(client, agent["id"], "verified")
    assert res.status_code == 422
    assert "evidence" in res.json()["detail"].lower()
    # Status is unchanged after the refusal.
    got = client.get(f"/api/agents/{agent['id']}").json()
    assert got["connection_status"] == "demo_simulated"


def test_failing_evidence_does_not_unlock_verified(client, make_agent):
    agent = make_agent(display_name="Taxonomy Agent D")
    assert _add_evidence(client, agent["id"], "fail").status_code == 201
    res = _set_status(client, agent["id"], "verified")
    assert res.status_code == 422


def test_passing_evidence_unlocks_verified(client, make_agent):
    agent = make_agent(display_name="Taxonomy Agent E")
    assert _add_evidence(client, agent["id"], "pass").status_code == 201
    res = _set_status(client, agent["id"], "verified")
    assert res.status_code == 200
    assert res.json()["connection_status"] == "verified"


def test_operational_refused_on_direct_endpoint_even_with_evidence(
        client, make_agent):
    agent = make_agent(display_name="Taxonomy Agent F")
    _add_evidence(client, agent["id"], "pass")
    res = _set_status(client, agent["id"], "operational")
    assert res.status_code == 422
    assert "approval" in res.json()["detail"].lower()


def test_cannot_register_directly_as_verified_or_operational(client):
    for status in ("verified", "operational"):
        res = client.post(
            "/api/agents",
            json={"display_name": f"Direct {status}",
                  "connection_status": status},
            headers=OPERATOR,
        )
        assert res.status_code == 422


def test_patch_cannot_change_connection_status(client, make_agent):
    agent = make_agent(display_name="Taxonomy Agent G")
    res = client.patch(
        f"/api/agents/{agent['id']}",
        json={"connection_status": "connected"}, headers=OPERATOR,
    )
    assert res.status_code == 422
