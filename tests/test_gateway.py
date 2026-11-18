"""Gateway Control: the secret guard refuses credential-shaped
values, ordinary pointers pass, and health status moves only on
recorded health evidence."""

from conftest import OPERATOR, VIEWER


def _connection(client, name="Gateway One", **overrides):
    body = {"display_name": name, "kind": "model",
            "provider": "example-provider",
            "config_ref": "vault://gateway/example"}
    body.update(overrides)
    res = client.post("/api/connections", json=body, headers=OPERATOR)
    assert res.status_code == 201, res.text
    return res.json()


def test_secret_guard_rejects_credential_shapes(client):
    attempts = [
        {"config_ref": "sk-1234567890abcdef1234567890"},
        {"config_ref": "sk-ant-abcdefghijklmnopqrstuvwxyz"},
        {"config_ref": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"},
        {"config_notes": "api_key: hunter2hunter2"},
        {"config_notes": "access_token=abc123"},
        {"access_boundary": "-----BEGIN RSA PRIVATE KEY-----"},
        {"provider": "AKIAIOSFODNN7EXAMPLE"},
        {"config_notes": "use Bearer abcdefghijklmnopqrstuvwxyz"},
    ]
    for i, override in enumerate(attempts):
        body = {"display_name": f"Guarded {i}", "kind": "api"}
        body.update(override)
        res = client.post("/api/connections", json=body,
                          headers=OPERATOR)
        assert res.status_code == 422, (override, res.text)
        assert "credential" in res.json()["detail"]


def test_secret_guard_allows_pointers_and_prose(client):
    connection = _connection(
        client, "Pointer Only",
        config_ref="env:MQ_EXAMPLE_KEY (name only, not the value)",
        config_notes="The key itself lives in the founder's password"
                     " manager; only its name is recorded here.",
        access_boundary="Read-only status calls; may not read"
                        " customer data.")
    assert connection["health_status"] == "configured"
    assert connection["config_ref"].startswith("env:")


def test_patch_is_guarded_too(client):
    connection = _connection(client, "Patch Guarded")
    res = client.patch(
        f"/api/connections/{connection['id']}",
        json={"config_notes": "secret: p4ssw0rd-value"},
        headers=OPERATOR)
    assert res.status_code == 422
    ok = client.patch(
        f"/api/connections/{connection['id']}",
        json={"access_boundary": "Status reads only."},
        headers=OPERATOR)
    assert ok.status_code == 200
    assert ok.json()["access_boundary"] == "Status reads only."


def test_health_records_move_status_conservatively(client):
    connection = _connection(client, "Health Tracked")
    res = client.post(f"/api/connections/{connection['id']}/health",
                      json={"result": "fail", "detail": "timeout"},
                      headers=OPERATOR)
    assert res.status_code == 201
    after_fail = client.get(
        f"/api/connections/{connection['id']}").json()
    assert after_fail["health_status"] == "failed"
    assert after_fail["last_health_result"] == "fail"
    client.post(f"/api/connections/{connection['id']}/health",
                json={"result": "pass", "detail": "responded"},
                headers=OPERATOR)
    after_pass = client.get(
        f"/api/connections/{connection['id']}").json()
    # A pass restores 'connected' — never jumps straight to verified.
    assert after_pass["health_status"] == "connected"


def test_verified_requires_passing_health_record(client):
    connection = _connection(client, "Needs Proof")
    res = client.post(f"/api/connections/{connection['id']}/status",
                      json={"status": "verified"}, headers=OPERATOR)
    assert res.status_code == 422
    client.post(f"/api/connections/{connection['id']}/health",
                json={"result": "pass"}, headers=OPERATOR)
    res = client.post(f"/api/connections/{connection['id']}/status",
                      json={"status": "verified"}, headers=OPERATOR)
    assert res.status_code == 200, res.text
    assert res.json()["health_status"] == "verified"


def test_operational_and_direct_registration_refused(client):
    connection = _connection(client, "No Operational")
    client.post(f"/api/connections/{connection['id']}/health",
                json={"result": "pass"}, headers=OPERATOR)
    res = client.post(f"/api/connections/{connection['id']}/status",
                      json={"status": "operational"},
                      headers=OPERATOR)
    assert res.status_code == 422
    direct = client.post(
        "/api/connections",
        json={"display_name": "Born Verified",
              "health_status": "verified"},
        headers=OPERATOR)
    assert direct.status_code == 422


def test_gateway_summary_states_secrets_policy(client):
    _connection(client, "Counted Connection")
    summary = client.get("/api/gateway/summary").json()
    assert summary["total_connections"] >= 1
    assert set(summary["counts_by_status"]) == {
        "demo_simulated", "configured", "connected", "verified",
        "operational", "degraded", "disconnected", "failed"}
    assert "no credentials are stored" in summary["secrets_policy"]
    assert client.post("/api/connections",
                       json={"display_name": "Viewer Made"},
                       headers=VIEWER).status_code == 403
