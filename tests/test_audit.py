"""Audit log: writes are recorded, the hash chain verifies, and direct
tampering with a stored row is detected."""

import os
import sqlite3

from conftest import OPERATOR, OWNER


def test_writes_are_audited(client, make_agent):
    agent = make_agent(display_name="Audit Agent")
    client.patch(f"/api/agents/{agent['id']}", json={"role": "audited"},
                 headers=OPERATOR)
    entries = client.get("/api/audit?limit=100").json()
    actions = [e["action"] for e in entries]
    assert "create_agent" in actions
    assert "update_agent" in actions
    update = next(e for e in entries if e["action"] == "update_agent")
    assert '"role"' in update["detail"]
    assert update["actor"] == "test-operator"


def test_chain_verifies(client, make_agent):
    make_agent(display_name="Chain Agent")
    res = client.get("/api/audit/verify")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["entries"] > 0


def test_tamper_detection(client, make_agent):
    make_agent(display_name="Tamper Agent")
    assert client.get("/api/audit/verify").json()["ok"] is True
    # Rewrite history directly in the database, bypassing the service.
    db_file = os.environ["MQ_CMD_DB"]
    conn = sqlite3.connect(db_file)
    conn.execute(
        "UPDATE audit_log SET detail = '{\"fields\": [\"forged\"]}'"
        " WHERE id = (SELECT MIN(id) FROM audit_log)"
    )
    conn.commit()
    conn.close()
    body = client.get("/api/audit/verify").json()
    assert body["ok"] is False
    assert body["broken_at"] is not None
