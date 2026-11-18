"""Live Activity: the feed labels every entry's source, counts are
reported per source and never blended, and demo entries stay demo."""

from conftest import OPERATOR, OWNER


def test_feed_labels_sources_and_counts_separately(client):
    task = client.post("/api/tasks", json={"title": "Feed task"},
                       headers=OPERATOR)
    assert task.status_code == 201
    client.post("/api/admin/seed-demo", headers=OWNER)
    feed = client.get("/api/activity?limit=100").json()
    counts = feed["counts_by_source"]
    assert set(counts) == {"live", "demo", "system"}
    assert counts["live"] >= 1       # the task_created event above
    assert counts["demo"] >= 1       # seeded demo events
    assert counts["system"] >= 1     # audit-log entries
    # There is no blended total anywhere in the payload.
    assert "total" not in feed
    for entry in feed["entries"]:
        assert entry["data_source"] in ("live", "demo", "system")
        assert entry["source_label"]
        assert entry["occurred_at"]


def test_feed_source_filter(client):
    client.post("/api/admin/seed-demo", headers=OWNER)
    demo_feed = client.get("/api/activity?source=demo&limit=100").json()
    assert demo_feed["entries"]
    assert all(e["data_source"] == "demo"
               for e in demo_feed["entries"])
    system_feed = client.get(
        "/api/activity?source=system&limit=100").json()
    assert system_feed["entries"]
    assert all(e["data_source"] == "system"
               and e["kind"] == "audit"
               for e in system_feed["entries"])


def test_demo_seed_rows_carry_demo_source_everywhere(client):
    client.post("/api/admin/seed-demo", headers=OWNER)
    tasks = client.get("/api/tasks").json()
    demo_tasks = [t for t in tasks if t["data_source"] == "demo"]
    assert len(demo_tasks) == 2
    executions = client.get("/api/executions").json()
    demo_executions = [e for e in executions
                       if e["data_source"] == "demo"]
    assert len(demo_executions) == 2
    comms = client.get("/api/communications?source=demo").json()
    assert len(comms) == 3
    connections = client.get("/api/connections").json()
    demo_conn = [c for c in connections
                 if c["slug"] == "demo-gateway"]
    assert len(demo_conn) == 1
    assert demo_conn[0]["health_status"] == "demo_simulated"
    assert demo_conn[0]["data_source"] == "demo"


def test_wipe_demo_removes_phase2_demo_rows(client):
    client.post("/api/admin/seed-demo", headers=OWNER)
    res = client.post("/api/admin/wipe-demo", headers=OWNER)
    assert res.status_code == 200
    tasks = client.get("/api/tasks").json()
    assert all(t["data_source"] == "live" for t in tasks)
    executions = client.get("/api/executions").json()
    assert all(e["data_source"] == "live" for e in executions)
    feed = client.get("/api/activity?limit=100").json()
    assert feed["counts_by_source"]["demo"] == 0
    # Re-seed so later tests that rely on the demo fixture still pass.
    client.post("/api/admin/seed-demo", headers=OWNER)
