"""Governance Center surface + Intelligence Network graph data."""

from conftest import OPERATOR, OWNER


def test_governance_summary(client):
    client.post("/api/agents", json={"display_name": "Gov Agent"},
                headers=OPERATOR)
    summary = client.get("/api/governance").json()
    assert set(summary["approvals_by_status"]) == {
        "pending", "approved", "rejected", "expired"}
    assert summary["pending_approvals"] \
        == summary["approvals_by_status"]["pending"]
    assert "promote_operational" \
        in summary["consequential_actions_gated"]
    assert "deactivate_agent" in summary["consequential_actions_gated"]
    assert summary["roles"] == ["owner", "operator", "viewer"]
    # The audit block reports the chain state faithfully (the
    # foundation tamper test deliberately corrupts the shared test
    # chain, so only its shape is asserted here; chain-verifies-clean
    # is covered by tests/test_audit.py before the tamper test runs).
    assert set(summary["audit"]) == {"ok", "entries", "broken_at"}
    assert summary["audit"]["entries"] >= 1
    assert isinstance(summary["project_scopes"], list)
    assert summary["project_isolation"]


def test_network_graph_from_registry_and_communications(client):
    alpha = client.post("/api/agents",
                        json={"display_name": "Net Alpha"},
                        headers=OPERATOR).json()
    beta = client.post("/api/agents",
                       json={"display_name": "Net Beta"},
                       headers=OPERATOR).json()
    team = client.post("/api/teams",
                       json={"display_name": "Net Team"},
                       headers=OWNER).json()
    client.post(f"/api/teams/{team['id']}/members",
                json={"agent_id": alpha["id"]}, headers=OPERATOR)
    for _ in range(2):
        client.post("/api/communications",
                    json={"from_agent_id": alpha["id"],
                          "to_agent_id": beta["id"],
                          "summary": "ping"},
                    headers=OPERATOR)
    graph = client.get("/api/network-graph").json()
    agent_ids = {a["id"] for a in graph["agents"]}
    assert {alpha["id"], beta["id"]} <= agent_ids
    node = [a for a in graph["agents"] if a["id"] == alpha["id"]][0]
    assert node["data_source"] == "live"
    assert node["connection_status"] == "demo_simulated"
    assert {"team_id": team["id"], "agent_id": alpha["id"]} \
        in graph["memberships"]
    edges = {(e["from_agent_id"], e["to_agent_id"]): e["count"]
             for e in graph["communication_edges"]}
    assert edges[(alpha["id"], beta["id"])] == 2
