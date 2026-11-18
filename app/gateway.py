"""Gateway Control (module 6): the registry of approved model/tool
connections.

Connections are configuration records: a name, a kind, a pointer to
where real configuration lives (``config_ref``), and written access
boundaries. **No credential is ever stored here.** Every text field
that could carry configuration is scanned by a secret guard that
refuses values shaped like API keys, tokens, or private keys — the
rule is enforced in code, not by convention, and the summary endpoint
states it on the record.

Health is recorded, never assumed: a connection's health status moves
only from recorded health checks. A passing check can restore a failed
connection to ``connected`` — never directly to ``verified``; that
promotion requires a passing health record on file and an explicit
status decision, mirroring the foundation's evidence rule.
"""

import re
import sqlite3

from fastapi import HTTPException

from . import audit, events
from .db import utcnow
from .services import slugify
from .taxonomy import STATUS_SET, requires_approval, requires_evidence

CONNECTION_KINDS = ("model", "tool", "api", "webhook", "repo",
                    "messaging", "other")

# Patterns that mark a value as a probable credential. Deliberately
# specific (known vendor prefixes, assignment shapes, PEM markers) so
# ordinary prose and pointers like "vault://gateway/openai" pass.
SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.]{16,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|secret|access[_-]?token|"
               r"auth[_-]?token|password|passwd)\s*[:=]\s*\S+"),
)

GUARDED_FIELDS = ("display_name", "provider", "config_ref",
                  "config_notes", "access_boundary")


def assert_no_secret(value, field: str) -> None:
    if value is None:
        return
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise HTTPException(
                422,
                f"field '{field}' appears to contain a credential;"
                " credentials are never stored in Gateway Control —"
                " store a pointer (config_ref) to where the secret"
                " lives instead",
            )


def _check_guarded(body: dict) -> None:
    for field in GUARDED_FIELDS:
        if field in body:
            assert_no_secret(body[field], field)


def _connection_detail(conn, connection_id: int) -> dict:
    row = conn.execute("SELECT * FROM connections WHERE id = ?",
                       (connection_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "connection not found")
    connection = dict(row)
    records = conn.execute(
        "SELECT * FROM connection_health_records"
        " WHERE connection_id = ? ORDER BY id DESC",
        (connection_id,)).fetchall()
    connection["health_records"] = [dict(r) for r in records]
    connection["last_health_result"] = (
        records[0]["result"] if records else None)
    return connection


def get_connection(conn, connection_id: int) -> dict:
    return _connection_detail(conn, connection_id)


def list_connections(conn) -> list[dict]:
    return [_connection_detail(conn, r["id"]) for r in conn.execute(
        "SELECT id FROM connections ORDER BY display_name")]


def create_connection(conn, identity, body: dict) -> dict:
    if not body.get("display_name"):
        raise HTTPException(422, "display_name is required")
    _check_guarded(body)
    kind = body.get("kind", "api")
    if kind not in CONNECTION_KINDS:
        raise HTTPException(422, f"kind must be one of"
                                 f" {CONNECTION_KINDS}")
    status = body.get("health_status", "configured")
    if status not in STATUS_SET:
        raise HTTPException(422, f"health_status must be one of"
                                 f" {sorted(STATUS_SET)}")
    if requires_evidence(status) or requires_approval(status):
        raise HTTPException(
            422,
            f"a connection cannot be registered directly in status"
            f" '{status}'; register it as configured and promote it"
            f" only on recorded health evidence",
        )
    data_source = body.get("data_source", "live")
    if data_source not in ("live", "demo"):
        raise HTTPException(422, "data_source must be 'live' or 'demo'")
    slug = body.get("slug") or slugify(body["display_name"])
    now = utcnow()
    try:
        cur = conn.execute(
            "INSERT INTO connections (slug, display_name, kind,"
            " provider, config_ref, config_notes, access_boundary,"
            " health_status, data_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (slug, body["display_name"], kind,
             body.get("provider", ""), body.get("config_ref", ""),
             body.get("config_notes", ""),
             body.get("access_boundary", ""), status, data_source,
             now, now),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"connection slug '{slug}' already"
                                 " exists")
    connection_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "create_connection",
                 "connection", connection_id,
                 {"fields": ["slug", "kind", "health_status"]})
    conn.commit()
    return _connection_detail(conn, connection_id)


def update_connection(conn, identity, connection_id: int,
                      body: dict) -> dict:
    connection = _connection_detail(conn, connection_id)
    _check_guarded(body)
    if "health_status" in body:
        raise HTTPException(
            422, "health_status changes go through health records and"
                 " POST /api/connections/{id}/status, not PATCH")
    changed, sets, values = [], [], []
    for field in ("display_name", "kind", "provider", "config_ref",
                  "config_notes", "access_boundary"):
        if field in body and body[field] != connection[field]:
            if field == "kind" and body[field] not in CONNECTION_KINDS:
                raise HTTPException(422, f"kind must be one of"
                                         f" {CONNECTION_KINDS}")
            sets.append(f"{field} = ?")
            values.append(body[field])
            changed.append(field)
    if not changed:
        return connection
    sets.append("updated_at = ?")
    values.append(utcnow())
    values.append(connection_id)
    conn.execute(f"UPDATE connections SET {', '.join(sets)}"
                 " WHERE id = ?", values)
    audit.append(conn, identity.actor, "update_connection",
                 "connection", connection_id, {"fields": changed})
    conn.commit()
    return _connection_detail(conn, connection_id)


def has_passing_health(conn, connection_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM connection_health_records"
        " WHERE connection_id = ? AND result = 'pass'",
        (connection_id,)).fetchone()
    if row["n"]:
        return True
    ev = conn.execute(
        "SELECT COUNT(*) AS n FROM evidence_records"
        " WHERE target_type = 'connection' AND target_id = ?"
        " AND result = 'pass'", (connection_id,)).fetchone()
    return ev["n"] > 0


def record_health(conn, identity, connection_id: int,
                  body: dict) -> dict:
    connection = _connection_detail(conn, connection_id)
    result = body.get("result", "")
    if result not in ("pass", "fail", "degraded"):
        raise HTTPException(422, "result must be pass, fail, or"
                                 " degraded")
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO connection_health_records (connection_id, result,"
        " detail, checked_by, checked_at, data_source, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (connection_id, result, body.get("detail", ""), identity.actor,
         now, connection["data_source"], now),
    )
    record_id = int(cur.lastrowid)
    # Conservative status movement from recorded evidence only:
    # fail -> failed, degraded -> degraded, pass restores a down
    # connection to connected — never to verified/operational.
    new_status = connection["health_status"]
    if result == "fail":
        new_status = "failed"
    elif result == "degraded" \
            and connection["health_status"] != "failed":
        new_status = "degraded"
    elif result == "pass" \
            and connection["health_status"] in (
                "failed", "disconnected", "degraded"):
        new_status = "connected"
    conn.execute(
        "UPDATE connections SET health_status = ?, last_checked_at = ?,"
        " updated_at = ? WHERE id = ?",
        (new_status, now, now, connection_id))
    audit.append(conn, identity.actor, "record_connection_health",
                 "connection", connection_id,
                 {"fields": ["health"], "result": result,
                  "health_status": new_status})
    events.record_event(
        conn, "connection_health_recorded", "connection", connection_id,
        detail=f"health check {result}: {body.get('detail', '')}",
        severity="warning" if result == "fail" else "info",
        data_source=connection["data_source"],
        source_label="Gateway Control")
    conn.commit()
    return dict(conn.execute(
        "SELECT * FROM connection_health_records WHERE id = ?",
        (record_id,)).fetchone())


def set_connection_status(conn, identity, connection_id: int,
                          body: dict) -> dict:
    connection = _connection_detail(conn, connection_id)
    status = body.get("status", "")
    if status not in STATUS_SET:
        raise HTTPException(422, f"status must be one of"
                                 f" {sorted(STATUS_SET)}")
    if requires_approval(status):
        raise HTTPException(
            422,
            "status 'operational' is a consequential promotion; the"
            " governance approval flow currently covers agents, so a"
            " connection cannot be promoted to operational in this"
            " build — record passing health evidence and use"
            " 'verified' for a tested connection",
        )
    if requires_evidence(status) \
            and not has_passing_health(conn, connection_id):
        raise HTTPException(
            422,
            f"status '{status}' requires a passing health record for"
            f" this connection; none exists — record one via POST"
            f" /api/connections/{connection_id}/health first",
        )
    conn.execute(
        "UPDATE connections SET health_status = ?, updated_at = ?"
        " WHERE id = ?", (status, utcnow(), connection_id))
    audit.append(conn, identity.actor, "set_connection_status",
                 "connection", connection_id,
                 {"fields": ["health_status"], "to": status})
    conn.commit()
    return _connection_detail(conn, connection_id)


def gateway_summary(conn) -> dict:
    counts = {status: 0 for status in STATUS_SET}
    for row in conn.execute(
            "SELECT health_status, COUNT(*) AS n FROM connections"
            " GROUP BY health_status"):
        counts[row["health_status"]] = row["n"]
    return {
        "as_of": utcnow(),
        "total_connections": sum(counts.values()),
        "counts_by_status": counts,
        "secrets_policy": "no credentials are stored in Gateway"
                          " Control; config_ref holds pointers only,"
                          " and secret-shaped values are refused by"
                          " the gateway guard",
    }
