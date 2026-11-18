"""Role-based access control — identity from the real login.

The identity on a request comes ONLY from an authenticated user
account: the server-side session resolved from the ``Authorization:
Bearer`` token (Android / API clients) or the HttpOnly session cookie
(web UI). Client-supplied ``X-Role`` / ``X-Actor`` headers confer
nothing — the auth middleware in main.py strips them before routing,
and this module never reads them.

An unauthenticated request has ``role is None``: write/owner guards
answer 401 (sign in first), and the middleware has already refused
unauthenticated calls to every non-open /api path before any handler
runs (deny-by-default). A signed-in viewer attempting a write gets 403.
"""

from http.cookies import CookieError, SimpleCookie

from fastapi import HTTPException, Request

from . import auth
from .db import connect, init_db

SESSION_COOKIE = "mq_cmd_session"
ROLES = auth.ROLES


class Identity:
    def __init__(self, user: dict | None = None, session: dict | None = None):
        self.user = user
        self.session = session
        self.role = user["role"] if user else None
        # The audit actor is the session's username — never client input.
        self.actor = user["username"] if user else "anonymous"

    @property
    def user_id(self) -> int | None:
        return self.user["id"] if self.user else None

    @property
    def authenticated(self) -> bool:
        return self.user is not None

    @property
    def can_write(self) -> bool:
        return self.role in ("owner", "operator")

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def presented_token(headers) -> tuple[str | None, str | None]:
    """(token, kind) from `Authorization: Bearer` or the session cookie.
    Works on a Starlette Headers mapping (case-insensitive)."""
    authz = headers.get("authorization")
    if authz:
        scheme, _, value = authz.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip(), "bearer"
        return None, None
    cookie = headers.get("cookie")
    if cookie:
        try:
            jar = SimpleCookie()
            jar.load(cookie)
        except CookieError:
            return None, None
        if SESSION_COOKIE in jar:
            return jar[SESSION_COOKIE].value, "cookie"
    return None, None


def resolve_identity(request: Request) -> Identity:
    """Resolve the session on this request to an Identity (anonymous
    when there is no live session of the matching kind)."""
    token, kind = presented_token(request.headers)
    if not token:
        return Identity()
    conn = connect()
    try:
        init_db(conn)
        ident = auth.resolve_session(conn, token)
    finally:
        conn.close()
    if ident is None:
        return Identity()
    user, session = ident
    if session["kind"] != kind:
        return Identity()  # a cookie session used as bearer, or vice versa
    return Identity(user, session)


def get_identity(request: Request) -> Identity:
    return resolve_identity(request)


def require_write(identity: Identity) -> Identity:
    if not identity.authenticated:
        raise HTTPException(
            status_code=401,
            detail="login required — sign in to get a session",
        )
    if not identity.can_write:
        raise HTTPException(
            status_code=403,
            detail="write access requires the operator or owner role",
        )
    return identity


def require_owner(identity: Identity) -> Identity:
    if not identity.authenticated:
        raise HTTPException(
            status_code=401,
            detail="login required — sign in to get a session",
        )
    if not identity.is_owner:
        raise HTTPException(
            status_code=403,
            detail="this action requires the owner role",
        )
    return identity
