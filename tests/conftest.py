"""Test fixtures — authenticated against the REAL login.

The suite runs against the production auth: three accounts are created
directly in the database (owner via the one-time bootstrap, operator
and viewer created by that owner), and OWNER / OPERATOR / VIEWER are
`Authorization: Bearer` header dicts from real token-mode logins.

The default `client` fixture is pre-authenticated as the OWNER, so the
many read calls (and fixture writes) in the module tests keep working
without naming a role on every line; a per-request Authorization header
overrides the client's default. `raw_client` has no credentials at all,
for tests of the unauthenticated refusals themselves.
"""

import os
import sys
import tempfile

import pytest

# Point the app at a throwaway database BEFORE importing it, and make the
# project root importable.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_TMP = tempfile.mkdtemp(prefix="cmd-dash-test-")
os.environ["MQ_CMD_DB"] = os.path.join(_TMP, "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.main import app  # noqa: E402

OWNER_PW = "Sunrise-Coffee-42"
OPERATOR_PW = "Harbor-Lantern-73"
VIEWER_PW = "Meadow-Compass-21"


def _seed_accounts() -> None:
    conn = connect()
    try:
        init_db(conn)
        if not auth.owner_exists(conn):
            owner = auth.bootstrap_owner(conn, "test-owner", OWNER_PW,
                                         "Test Owner")
            auth.create_user(conn, "test-operator", OPERATOR_PW, "operator",
                             "Test Operator", owner["username"])
            auth.create_user(conn, "test-viewer", VIEWER_PW, "viewer",
                             "Test Viewer", owner["username"])
    finally:
        conn.close()


def _login_token(client: TestClient, username: str, password: str) -> str:
    res = client.post("/api/auth/login", json={
        "username": username, "password": password, "mode": "token"})
    assert res.status_code == 200, res.text
    return res.json()["token"]


_seed_accounts()
with TestClient(app) as _bootstrap_client:
    OWNER = {"Authorization":
             "Bearer " + _login_token(_bootstrap_client, "test-owner", OWNER_PW)}
    OPERATOR = {"Authorization":
                "Bearer " + _login_token(_bootstrap_client, "test-operator",
                                         OPERATOR_PW)}
    VIEWER = {"Authorization":
              "Bearer " + _login_token(_bootstrap_client, "test-viewer",
                                       VIEWER_PW)}


@pytest.fixture()
def client():
    """Pre-authenticated as the owner (see module docstring)."""
    with TestClient(app, headers=OWNER) as c:
        yield c


@pytest.fixture()
def raw_client():
    """No credentials — for unauthenticated-refusal tests."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def make_agent(client):
    def _make(headers=OPERATOR, **overrides):
        body = {"display_name": "Agent One", "role": "tester"}
        body.update(overrides)
        res = client.post("/api/agents", json=body, headers=headers)
        assert res.status_code == 201, res.text
        return res.json()
    return _make
