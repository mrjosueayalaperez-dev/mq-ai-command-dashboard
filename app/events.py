"""Live Activity: the system's own event records.

Two record streams feed the activity view, and they are never blended:

- ``events`` rows — operational occurrences recorded by the Phase 2
  modules (routing, executions, communications, incidents...). Each row
  carries its own ``data_source`` (``live`` | ``demo``) and a
  human-readable ``source_label`` naming the module that recorded it.
- ``audit_log`` rows — the governance trail. Audit rows do not claim a
  telemetry source; in the feed they are labeled ``system`` so a demo
  event, a live event, and a system record are always distinguishable
  and are counted separately.
"""

import sqlite3

from .db import utcnow

SEVERITIES = ("info", "warning", "critical")


def record_event(conn: sqlite3.Connection, event_type: str,
                 subject_type: str, subject_id, detail: str = "",
                 severity: str = "info", data_source: str = "live",
                 source_label: str = "") -> int:
    """Append one event row. Caller commits."""
    if severity not in SEVERITIES:
        severity = "info"
    if data_source not in ("live", "demo"):
        data_source = "live"
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO events (event_type, subject_type, subject_id,"
        " detail, severity, occurred_at, data_source, source_label,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_type, subject_type, subject_id, detail, severity, now,
         data_source, source_label, now),
    )
    return int(cur.lastrowid)


def feed(conn: sqlite3.Connection, source: str | None = None,
         limit: int = 50) -> dict:
    """Merged, newest-first activity feed with per-source counts.

    ``source`` may be 'live', 'demo', 'system', or None (all). Counts
    are always reported per source — never as one blended total.
    """
    entries: list[dict] = []
    counts = {"live": 0, "demo": 0, "system": 0}
    for row in conn.execute("SELECT * FROM events"):
        counts[row["data_source"]] += 1
        if source in (None, row["data_source"]):
            entries.append({
                "kind": "event",
                "id": row["id"],
                "event_type": row["event_type"],
                "subject_type": row["subject_type"],
                "subject_id": row["subject_id"],
                "detail": row["detail"],
                "severity": row["severity"],
                "occurred_at": row["occurred_at"],
                "data_source": row["data_source"],
                "source_label": row["source_label"] or "events",
            })
    audit_total = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    counts["system"] = audit_total
    if source in (None, "system"):
        for row in conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
                (limit,)):
            entries.append({
                "kind": "audit",
                "id": row["id"],
                "event_type": row["action"],
                "subject_type": row["target_type"],
                "subject_id": row["target_id"],
                "detail": row["detail"],
                "severity": "info",
                "occurred_at": row["created_at"],
                "data_source": "system",
                "source_label": "audit log",
                "actor": row["actor"],
            })
    entries.sort(key=lambda e: (e["occurred_at"], e["id"]),
                 reverse=True)
    return {
        "as_of": utcnow(),
        "counts_by_source": counts,
        "entries": entries[:limit],
    }
