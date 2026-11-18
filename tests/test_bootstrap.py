"""First-run web bootstrap (founder-ordered fix, 2026-11-18).

A fresh install has NO account, so the sign-in page alone was a dead
end: every login failed and the page offered no path forward. These
tests run against a throwaway EMPTY database (the shared conftest
database already has an owner) and cover: the status endpoint's truth
before/after, creation + immediate sign-in, permanent refusal once any
owner exists (however the owner was created), and password policy
identical to the CLI bootstrap path (both call
auth.create_user -> auth.validate_password).
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import audit as audit_mod
from app import auth
from app.db import connect, init_db
from app.main import app

PW = "Granite-Willow-3141"


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    """(client, db_path) against a brand-new, empty database."""
    db = tmp_path / "fresh.db"
    monkeypatch.setenv("MQ_CMD_DB", str(db))
    with TestClient(app) as c:
        yield c, db


def test_bootstrap_status_empty_install(fresh):
    client, _ = fresh
    res = client.get("/api/auth/bootstrap")
    assert res.status_code == 200
    # Exactly one boolean is disclosed — nothing else.
    assert res.json() == {"owner_exists": False}


def test_bootstrap_creates_owner_and_signs_in(fresh):
    client, _ = fresh
    res = client.post("/api/auth/bootstrap",
                      json={"username": "first-owner", "password": PW})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user"]["username"] == "first-owner"
    assert body["user"]["role"] == "owner"
    assert body["csrf_token"]
    assert "mq_cmd_session=" in res.headers["set-cookie"]
    # Signed in: the issued cookie session answers /api/auth/me. (The
    # cookie is Secure, so — like test_auth.py — it is replayed from the
    # Set-Cookie header rather than the client's http cookie jar.)
    cookie_token = res.headers["set-cookie"].split(
        "mq_cmd_session=", 1)[1].split(";", 1)[0]
    me = client.get("/api/auth/me",
                    headers={"Cookie": f"mq_cmd_session={cookie_token}"})
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "first-owner"
    assert me.json()["session"]["kind"] == "cookie"
    # Status flips to true and stays there.
    assert client.get("/api/auth/bootstrap").json() == {"owner_exists": True}


def test_bootstrap_token_mode_returns_usable_bearer(fresh):
    client, _ = fresh
    res = client.post("/api/auth/bootstrap", json={
        "username": "token-owner", "password": PW, "mode": "token"})
    assert res.status_code == 200, res.text
    token = res.json()["token"]
    assert res.json()["token_type"] == "bearer"
    me = client.get("/api/auth/me",
                    headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["session"]["kind"] == "bearer"


def test_bootstrap_refused_once_owner_exists(fresh):
    client, _ = fresh
    first = client.post("/api/auth/bootstrap",
                        json={"username": "first-owner", "password": PW})
    assert first.status_code == 200
    # A second call right after the first is refused...
    again = client.post("/api/auth/bootstrap",
                        json={"username": "second-owner", "password": PW})
    assert again.status_code == 403
    # ...and the second account was NOT created.
    login = client.post("/api/auth/login", json={
        "username": "second-owner", "password": PW, "mode": "token"})
    assert login.status_code == 401


def test_bootstrap_refused_when_cli_created_the_owner(fresh):
    client, db = fresh
    conn = connect(str(db))
    try:
        init_db(conn)
        auth.bootstrap_owner(conn, "cli-owner", PW, "CLI Owner")
    finally:
        conn.close()
    res = client.post("/api/auth/bootstrap",
                      json={"username": "web-owner", "password": PW})
    assert res.status_code == 403
    assert client.get("/api/auth/bootstrap").json() == {"owner_exists": True}


def test_web_bootstrap_closes_the_cli_path_too(fresh):
    client, db = fresh
    res = client.post("/api/auth/bootstrap",
                      json={"username": "web-owner", "password": PW})
    assert res.status_code == 200
    conn = connect(str(db))
    try:
        init_db(conn)
        with pytest.raises(auth.BootstrapRefused):
            auth.bootstrap_owner(conn, "cli-owner", PW)
    finally:
        conn.close()


@pytest.mark.parametrize("bad", [
    "short",                 # under the 12-character minimum
    "x" * 257,               # over the 256-character maximum
    "password123456",        # common password (digits stripped -> "password")
    "aaaaaaaaaaaa1",         # too few distinct characters
])
def test_bootstrap_password_policy_matches_cli(fresh, bad):
    client, _ = fresh
    # The CLI path rejects the same password through the shared policy.
    with pytest.raises(auth.PasswordPolicyError):
        auth.validate_password(bad, "policy-owner")
    res = client.post("/api/auth/bootstrap",
                      json={"username": "policy-owner", "password": bad})
    assert res.status_code == 400, res.text
    # A refused attempt creates nothing: setup is still available.
    assert client.get("/api/auth/bootstrap").json() == {"owner_exists": False}


def test_bootstrap_password_must_not_contain_username(fresh):
    client, _ = fresh
    with pytest.raises(auth.PasswordPolicyError):
        auth.validate_password("Policy-Owner-9999", "policy-owner")
    res = client.post("/api/auth/bootstrap", json={
        "username": "policy-owner", "password": "Policy-Owner-9999"})
    assert res.status_code == 400
    assert client.get("/api/auth/bootstrap").json() == {"owner_exists": False}


def test_bootstrap_bad_username_is_400_and_creates_nothing(fresh):
    client, _ = fresh
    res = client.post("/api/auth/bootstrap",
                      json={"username": "NO SPACES!", "password": PW})
    assert res.status_code == 400
    assert client.get("/api/auth/bootstrap").json() == {"owner_exists": False}


def test_bootstrap_is_audited_with_web_bootstrap_source(fresh):
    client, db = fresh
    res = client.post("/api/auth/bootstrap",
                      json={"username": "audited-owner", "password": PW})
    assert res.status_code == 200
    conn = connect(str(db))
    try:
        init_db(conn)
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action = 'owner_bootstrapped'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["actor"] == "web-bootstrap"
        detail = json.loads(rows[0]["detail"])
        assert detail["source"] == "web-bootstrap"
        assert detail["username"] == "audited-owner"
        assert audit_mod.verify_chain(conn)["ok"] is True
    finally:
        conn.close()
