"""Real-login coverage: password accounts (argon2id), server-side
sessions (idle + absolute expiry), login throttling/lockout, CSRF on
cookie sessions, bearer-token mode, the owner CLI bootstrap, password
change, user administration rules, and the full two-step (TOTP) flow —
enroll, challenge, wrong code, replayed code, recovery code, fresh
code. Destructive flows use dedicated accounts so the shared role
sessions in conftest are never disturbed."""

import os
import sqlite3
import subprocess
import sys
from datetime import timedelta

import pytest

from conftest import OWNER, _ROOT

from app import auth, totp

PW = "Granite-Willow-3141"
PW2 = "Copper-Harbor-2718"


def _mk_user(client, username, role="viewer", password=PW):
    res = client.post("/api/users", json={
        "username": username, "password": password, "role": role},
        headers=OWNER)
    assert res.status_code == 201, res.text
    return res.json()


def _login(raw_client, username, password=PW, mode="token"):
    return raw_client.post("/api/auth/login", json={
        "username": username, "password": password, "mode": mode})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _wrong_code(secret_b32):
    key = totp._unb32(secret_b32)
    valid = {totp.hotp(key, totp._step() + d) for d in (-1, 0, 1)}
    for candidate in ("000000", "111111", "999999", "123456"):
        if candidate not in valid:
            return candidate
    raise AssertionError("could not pick a wrong code")


# --------------------------------------------------------------- login

def test_login_token_mode_and_me(client, raw_client):
    _mk_user(client, "login-user", role="operator")
    res = _login(raw_client, "login-user")
    assert res.status_code == 200
    body = res.json()
    assert body["token"] and body["token_type"] == "bearer"
    assert body["user"]["username"] == "login-user"
    assert body["user"]["role"] == "operator"
    me = raw_client.get("/api/auth/me", headers=_bearer(body["token"]))
    assert me.status_code == 200
    assert me.json()["session"]["kind"] == "bearer"
    assert me.json()["user"]["username"] == "login-user"


def test_login_failures_are_generic(client, raw_client):
    _mk_user(client, "generic-user")
    bad_pw = _login(raw_client, "generic-user", password="Wrong-Pass-0000")
    assert bad_pw.status_code == 401
    assert bad_pw.json()["detail"] == auth.GENERIC_FAILURE
    unknown = _login(raw_client, "no-such-user", password="Wrong-Pass-0000")
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == auth.GENERIC_FAILURE


def test_login_lockout_and_recovery(client, raw_client, monkeypatch):
    _mk_user(client, "lockme-user")
    for _ in range(auth.USER_FAIL_LIMIT):
        res = _login(raw_client, "lockme-user", password="Wrong-Pass-0000")
        assert res.status_code in (401, 429)
    locked = _login(raw_client, "lockme-user")  # correct password, still locked
    assert locked.status_code == 429
    # Move past the lock window: the correct password works again.
    base = auth._real_clock()
    monkeypatch.setattr(auth, "_clock",
                        lambda: base + timedelta(seconds=auth.LOCK_BASE_SECONDS + 5))
    ok = _login(raw_client, "lockme-user")
    assert ok.status_code == 200


def test_disabled_user_cannot_login(client, raw_client):
    user = _mk_user(client, "disabled-user")
    res = client.post(f"/api/users/{user['id']}/disable", headers=OWNER)
    assert res.status_code == 200
    denied = _login(raw_client, "disabled-user")
    assert denied.status_code == 401
    assert denied.json()["detail"] == auth.GENERIC_FAILURE


# ------------------------------------------------------------- sessions

def test_session_idle_expiry(client, raw_client, monkeypatch):
    _mk_user(client, "idle-user")
    token = _login(raw_client, "idle-user").json()["token"]
    assert raw_client.get("/api/auth/me",
                          headers=_bearer(token)).status_code == 200
    base = auth._real_clock()
    monkeypatch.setattr(auth, "_clock",
                        lambda: base + timedelta(minutes=auth.IDLE_MINUTES + 1))
    assert raw_client.get("/api/auth/me",
                          headers=_bearer(token)).status_code == 401


def test_session_absolute_expiry(client, raw_client, monkeypatch):
    _mk_user(client, "absolute-user")
    token = _login(raw_client, "absolute-user").json()["token"]
    base = auth._real_clock()
    monkeypatch.setattr(auth, "_clock",
                        lambda: base + timedelta(hours=auth.ABSOLUTE_HOURS + 1))
    assert raw_client.get("/api/auth/me",
                          headers=_bearer(token)).status_code == 401


def test_cookie_session_csrf_and_kind_separation(client, raw_client):
    _mk_user(client, "cookie-user", role="operator")
    res = _login(raw_client, "cookie-user", mode="cookie")
    assert res.status_code == 200
    csrf = res.json()["csrf_token"]
    set_cookie = res.headers["set-cookie"]
    assert "mq_cmd_session=" in set_cookie and "HttpOnly" in set_cookie
    cookie_token = set_cookie.split("mq_cmd_session=", 1)[1].split(";", 1)[0]
    cookie_hdr = {"Cookie": f"mq_cmd_session={cookie_token}"}

    me = raw_client.get("/api/auth/me", headers=cookie_hdr)
    assert me.status_code == 200
    assert me.json()["session"]["kind"] == "cookie"
    assert me.json()["csrf_token"] == csrf

    # A cookie session used as a bearer token is refused (kind mismatch),
    # and a bearer token presented as the cookie is refused too.
    assert raw_client.get("/api/auth/me",
                          headers=_bearer(cookie_token)).status_code == 401
    bearer = _login(raw_client, "cookie-user").json()["token"]
    assert raw_client.get(
        "/api/auth/me",
        headers={"Cookie": f"mq_cmd_session={bearer}"}).status_code == 401

    # State-changing cookie requests need the CSRF token.
    refused = raw_client.post("/api/agents",
                              json={"display_name": "CSRF Agent"},
                              headers=cookie_hdr)
    assert refused.status_code == 403
    allowed = raw_client.post(
        "/api/agents", json={"display_name": "CSRF Agent"},
        headers={**cookie_hdr, "X-CSRF-Token": csrf})
    assert allowed.status_code == 201

    # Logout revokes the cookie session.
    out = raw_client.post("/api/auth/logout",
                          headers={**cookie_hdr, "X-CSRF-Token": csrf})
    assert out.status_code == 200
    assert raw_client.get("/api/auth/me",
                          headers=cookie_hdr).status_code == 401


# ---------------------------------------------------------- owner CLI

def _run_cli(db_file, *argv, stdin_text=""):
    env = dict(os.environ)
    env["MQ_CMD_DB"] = str(db_file)
    return subprocess.run(
        [sys.executable, "-m", "app.auth_cli", *argv],
        input=stdin_text, capture_output=True, text=True,
        cwd=_ROOT, env=env, timeout=120)


def test_owner_cli_bootstrap_and_break_glass(tmp_path):
    db_file = tmp_path / "cli.db"
    first = _run_cli(db_file, "create-owner", "--username", "cli-owner",
                     "--password-stdin", stdin_text="Granite-Willow-5150\n")
    assert first.returncode == 0, first.stderr
    assert "created" in first.stdout
    # One-time only: a second bootstrap is refused.
    second = _run_cli(db_file, "create-owner", "--username", "cli-owner-2",
                      "--password-stdin", stdin_text="Granite-Willow-5150\n")
    assert second.returncode == 2
    # The stored credential is an argon2id hash, never the password.
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE username = 'cli-owner'").fetchone()
    assert row["role"] == "owner"
    assert row["password_hash"].startswith("$argon2id$")
    assert "Granite-Willow-5150" not in row["password_hash"]
    assert auth.verify_password(row["password_hash"], "Granite-Willow-5150")
    # Break-glass password recovery works from the shell only.
    reset = _run_cli(db_file, "reset-password", "--username", "cli-owner",
                     "--password-stdin", stdin_text="Copper-Harbor-6151\n")
    assert reset.returncode == 0, reset.stderr
    row = conn.execute("SELECT * FROM users WHERE username = 'cli-owner'").fetchone()
    assert auth.verify_password(row["password_hash"], "Copper-Harbor-6151")
    conn.close()


# -------------------------------------------------------- password flow

def test_password_change_revokes_other_sessions(client, raw_client):
    _mk_user(client, "change-user")
    t1 = _login(raw_client, "change-user").json()["token"]
    t2 = _login(raw_client, "change-user").json()["token"]
    wrong = raw_client.post("/api/auth/password", json={
        "current_password": "Wrong-Pass-0000", "new_password": PW2},
        headers=_bearer(t1))
    assert wrong.status_code == 403
    ok = raw_client.post("/api/auth/password", json={
        "current_password": PW, "new_password": PW2}, headers=_bearer(t1))
    assert ok.status_code == 200
    # The other session is signed out; the changing session survives.
    assert raw_client.get("/api/auth/me",
                          headers=_bearer(t2)).status_code == 401
    assert raw_client.get("/api/auth/me",
                          headers=_bearer(t1)).status_code == 200
    assert _login(raw_client, "change-user").status_code == 401
    assert _login(raw_client, "change-user",
                  password=PW2).status_code == 200


def test_user_admin_rules(client, raw_client):
    from conftest import OPERATOR
    # Operators cannot administer users at all.
    assert client.get("/api/users", headers=OPERATOR).status_code == 403
    # Weak passwords and duplicates are refused.
    weak = client.post("/api/users", json={
        "username": "weak-user", "password": "short", "role": "viewer"},
        headers=OWNER)
    assert weak.status_code == 400
    _mk_user(client, "admin-user", role="operator")
    dup = client.post("/api/users", json={
        "username": "admin-user", "password": PW, "role": "viewer"},
        headers=OWNER)
    assert dup.status_code == 400
    # An owner's password cannot be reset through the API (CLI only)...
    owners = [u for u in client.get("/api/users", headers=OWNER).json()
              if u["role"] == "owner"]
    target = next(u for u in owners if u["username"] == "test-owner")
    refused = client.post(f"/api/users/{target['id']}/password",
                          json={"new_password": PW2}, headers=OWNER)
    assert refused.status_code == 403
    # ...but a non-owner's can, and the new password signs in.
    op = next(u for u in client.get("/api/users", headers=OWNER).json()
              if u["username"] == "admin-user")
    done = client.post(f"/api/users/{op['id']}/password",
                       json={"new_password": PW2}, headers=OWNER)
    assert done.status_code == 200
    assert _login(raw_client, "admin-user",
                  password=PW2).status_code == 200


# ------------------------------------------------------------- two-step

def test_totp_full_flow(client, raw_client, monkeypatch):
    _mk_user(client, "totp-owner", role="owner")
    token = _login(raw_client, "totp-owner").json()["token"]
    hdr = _bearer(token)

    status = raw_client.get("/api/auth/totp", headers=hdr).json()
    assert status["enabled"] is False
    assert status["available_for_role"] is True

    enroll = raw_client.post("/api/auth/totp/enroll", headers=hdr)
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    key = totp._unb32(secret)
    enroll_code = totp.hotp(key, totp._step())
    confirm = raw_client.post("/api/auth/totp/confirm",
                              json={"code": enroll_code}, headers=hdr)
    assert confirm.status_code == 200
    recovery_codes = confirm.json()["recovery_codes"]
    assert len(recovery_codes) == 10
    assert raw_client.get("/api/auth/totp",
                          headers=hdr).json()["enabled"] is True

    # Sign out; the password alone now yields a challenge, never a session.
    raw_client.post("/api/auth/logout", headers=hdr)
    step1 = _login(raw_client, "totp-owner")
    assert step1.status_code == 200
    body = step1.json()
    assert body["two_step_required"] is True
    assert "token" not in body and "csrf_token" not in body

    def verify(**kwargs):
        return raw_client.post("/api/auth/login/verify",
                               json={"challenge": _login(raw_client,
                                                         "totp-owner")
                                     .json()["challenge"], **kwargs})

    # Wrong code: refused.
    assert verify(code=_wrong_code(secret)).status_code == 401
    # The enrollment code was already spent at confirm: replay refused.
    assert verify(code=enroll_code).status_code == 401
    # A recovery code works — once.
    ok = verify(recovery_code=recovery_codes[0])
    assert ok.status_code == 200 and ok.json()["token"]
    assert verify(recovery_code=recovery_codes[0]).status_code == 401
    # A fresh authenticator code (next time step) completes the sign-in.
    base = auth._real_clock()
    monkeypatch.setattr(auth, "_clock",
                        lambda: base + timedelta(seconds=totp.PERIOD + 1))
    fresh = totp.hotp(key, totp._step())
    ok2 = verify(code=fresh)
    assert ok2.status_code == 200 and ok2.json()["token"]


def test_totp_enrollment_is_owner_only(client, raw_client):
    _mk_user(client, "totp-operator", role="operator")
    token = _login(raw_client, "totp-operator").json()["token"]
    res = raw_client.post("/api/auth/totp/enroll", headers=_bearer(token))
    assert res.status_code == 403


# ------------------------------------------------- coverage / auditing

@pytest.mark.parametrize("path", [
    "/api/agents", "/api/teams", "/api/tasks", "/api/executions",
    "/api/communications", "/api/workflows", "/api/connections",
    "/api/activity", "/api/performance", "/api/economics/summary",
    "/api/budgets", "/api/incidents", "/api/governance",
    "/api/reliability/summary", "/api/overview", "/api/audit",
    "/api/users", "/api/auth/me",
])
def test_unauthenticated_refused_across_modules(raw_client, path):
    assert raw_client.get(path).status_code == 401
    # Health is the one open endpoint (it returns no data).
    assert raw_client.get("/api/health").status_code == 200


def test_audit_actor_is_the_session_user(client, raw_client):
    _mk_user(client, "audit-user", role="operator")
    _login(raw_client, "audit-user")
    entries = client.get("/api/audit?limit=100", headers=OWNER).json()
    login_entries = [e for e in entries
                     if e["action"] == "login_success"
                     and e["detail"] and "audit-user" in e["detail"]]
    assert login_entries
    assert all(e["actor"] == "audit-user" for e in login_entries)
