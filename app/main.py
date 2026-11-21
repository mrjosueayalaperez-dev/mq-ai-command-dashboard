"""M&Q AI Command Dashboard — HTTP API (FastAPI).

All 11 modules over records this system itself keeps: Agent Registry,
Agent Operations, Orchestration Engine, Communication Hub, Workflow
Visualizer, Gateway Control, Live Activity, Performance Center, Model
Economics, Governance Center, Reliability Center. No external network
calls. No real agent integrations exist in this build — demo data is
labeled demo, and no status is presented as verified/operational
without evidence.

Access is a REAL LOGIN (app/auth.py, app/totp.py): argon2id password
accounts, server-side sessions (HttpOnly cookie for the web UI, bearer
token for the Android app), login throttling, and optional-but-warned
owner two-step login (TOTP). _AuthMiddleware strips any client-supplied
X-Role / X-Actor header and refuses unauthenticated calls to every
non-open /api path — deny by default. The first owner is created only
from the server shell: python -m app.auth_cli create-owner.
"""

import logging
import os
from http.cookies import CookieError, SimpleCookie

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit as audit_mod
from . import auth, totp
from . import economics, events, gateway, governance, hub
from . import reliability, services, work
from .db import connect, init_db
from .rbac import (SESSION_COOKIE, Identity, get_identity, require_owner,
                  require_write)
from .seed import seed_demo, wipe_demo

_log = logging.getLogger("mq_command.main")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, "web")

app = FastAPI(title="M&Q AI Command Dashboard", version="0.3.0-auth")


def get_conn():
    conn = connect()
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


# ------------------------------------------------------------- auth gate

# No session needed: liveness (returns no data), the login flow
# itself (the verify step takes the pending challenge, which is NOT a
# session and opens nothing else), and the first-run bootstrap (open
# only in effect while no owner exists; it refuses permanently after).
OPEN_PATHS = {"/api/health", "/api/auth/login", "/api/auth/login/verify",
              "/api/auth/bootstrap"}
_IDENTITY_HEADERS = {b"x-role", b"x-actor"}  # never trusted from clients
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-csrf-token"


def _hdr(headers, name: bytes) -> str | None:
    for k, v in headers:
        if k.lower() == name:
            return v.decode("latin-1")
    return None


def _presented_token_raw(headers) -> tuple[str | None, str | None]:
    """(token, kind) from raw ASGI headers: Authorization Bearer or the
    session cookie."""
    authz = _hdr(headers, b"authorization")
    if authz:
        scheme, _, value = authz.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip(), "bearer"
        return None, None
    cookie = _hdr(headers, b"cookie")
    if cookie:
        try:
            jar = SimpleCookie()
            jar.load(cookie)
        except CookieError:
            return None, None
        if SESSION_COOKIE in jar:
            return jar[SESSION_COOKIE].value, "cookie"
    return None, None


async def _deny(scope, receive, send, status: int, detail: str):
    headers = {}
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="mq-command"'
    await JSONResponse({"detail": detail}, status_code=status,
                       headers=headers)(scope, receive, send)


class _AuthMiddleware:
    """Authenticate every /api request from a server-side session.

    1. ALWAYS strip client-supplied X-Role / X-Actor (any case) — a
       client cannot assert a role or an audit identity.
    2. Resolve the session from `Authorization: Bearer <token>` or the
       HttpOnly session cookie; a token of the wrong kind is refused.
    3. Cookie sessions: every state-changing request must carry the
       session's CSRF token in X-CSRF-Token, and a cross-site Origin is
       refused. Bearer tokens are not sent automatically by browsers,
       so they are not CSRF-able.
    No session -> 401 (deny by default); OPEN_PATHS excepted. Non-/api
    paths (the static web UI) pass through — the UI holds no data and
    every call it makes is gated here."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api"):
            return await self.inner(scope, receive, send)
        raw = list(scope.get("headers") or [])
        headers = [(k, v) for k, v in raw if k.lower() not in _IDENTITY_HEADERS]
        scope = dict(scope)
        scope["headers"] = headers
        token, kind = _presented_token_raw(headers)
        ident = None
        if token:
            conn = connect()
            try:
                init_db(conn)
                ident = auth.resolve_session(conn, token)
            finally:
                conn.close()
            if ident is not None and ident[1]["kind"] != kind:
                ident = None  # a cookie session used as bearer, or vice versa
        if ident is None:
            if scope["path"] in OPEN_PATHS:
                return await self.inner(scope, receive, send)
            return await _deny(scope, receive, send, 401,
                               "login required — sign in to get a session")
        user, session = ident
        method = scope.get("method", "GET").upper()
        if session["kind"] == "cookie" and method not in _SAFE_METHODS:
            origin = _hdr(headers, b"origin")
            host = _hdr(headers, b"host")
            if origin and host and origin.split("://", 1)[-1] != host:
                return await _deny(scope, receive, send, 403,
                                   "cross-site request refused")
            if not auth.csrf_ok(session, _hdr(headers, CSRF_HEADER.encode())):
                return await _deny(scope, receive, send, 403,
                                   "CSRF token missing or invalid")
        await self.inner(scope, receive, send)


app.add_middleware(_AuthMiddleware)


def _startup_auth_warnings() -> None:
    try:
        conn = connect()
        try:
            init_db(conn)
            if not auth.owner_exists(conn):
                _log.warning(
                    "no owner account exists yet — create the first owner"
                    " from the sign-in page (first-run bootstrap) or from"
                    " the server shell: python -m app.auth_cli"
                    " create-owner --username <name>")
            for name in totp.owners_without_two_step(conn):
                _log.warning(
                    "owner account '%s' has NO two-step login"
                    " (authenticator app). Enroll via POST"
                    " /api/auth/totp/enroll, then"
                    " /api/auth/totp/confirm", name)
        finally:
            conn.close()
    except Exception:  # never let a warning check break startup
        _log.exception("auth startup check failed")


_startup_auth_warnings()


# ---------------------------------------------------------------- health

@app.get("/api/health")
def health():
    return {"ok": True, "product": "M&Q AI Command Dashboard",
            "stage": "full+auth", "data_notice":
            "local records only; no real integrations connected"}


# ------------------------------------------------------------------- auth
# Login, logout, session, two-step, password, owner-only user admin.

def _client_ip(request: Request) -> str:
    # The direct peer. A reverse proxy's X-Forwarded-For is NOT trusted
    # here; a hosted deployment must configure that explicitly.
    return request.client.host if request.client else "unknown"


class LoginIn(BaseModel):
    username: str
    password: str
    mode: str = "cookie"   # "cookie" (web UI) | "token" (Android / API)


def _cookie_path(request: Request) -> str:
    """Session-cookie Path for this deployment. The API lives under
    <root_path>/api: "/api" at the site root, "<prefix>/api" when the
    app is mounted under a path prefix (e.g. "/command" in the combined
    PythonAnywhere deployment). Scoping the cookie to the bare "/api"
    under a prefix would mean the browser never sends it back."""
    return (request.scope.get("root_path") or "").rstrip("/") + "/api"


def _login_response(user: dict, sess: dict, mode: str,
                    cookie_path: str = "/api") -> JSONResponse:
    body = {"user": {k: user[k] for k in ("username", "display_name", "role")},
            "idle_minutes": sess["idle_minutes"],
            "absolute_expires_at": sess["absolute_expires_at"]}
    if mode == "token":
        body["token"] = sess["token"]
        body["token_type"] = "bearer"
        resp = JSONResponse(body)
    else:
        body["csrf_token"] = sess["csrf_token"]
        resp = JSONResponse(body)
        resp.set_cookie(SESSION_COOKIE, sess["token"], httponly=True,
                        secure=True, samesite="strict", path=cookie_path,
                        max_age=auth.ABSOLUTE_HOURS * 3600)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _auth_error(e: auth.AuthError) -> HTTPException:
    headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
    return HTTPException(e.status, e.detail, headers=headers)


class BootstrapIn(BaseModel):
    username: str
    password: str
    mode: str = "cookie"   # "cookie" (web UI) | "token" (Android / API)


@app.get("/api/auth/bootstrap")
def bootstrap_status(response: Response, conn=Depends(get_conn)):
    """First-run status for the sign-in page: does an owner account
    exist yet? Discloses that one boolean and nothing else."""
    response.headers["Cache-Control"] = "no-store"
    return {"owner_exists": auth.owner_exists(conn)}


@app.post("/api/auth/bootstrap")
def bootstrap(body: BootstrapIn, request: Request, conn=Depends(get_conn)):
    """First-run setup: create the FIRST owner from the sign-in page and
    sign them in (the same response a login returns). Refused with 403
    once any owner exists — from then on the owner creates accounts
    inside the app. Password policy is the shared one in app/auth.py,
    identical to the CLI bootstrap."""
    if body.mode not in ("cookie", "token"):
        raise HTTPException(400, "mode must be cookie or token")
    try:
        user = auth.bootstrap_owner_web(conn, body.username, body.password)
    except auth.BootstrapRefused as e:
        raise HTTPException(403, str(e))
    except ValueError as e:  # username rules + PasswordPolicyError
        raise HTTPException(400, str(e))
    kind = "bearer" if body.mode == "token" else "cookie"
    sess = auth.create_session(conn, user, kind)
    return _login_response(user, sess, body.mode, _cookie_path(request))


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, conn=Depends(get_conn)):
    if body.mode not in ("cookie", "token"):
        raise HTTPException(400, "mode must be cookie or token")
    try:
        user = auth.authenticate(conn, body.username, body.password,
                                 _client_ip(request))
    except auth.AuthError as e:
        raise _auth_error(e)
    kind = "bearer" if body.mode == "token" else "cookie"
    if user.get("two_step_required"):
        # Password OK, but this account has two-step login. No session
        # and no cookie yet — only a short-lived pending challenge.
        ch = totp.begin_login(conn, user, kind)
        resp = JSONResponse({"two_step_required": True,
                             "challenge": ch["challenge"],
                             "challenge_expires_in": ch["expires_in"],
                             "methods": ch["methods"],
                             "next": "POST /api/auth/login/verify with the"
                                     " challenge and a 6-digit code or a"
                                     " recovery code"})
        resp.headers["Cache-Control"] = "no-store"
        return resp
    sess = auth.create_session(conn, user, kind)
    return _login_response(user, sess, body.mode, _cookie_path(request))


class LoginVerifyIn(BaseModel):
    challenge: str | None = None
    code: str | None = None
    recovery_code: str | None = None


@app.post("/api/auth/login/verify")
def login_verify(body: LoginVerifyIn, request: Request,
                 conn=Depends(get_conn)):
    """Second sign-in step for two-step accounts. Without a live pending
    challenge this is a plain 401, like any protected route."""
    if not body.challenge:
        raise HTTPException(401, totp.CHALLENGE_INVALID,
                            headers={"WWW-Authenticate":
                                     'Bearer realm="mq-command"'})
    if (body.code is None) == (body.recovery_code is None):
        raise HTTPException(400, "send exactly one of code or recovery_code")
    try:
        user, kind = totp.complete_login(conn, body.challenge, body.code,
                                         body.recovery_code,
                                         _client_ip(request))
    except auth.AuthError as e:
        raise _auth_error(e)
    sess = auth.create_session(conn, user, kind)
    return _login_response(user, sess, "token" if kind == "bearer" else "cookie",
                           _cookie_path(request))


@app.post("/api/auth/logout")
def logout(request: Request, conn=Depends(get_conn),
           identity: Identity = Depends(get_identity)):
    require_write_or_read(identity)
    auth.logout(conn, identity.user, identity.session)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path=_cookie_path(request), secure=True,
                       httponly=True, samesite="strict")
    return resp


def require_write_or_read(identity: Identity) -> Identity:
    if not identity.authenticated:
        raise HTTPException(401, "login required — sign in to get a session")
    return identity


@app.get("/api/auth/me")
def me(response: Response,
       identity: Identity = Depends(get_identity)):
    require_write_or_read(identity)
    user, session = identity.user, identity.session
    response.headers["Cache-Control"] = "no-store"
    out = {"user": {k: user[k] for k in ("username", "display_name", "role")},
           "session": {"kind": session["kind"],
                       "absolute_expires_at": session["absolute_expires_at"],
                       "idle_minutes": auth.IDLE_MINUTES}}
    if session["kind"] == "cookie":
        # Same-origin JS needs this to send X-CSRF-Token after a reload;
        # a cross-site page cannot read it (no CORS is configured).
        out["csrf_token"] = session["csrf_token"]
    conn = connect()
    try:
        init_db(conn)
        two = totp.enabled_for(conn, user["id"])
    finally:
        conn.close()
    out["two_step"] = {"enabled": two}
    if user["role"] == "owner" and not two:
        out["warnings"] = ["owner_two_step_not_enrolled"]
    return out


# ------------------------------------------------------- two-step (TOTP)

def _totp_call(fn):
    try:
        return fn()
    except auth.AuthError as e:
        raise _auth_error(e)


class TotpCodeIn(BaseModel):
    code: str


class TotpDisableIn(BaseModel):
    password: str
    code: str | None = None
    recovery_code: str | None = None


@app.get("/api/auth/totp")
def totp_status(response: Response, conn=Depends(get_conn),
                identity: Identity = Depends(get_identity)):
    require_write_or_read(identity)
    response.headers["Cache-Control"] = "no-store"
    return totp.status(conn, identity.user)       # never the secret


@app.post("/api/auth/totp/enroll")
def totp_enroll(conn=Depends(get_conn),
                identity: Identity = Depends(get_identity)):
    require_write_or_read(identity)
    out = _totp_call(lambda: totp.start_enrollment(conn, identity.user))
    resp = JSONResponse(out)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/auth/totp/confirm")
def totp_confirm(body: TotpCodeIn, conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_write_or_read(identity)
    out = _totp_call(lambda: totp.confirm_enrollment(
        conn, identity.user, body.code, identity.session["token_hash"]))
    resp = JSONResponse(out)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/auth/totp/disable")
def totp_disable(body: TotpDisableIn, request: Request,
                 conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_write_or_read(identity)
    if body.code is not None and body.recovery_code is not None:
        raise HTTPException(400, "send one of code or recovery_code")
    _totp_call(lambda: totp.disable(conn, identity.user, body.password,
                                    body.code, body.recovery_code,
                                    _client_ip(request)))
    return {"ok": True, "two_step": {"enabled": False}}


# -------------------------------------------------------------- password

class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/password")
def change_password(body: PasswordChangeIn, conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_write_or_read(identity)
    try:
        auth.change_own_password(conn, identity.user["id"],
                                 body.current_password, body.new_password,
                                 identity.session["token_hash"])
    except auth.AuthError as e:
        raise HTTPException(e.status, e.detail)
    except auth.PasswordPolicyError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "other_sessions": "signed out"}


# ------------------------------------------------------------ user admin

class UserIn(BaseModel):
    username: str
    password: str
    role: str
    display_name: str | None = None


class RoleIn(BaseModel):
    role: str


class ResetIn(BaseModel):
    new_password: str


def _user_admin(fn):
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:  # includes PasswordPolicyError
        raise HTTPException(400, str(e))


@app.get("/api/users")
def users_list(conn=Depends(get_conn),
               identity: Identity = Depends(get_identity)):
    require_owner(identity)
    return auth.list_users(conn)


@app.post("/api/users", status_code=201)
def users_create(body: UserIn, conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_owner(identity)
    return _user_admin(lambda: auth.create_user(
        conn, body.username, body.password, body.role, body.display_name,
        identity.actor))


@app.post("/api/users/{user_id}/role")
def users_role(user_id: int, body: RoleIn, conn=Depends(get_conn),
               identity: Identity = Depends(get_identity)):
    require_owner(identity)
    return _user_admin(lambda: auth.set_role(conn, user_id, body.role,
                                             identity.actor))


@app.post("/api/users/{user_id}/disable")
def users_disable(user_id: int, conn=Depends(get_conn),
                  identity: Identity = Depends(get_identity)):
    require_owner(identity)
    return _user_admin(lambda: auth.set_disabled(conn, user_id, True,
                                                 identity.actor))


@app.post("/api/users/{user_id}/enable")
def users_enable(user_id: int, conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_owner(identity)
    return _user_admin(lambda: auth.set_disabled(conn, user_id, False,
                                                 identity.actor))


@app.post("/api/users/{user_id}/password")
def users_reset_password(user_id: int, body: ResetIn,
                         conn=Depends(get_conn),
                         identity: Identity = Depends(get_identity)):
    require_owner(identity)
    try:
        _user_admin(lambda: auth.reset_password(conn, user_id,
                                                body.new_password,
                                                identity.actor))
    except PermissionError as e:   # owner targets: server CLI only
        raise HTTPException(403, str(e))
    return {"ok": True, "sessions": "signed out"}


# ------------------------------------------------------------------ teams

@app.get("/api/teams")
def list_teams(conn=Depends(get_conn)):
    return services.list_teams(conn)


@app.post("/api/teams", status_code=201)
def create_team(body: dict, conn=Depends(get_conn),
                identity: Identity = Depends(get_identity)):
    require_write(identity)
    if not body.get("display_name"):
        raise HTTPException(422, "display_name is required")
    return services.create_team(conn, identity, body)


@app.get("/api/teams/{team_id}")
def get_team(team_id: int, conn=Depends(get_conn)):
    return services.get_team(conn, team_id)


@app.post("/api/teams/{team_id}/members")
def add_team_member(team_id: int, body: dict, conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_write(identity)
    if "agent_id" not in body:
        raise HTTPException(422, "agent_id is required")
    return services.add_team_member(conn, identity, team_id, body)


# ------------------------------------------------------------ capabilities

@app.get("/api/capabilities")
def list_capabilities(conn=Depends(get_conn)):
    return services.list_capabilities(conn)


@app.post("/api/capabilities", status_code=201)
def create_capability(body: dict, conn=Depends(get_conn),
                      identity: Identity = Depends(get_identity)):
    require_write(identity)
    if not body.get("display_name"):
        raise HTTPException(422, "display_name is required")
    return services.create_capability(conn, identity, body)


# ------------------------------------------------------------ model configs

@app.get("/api/model-configs")
def list_model_configs(conn=Depends(get_conn)):
    return services.list_model_configs(conn)


@app.post("/api/model-configs", status_code=201)
def create_model_config(body: dict, conn=Depends(get_conn),
                        identity: Identity = Depends(get_identity)):
    require_write(identity)
    for field in ("provider", "model_name", "display_name"):
        if not body.get(field):
            raise HTTPException(422, f"{field} is required")
    return services.create_model_config(conn, identity, body)


# ------------------------------------------------------------------ agents

@app.get("/api/agents")
def list_agents(conn=Depends(get_conn)):
    return services.list_agents(conn)


@app.post("/api/agents", status_code=201)
def create_agent(body: dict, conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_write(identity)
    if not body.get("display_name"):
        raise HTTPException(422, "display_name is required")
    return services.create_agent(conn, identity, body)


@app.get("/api/agents/{agent_id}")
def get_agent(agent_id: int, conn=Depends(get_conn)):
    return services.get_agent(conn, agent_id)


@app.patch("/api/agents/{agent_id}")
def update_agent(agent_id: int, body: dict, conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_write(identity)
    return services.update_agent(conn, identity, agent_id, body)


@app.post("/api/agents/{agent_id}/status")
def set_agent_status(agent_id: int, body: dict, conn=Depends(get_conn),
                     identity: Identity = Depends(get_identity)):
    require_write(identity)
    if not body.get("status"):
        raise HTTPException(422, "status is required")
    return services.set_agent_status(conn, identity, agent_id, body)


@app.post("/api/agents/{agent_id}/deactivate")
def deactivate_agent(agent_id: int, conn=Depends(get_conn),
                     identity: Identity = Depends(get_identity)):
    require_write(identity)
    services.get_agent(conn, agent_id)
    raise HTTPException(
        422,
        "deactivation is a consequential action: request it through"
        " POST /api/approvals (action_type=deactivate_agent) and have"
        " an owner decide it",
    )


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: int, conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_write(identity)
    services.get_agent(conn, agent_id)
    raise HTTPException(
        422,
        "agents are never hard-deleted from the registry; deactivation"
        " is a consequential action handled through POST /api/approvals"
        " (action_type=deactivate_agent)",
    )


@app.post("/api/agents/{agent_id}/model-assignments")
def assign_model(agent_id: int, body: dict, conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_write(identity)
    if "model_config_id" not in body:
        raise HTTPException(422, "model_config_id is required")
    return services.assign_model(conn, identity, agent_id, body)


# ---------------------------------------------------------------- evidence

@app.get("/api/evidence")
def list_evidence(target_id: int | None = None, conn=Depends(get_conn)):
    return services.list_evidence(conn, target_id)


@app.post("/api/evidence", status_code=201)
def create_evidence(body: dict, conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_write(identity)
    if "target_id" not in body:
        raise HTTPException(422, "target_id is required")
    return services.create_evidence(conn, identity, body)


# --------------------------------------------------------------- approvals

@app.get("/api/approvals")
def list_approvals(status: str | None = None, conn=Depends(get_conn)):
    return services.list_approvals(conn, status)


@app.post("/api/approvals", status_code=201)
def create_approval(body: dict, conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_write(identity)
    if "target_id" not in body:
        raise HTTPException(422, "target_id is required")
    return services.create_approval(conn, identity, body)


@app.post("/api/approvals/{approval_id}/decide")
def decide_approval(approval_id: int, body: dict, conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_owner(identity)
    return services.decide_approval(conn, identity, approval_id, body)


# ------------------------------------------------------------------- audit

@app.get("/api/audit")
def list_audit(limit: int = 50, conn=Depends(get_conn)):
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/audit/verify")
def verify_audit(conn=Depends(get_conn)):
    return audit_mod.verify_chain(conn)


# ---------------------------------------------------------------- overview

@app.get("/api/overview")
def overview(conn=Depends(get_conn)):
    return services.overview(conn)


# ------------------------------------------------------------------- admin

@app.post("/api/admin/seed-demo")
def admin_seed_demo(conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_owner(identity)
    result = seed_demo(conn)
    audit_mod.append(conn, identity.actor, "seed_demo", "system", None,
                     {"fields": ["demo_seed"], "agents": result["agents"]})
    conn.commit()
    return result


@app.post("/api/admin/wipe-demo")
def admin_wipe_demo(conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_owner(identity)
    wipe_demo(conn)
    audit_mod.append(conn, identity.actor, "wipe_demo", "system", None,
                     {"fields": ["demo_seed"]})
    conn.commit()
    return {"wiped": True}


# ------------------------------------------- tasks + orchestration (m3)

@app.get("/api/tasks")
def list_tasks(status: str | None = None, agent_id: int | None = None,
               conn=Depends(get_conn)):
    return work.list_tasks(conn, status, agent_id)


@app.post("/api/tasks", status_code=201)
def create_task(body: dict, conn=Depends(get_conn),
                identity: Identity = Depends(get_identity)):
    require_write(identity)
    return work.create_task(conn, identity, body)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, conn=Depends(get_conn)):
    return work.get_task(conn, task_id)


@app.post("/api/tasks/{task_id}/route")
def route_task(task_id: int, conn=Depends(get_conn),
               identity: Identity = Depends(get_identity)):
    require_write(identity)
    return work.route_task(conn, identity, task_id)


@app.post("/api/tasks/{task_id}/status")
def set_task_status(task_id: int, body: dict, conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_write(identity)
    if not body.get("status"):
        raise HTTPException(422, "status is required")
    return work.set_task_status(conn, identity, task_id, body)


# ------------------------------------------------------------- executions

@app.get("/api/executions")
def list_executions(task_id: int | None = None,
                    agent_id: int | None = None,
                    status: str | None = None,
                    conn=Depends(get_conn)):
    return work.list_executions(conn, task_id, agent_id, status)


@app.post("/api/executions", status_code=201)
def create_execution(body: dict, conn=Depends(get_conn),
                     identity: Identity = Depends(get_identity)):
    require_write(identity)
    return work.create_execution(conn, identity, body)


@app.get("/api/executions/{execution_id}")
def get_execution(execution_id: int, conn=Depends(get_conn)):
    return work.get_execution(conn, execution_id)


@app.post("/api/executions/{execution_id}/finish")
def finish_execution(execution_id: int, body: dict,
                     conn=Depends(get_conn),
                     identity: Identity = Depends(get_identity)):
    require_write(identity)
    return work.finish_execution(conn, identity, execution_id, body)


@app.post("/api/executions/{execution_id}/steps/{step_id}")
def set_step_status(execution_id: int, step_id: int, body: dict,
                    conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_write(identity)
    if not body.get("status"):
        raise HTTPException(422, "status is required")
    return work.set_step_status(conn, identity, execution_id, step_id,
                                body)


# ------------------------------------------------- agent operations (m2)

@app.get("/api/operations/agents/{agent_id}")
def agent_operations(agent_id: int, conn=Depends(get_conn)):
    return work.agent_operations(conn, agent_id)


@app.post("/api/operations/assignments", status_code=201)
def create_assignment(body: dict, conn=Depends(get_conn),
                      identity: Identity = Depends(get_identity)):
    require_write(identity)
    if not (body.get("assigned_agent_id")
            or body.get("assigned_team_id")):
        raise HTTPException(
            422, "an assignment needs assigned_agent_id or"
                 " assigned_team_id")
    return work.create_task(conn, identity, body)


@app.post("/api/agents/{agent_id}/events", status_code=201)
def record_agent_event(agent_id: int, body: dict,
                       conn=Depends(get_conn),
                       identity: Identity = Depends(get_identity)):
    require_write(identity)
    return work.record_agent_event(conn, identity, agent_id, body)


@app.post("/api/agents/{agent_id}/pause")
def pause_agent(agent_id: int, conn=Depends(get_conn),
                identity: Identity = Depends(get_identity)):
    require_write(identity)
    return work.set_agent_lifecycle(conn, identity, agent_id, "paused")


@app.post("/api/agents/{agent_id}/resume")
def resume_agent(agent_id: int, conn=Depends(get_conn),
                 identity: Identity = Depends(get_identity)):
    require_write(identity)
    return work.set_agent_lifecycle(conn, identity, agent_id, "active")


# ------------------------------------------------ communications hub (m4)

@app.get("/api/communications")
def list_communications(task_id: int | None = None,
                        agent_id: int | None = None,
                        source: str | None = None,
                        conn=Depends(get_conn)):
    return hub.list_communications(conn, task_id, agent_id, source)


@app.post("/api/communications", status_code=201)
def create_communication(body: dict, conn=Depends(get_conn),
                         identity: Identity = Depends(get_identity)):
    require_write(identity)
    return hub.create_communication(conn, identity, body)


# ------------------------------------------------------ workflows (m5)

@app.get("/api/workflows")
def list_workflows(conn=Depends(get_conn)):
    return hub.list_workflows(conn)


@app.post("/api/workflows", status_code=201)
def create_workflow(body: dict, conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_write(identity)
    return hub.create_workflow(conn, identity, body)


@app.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: int, conn=Depends(get_conn)):
    return hub.get_workflow(conn, workflow_id)


@app.get("/api/workflows/{workflow_id}/graph")
def workflow_graph(workflow_id: int, execution_id: int | None = None,
                   conn=Depends(get_conn)):
    return hub.workflow_graph(conn, workflow_id, execution_id)


@app.get("/api/network-graph")
def network_graph(conn=Depends(get_conn)):
    return hub.network_graph(conn)


# ---------------------------------------------------- gateway control (m6)

@app.get("/api/connections")
def list_connections(conn=Depends(get_conn)):
    return gateway.list_connections(conn)


@app.post("/api/connections", status_code=201)
def create_connection(body: dict, conn=Depends(get_conn),
                      identity: Identity = Depends(get_identity)):
    require_write(identity)
    return gateway.create_connection(conn, identity, body)


@app.get("/api/connections/{connection_id}")
def get_connection(connection_id: int, conn=Depends(get_conn)):
    return gateway.get_connection(conn, connection_id)


@app.patch("/api/connections/{connection_id}")
def update_connection(connection_id: int, body: dict,
                      conn=Depends(get_conn),
                      identity: Identity = Depends(get_identity)):
    require_write(identity)
    return gateway.update_connection(conn, identity, connection_id,
                                     body)


@app.post("/api/connections/{connection_id}/health", status_code=201)
def record_connection_health(connection_id: int, body: dict,
                             conn=Depends(get_conn),
                             identity: Identity = Depends(get_identity)):
    require_write(identity)
    return gateway.record_health(conn, identity, connection_id, body)


@app.post("/api/connections/{connection_id}/status")
def set_connection_status(connection_id: int, body: dict,
                          conn=Depends(get_conn),
                          identity: Identity = Depends(get_identity)):
    require_write(identity)
    if not body.get("status"):
        raise HTTPException(422, "status is required")
    return gateway.set_connection_status(conn, identity, connection_id,
                                         body)


@app.get("/api/gateway/summary")
def gateway_summary(conn=Depends(get_conn)):
    return gateway.gateway_summary(conn)


# ----------------------------------------------------- live activity (m7)

@app.get("/api/activity")
def activity(source: str | None = None, limit: int = 50,
             conn=Depends(get_conn)):
    return events.feed(conn, source, limit)


# ------------------------------------------- performance center (module 8)

@app.get("/api/performance")
def performance(agent_id: int | None = None, conn=Depends(get_conn)):
    return economics.performance(conn, agent_id)


# -------------------------------------------- model economics (module 9)

@app.get("/api/model-prices")
def list_model_prices(conn=Depends(get_conn)):
    return economics.list_prices(conn)


@app.post("/api/model-prices", status_code=201)
def upsert_model_price(body: dict, conn=Depends(get_conn),
                       identity: Identity = Depends(get_identity)):
    require_write(identity)
    return economics.upsert_price(conn, identity, body)


@app.get("/api/usage-records")
def list_usage_records(agent_id: int | None = None,
                       task_id: int | None = None,
                       project_scope: str | None = None,
                       source: str | None = None,
                       conn=Depends(get_conn)):
    return economics.list_usage(conn, agent_id, task_id, project_scope,
                                  source)


@app.post("/api/usage-records", status_code=201)
def create_usage_record(body: dict, conn=Depends(get_conn),
                        identity: Identity = Depends(get_identity)):
    require_write(identity)
    return economics.create_usage(conn, identity, body)


@app.get("/api/economics/summary")
def economics_summary(agent_id: int | None = None,
                      project_scope: str | None = None,
                      conn=Depends(get_conn)):
    return economics.economics_summary(conn, agent_id, project_scope)


@app.get("/api/budgets")
def list_budgets(conn=Depends(get_conn)):
    return economics.list_budgets(conn)


@app.post("/api/budgets", status_code=201)
def create_budget(body: dict, conn=Depends(get_conn),
                  identity: Identity = Depends(get_identity)):
    require_write(identity)
    return economics.create_budget(conn, identity, body)


@app.get("/api/budgets/{budget_id}")
def get_budget(budget_id: int, conn=Depends(get_conn)):
    return economics.get_budget(conn, budget_id)


# ------------------------------------------------------ governance (m10)

@app.get("/api/governance")
def governance_view(conn=Depends(get_conn)):
    return governance.governance_summary(conn)


# ----------------------------------------------------- reliability (m11)

@app.post("/api/reliability/run-checks")
def run_health_checks(conn=Depends(get_conn),
                      identity: Identity = Depends(get_identity)):
    require_write(identity)
    return reliability.run_checks(conn, identity)


@app.get("/api/reliability/health-checks")
def list_health_checks(subject_type: str | None = None,
                       limit: int = 100, conn=Depends(get_conn)):
    return reliability.list_health_checks(conn, subject_type, limit)


@app.get("/api/reliability/summary")
def reliability_summary(conn=Depends(get_conn)):
    return reliability.reliability_summary(conn)


@app.get("/api/incidents")
def list_incidents(status: str | None = None, conn=Depends(get_conn)):
    return reliability.list_incidents(conn, status)


@app.post("/api/incidents", status_code=201)
def create_incident(body: dict, conn=Depends(get_conn),
                    identity: Identity = Depends(get_identity)):
    require_write(identity)
    return reliability.create_incident(conn, identity, body)


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: int, conn=Depends(get_conn)):
    return reliability.get_incident(conn, incident_id)


@app.post("/api/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: int, body: dict | None = None,
                         conn=Depends(get_conn),
                         identity: Identity = Depends(get_identity)):
    require_write(identity)
    return reliability.acknowledge_incident(conn, identity,
                                            incident_id, body or {})


@app.post("/api/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int, body: dict | None = None,
                     conn=Depends(get_conn),
                     identity: Identity = Depends(get_identity)):
    require_write(identity)
    return reliability.resolve_incident(conn, identity, incident_id,
                                        body or {})


# ------------------------------------------------------------- analyst

@app.get("/api/analyst/status")
def analyst_status(conn=Depends(get_conn),
                   identity: Identity = Depends(get_identity)):
    from . import analyst
    return analyst.status(conn)


@app.post("/api/analyst/register")
def analyst_register(conn=Depends(get_conn),
                     identity: Identity = Depends(get_identity)):
    require_owner(identity)
    from . import analyst
    return analyst.register_analyst(conn, identity.actor)


@app.post("/api/analyst/ask")
def analyst_ask(body: dict, conn=Depends(get_conn),
                identity: Identity = Depends(get_identity)):
    require_owner(identity)
    from . import analyst
    return analyst.ask(conn, identity, body.get("question", ""),
                       source=body.get("data_source", "live"))


@app.post("/api/analyst/draft", status_code=201)
def analyst_draft(body: dict, conn=Depends(get_conn),
                  identity: Identity = Depends(get_identity)):
    require_write(identity)
    from . import analyst
    try:
        return analyst.draft_assignment(conn, identity, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# -------------------------------------------------------------- front end

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")

