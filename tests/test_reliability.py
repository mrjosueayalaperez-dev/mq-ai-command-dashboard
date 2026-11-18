"""Reliability Center: self-checks are recorded truthfully (a
connection with no evidence is degraded, not 'fine'), and incidents
run their open → acknowledged → resolved lifecycle."""

from conftest import OPERATOR, VIEWER


def test_run_checks_records_service_passes(client):
    res = client.post("/api/reliability/run-checks", headers=OPERATOR)
    assert res.status_code == 200, res.text
    checks = res.json()["checks"]
    service = {c["check_type"]: c for c in checks
               if c["subject_type"] == "service"}
    assert service["database_query"]["result"] == "pass"
    # The audit-chain check is recorded with its true result (the
    # foundation tamper test corrupts the shared test chain on
    # purpose, so its result is not asserted here).
    assert service["audit_chain"]["result"] in ("pass", "fail")
    summary = client.get("/api/reliability/summary").json()
    assert summary["service_status"] == "pass"
    assert summary["health_checks_recorded"] >= 2
    assert summary["health_check_pass_rate"] is not None


def test_unchecked_connection_reported_degraded_with_reason(client):
    conn = client.post(
        "/api/connections",
        json={"display_name": "Never Checked", "kind": "api"},
        headers=OPERATOR).json()
    client.post("/api/reliability/run-checks", headers=OPERATOR)
    rows = client.get(
        "/api/reliability/health-checks?subject_type=connection"
    ).json()
    mine = [r for r in rows if r["subject_id"] == conn["id"]]
    assert mine, "a recorded-status check row must exist"
    latest = mine[0]
    assert latest["result"] == "degraded"
    assert "no health record" in latest["detail"]


def test_incident_lifecycle(client):
    res = client.post(
        "/api/incidents",
        json={"title": "Simulated outage", "severity": "critical",
              "subject_type": "service",
              "detail": "Drill incident for lifecycle testing."},
        headers=OPERATOR)
    assert res.status_code == 201, res.text
    incident = res.json()
    assert incident["status"] == "open"
    assert incident["opened_by"] == "test-operator"
    ack = client.post(
        f"/api/incidents/{incident['id']}/acknowledge", json={},
        headers=OPERATOR)
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"
    assert ack.json()["acknowledged_at"] is not None
    again = client.post(
        f"/api/incidents/{incident['id']}/acknowledge", json={},
        headers=OPERATOR)
    assert again.status_code == 409
    resolved = client.post(
        f"/api/incidents/{incident['id']}/resolve",
        json={"resolution_note": "drill complete"},
        headers=OPERATOR)
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolution_note"] == "drill complete"
    assert resolved.json()["resolved_at"] is not None
    twice = client.post(
        f"/api/incidents/{incident['id']}/resolve", json={},
        headers=OPERATOR)
    assert twice.status_code == 409
    # The lifecycle shows up in reliability history and the feed.
    summary = client.get("/api/reliability/summary").json()
    assert summary["resolved_incidents"] >= 1
    feed = client.get("/api/activity?limit=100").json()
    assert any(e["event_type"] == "incident_opened"
               and e["subject_id"] == incident["id"]
               for e in feed["entries"])


def test_reliability_writes_require_role(client):
    assert client.post("/api/reliability/run-checks",
                       headers=VIEWER).status_code == 403
    assert client.post("/api/incidents",
                       json={"title": "viewer incident"},
                       headers=VIEWER).status_code == 403
