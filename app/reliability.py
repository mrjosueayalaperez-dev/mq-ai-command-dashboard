"""Reliability Center (module 11): health checks and incidents.

The service can only check what it can actually reach: its own
database and audit chain, plus the *recorded* status of registered
connections. A connection with no recorded health evidence is
reported as degraded with that reason stated — this module never
pretends to have probed an external system. Incidents are records
with an open → acknowledged → resolved lifecycle; every transition
is audit-logged and appears in the activity feed.
"""

from fastapi import HTTPException

from . import audit, events
from .db import utcnow

CHECK_RESULTS = ("pass", "fail", "degraded")


def run_checks(conn, identity) -> dict:
    """Run the checks this service can truthfully perform and store
    one health_checks row per subject."""
    now = utcnow()
    results = []

    def store(subject_type, subject_id, check_type, result, detail,
              data_source="live"):
        cur = conn.execute(
            "INSERT INTO health_checks (subject_type, subject_id,"
            " check_type, result, detail, checked_at, data_source,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (subject_type, subject_id, check_type, result, detail,
             now, data_source, now))
        row = dict(conn.execute(
            "SELECT * FROM health_checks WHERE id = ?",
            (int(cur.lastrowid),)).fetchone())
        results.append(row)

    # 1. Own database: a real query against the live connection.
    try:
        conn.execute("SELECT 1").fetchone()
        store("service", None, "database_query", "pass",
              "SQLite database answered a query on this connection")
    except Exception as exc:  # pragma: no cover - defensive
        store("service", None, "database_query", "fail",
              f"database query failed: {exc}")

    # 2. Audit chain integrity: recomputed in full.
    from .audit import verify_chain
    chain = verify_chain(conn)
    store("service", None, "audit_chain",
          "pass" if chain["ok"] else "fail",
          f"audit chain verified over {chain['entries']} entries"
          if chain["ok"] else
          f"audit chain broken at entry {chain['broken_at']}")

    # 3. Registered connections: report their RECORDED state only.
    for row in conn.execute(
            "SELECT * FROM connections ORDER BY display_name"):
        last = conn.execute(
            "SELECT result FROM connection_health_records"
            " WHERE connection_id = ? ORDER BY id DESC LIMIT 1",
            (row["id"],)).fetchone()
        if last is None:
            store("connection", row["id"], "recorded_status",
                  "degraded",
                  "no health record has ever been recorded for this"
                  " connection; it has not been tested",
                  data_source=row["data_source"])
        else:
            mapped = {"pass": "pass", "fail": "fail",
                      "degraded": "degraded"}[last["result"]]
            store("connection", row["id"], "recorded_status", mapped,
                  f"latest recorded health check: {last['result']}"
                  f" (recorded status: {row['health_status']})",
                  data_source=row["data_source"])
    audit.append(conn, identity.actor, "run_health_checks", "system",
                 None, {"fields": ["health_checks"],
                        "checks": len(results)})
    conn.commit()
    return {"as_of": now, "checks": results,
            "summary": {
                result: sum(1 for r in results
                            if r["result"] == result)
                for result in CHECK_RESULTS}}


def list_health_checks(conn, subject_type: str | None = None,
                       limit: int = 100) -> list[dict]:
    if subject_type:
        rows = conn.execute(
            "SELECT * FROM health_checks WHERE subject_type = ?"
            " ORDER BY id DESC LIMIT ?", (subject_type, limit))
    else:
        rows = conn.execute(
            "SELECT * FROM health_checks ORDER BY id DESC LIMIT ?",
            (limit,))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- incidents

def create_incident(conn, identity, body: dict) -> dict:
    if not body.get("title"):
        raise HTTPException(422, "title is required")
    severity = body.get("severity", "warning")
    if severity not in ("info", "warning", "critical"):
        raise HTTPException(422, "severity must be info, warning, or"
                                 " critical")
    data_source = body.get("data_source", "live")
    if data_source not in ("live", "demo"):
        raise HTTPException(422, "data_source must be 'live' or 'demo'")
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO incidents (subject_type, subject_id, title,"
        " detail, severity, status, opened_by, opened_at, data_source,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?,"
        " ?, ?, ?)",
        (body.get("subject_type", "system"), body.get("subject_id"),
         body["title"], body.get("detail", ""), severity,
         identity.actor, now, data_source, now, now),
    )
    incident_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "open_incident", "incident",
                 incident_id, {"fields": ["title", "severity"],
                               "severity": severity})
    events.record_event(
        conn, "incident_opened", "incident", incident_id,
        detail=body["title"], severity=severity,
        data_source=data_source, source_label="Reliability Center")
    conn.commit()
    return get_incident(conn, incident_id)


def get_incident(conn, incident_id: int) -> dict:
    row = conn.execute("SELECT * FROM incidents WHERE id = ?",
                       (incident_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "incident not found")
    return dict(row)


def list_incidents(conn, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM incidents WHERE status = ? ORDER BY id DESC",
            (status,))
    else:
        rows = conn.execute("SELECT * FROM incidents ORDER BY id DESC")
    return [dict(r) for r in rows]


def _transition(conn, identity, incident_id: int, action: str,
                body: dict) -> dict:
    incident = get_incident(conn, incident_id)
    now = utcnow()
    if action == "acknowledge":
        if incident["status"] != "open":
            raise HTTPException(
                409, f"incident is '{incident['status']}'; only an"
                     " open incident can be acknowledged")
        conn.execute(
            "UPDATE incidents SET status = 'acknowledged',"
            " acknowledged_by = ?, acknowledged_at = ?,"
            " updated_at = ? WHERE id = ?",
            (identity.actor, now, now, incident_id))
    else:  # resolve
        if incident["status"] == "resolved":
            raise HTTPException(409, "incident is already resolved")
        conn.execute(
            "UPDATE incidents SET status = 'resolved',"
            " resolved_by = ?, resolved_at = ?, resolution_note = ?,"
            " updated_at = ? WHERE id = ?",
            (identity.actor, now, body.get("resolution_note", ""), now,
             incident_id))
        events.record_event(
            conn, "incident_resolved", "incident", incident_id,
            detail=body.get("resolution_note", "")
            or f"incident '{incident['title']}' resolved",
            data_source=incident["data_source"],
            source_label="Reliability Center")
    audit.append(conn, identity.actor, f"{action}_incident",
                 "incident", incident_id, {"fields": ["status"]})
    conn.commit()
    return get_incident(conn, incident_id)


def acknowledge_incident(conn, identity, incident_id: int,
                         body: dict) -> dict:
    return _transition(conn, identity, incident_id, "acknowledge",
                       body)


def resolve_incident(conn, identity, incident_id: int,
                     body: dict) -> dict:
    return _transition(conn, identity, incident_id, "resolve", body)


def reliability_summary(conn) -> dict:
    # Service status is the latest recorded database self-check;
    # audit-chain integrity is reported by the Governance Center.
    latest_service = conn.execute(
        "SELECT * FROM health_checks WHERE subject_type = 'service'"
        " AND check_type = 'database_query'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    checks = conn.execute(
        "SELECT result, COUNT(*) AS n FROM health_checks"
        " GROUP BY result").fetchall()
    totals = {r["result"]: r["n"] for r in checks}
    total_checks = sum(totals.values())
    passes = totals.get("pass", 0)
    incidents = list_incidents(conn)
    return {
        "as_of": utcnow(),
        "service_status": (latest_service["result"]
                           if latest_service else "unknown"),
        "service_status_note": (
            "from the latest recorded self-check"
            if latest_service else
            "no health check has been run yet"),
        "health_checks_recorded": total_checks,
        "health_check_pass_rate": (round(passes / total_checks, 4)
                                   if total_checks else None),
        "open_incidents": sum(1 for i in incidents
                              if i["status"] == "open"),
        "acknowledged_incidents": sum(
            1 for i in incidents if i["status"] == "acknowledged"),
        "resolved_incidents": sum(
            1 for i in incidents if i["status"] == "resolved"),
        "incidents": incidents[:20],
    }
