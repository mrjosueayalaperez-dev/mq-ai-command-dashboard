"""Demo-data labeling: the seed is entirely demo_simulated, the overview
marks demo data as demo, and demo/live counts are never blended."""

from conftest import OWNER


def test_seed_and_overview_label_demo(client):
    res = client.post("/api/admin/seed-demo", headers=OWNER)
    assert res.status_code == 200
    assert res.json()["data_source"] == "demo"

    agents = client.get("/api/agents").json()
    demo_agents = [a for a in agents if a["slug"].startswith("demo-")]
    assert len(demo_agents) == 4
    for agent in demo_agents:
        assert agent["data_source"] == "demo"
        assert agent["connection_status"] == "demo_simulated"

    overview = client.get("/api/overview").json()
    assert overview["demo_data_present"] is True
    assert overview["demo_counts_by_status"]["demo_simulated"] >= 4
    # Demo and live are reported separately and sum to the total.
    for status, total in overview["counts_by_status"].items():
        assert (overview["demo_counts_by_status"][status]
                + overview["live_counts_by_status"][status]) == total
    # Nothing anywhere in the overview is presented as operational
    # unless a live agent actually earned it through evidence+approval.
    assert overview["demo_counts_by_status"]["operational"] == 0
    assert overview["demo_counts_by_status"]["verified"] == 0


def test_overview_shape(client):
    overview = client.get("/api/overview").json()
    for key in ("as_of", "data_source", "total_agents",
                "counts_by_status", "teams", "pending_approvals",
                "recent_audit"):
        assert key in overview
    assert set(overview["counts_by_status"]) == {
        "demo_simulated", "configured", "connected", "verified",
        "operational", "degraded", "disconnected", "failed",
    }
