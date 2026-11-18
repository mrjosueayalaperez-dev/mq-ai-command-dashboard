"""Append-only, hash-chained audit log.

Each entry's ``entry_hash`` is the SHA-256 of the previous entry's hash
plus the canonical content of this entry, so editing or deleting any
historical row breaks verification of every later row. The log records
WHICH fields changed on an update — never secret values (this system
stores no secrets).
"""

import hashlib
import json
import sqlite3

from .db import utcnow

GENESIS_HASH = "GENESIS"


def _entry_hash(prev_hash: str, actor: str, action: str, target_type: str,
                target_id, detail: str, created_at: str) -> str:
    payload = "|".join([
        prev_hash,
        actor,
        action,
        target_type,
        "" if target_id is None else str(target_id),
        detail,
        created_at,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append(conn: sqlite3.Connection, actor: str, action: str,
           target_type: str, target_id, detail: dict | None = None) -> int:
    """Append one entry and return its id. Caller commits."""
    detail_text = json.dumps(detail or {}, sort_keys=True)
    row = conn.execute(
        "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = row["entry_hash"] if row else GENESIS_HASH
    created_at = utcnow()
    entry_hash = _entry_hash(prev_hash, actor, action, target_type,
                             target_id, detail_text, created_at)
    cur = conn.execute(
        "INSERT INTO audit_log (actor, action, target_type, target_id,"
        " detail, created_at, entry_hash, prev_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (actor, action, target_type, target_id, detail_text,
         created_at, entry_hash, prev_hash),
    )
    return int(cur.lastrowid)


def verify_chain(conn: sqlite3.Connection) -> dict:
    """Recompute the whole chain. Returns {ok, entries, broken_at}."""
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id ASC"
    ).fetchall()
    prev_hash = GENESIS_HASH
    for row in rows:
        if row["prev_hash"] != prev_hash:
            return {"ok": False, "entries": len(rows),
                    "broken_at": row["id"]}
        expected = _entry_hash(
            row["prev_hash"], row["actor"], row["action"],
            row["target_type"], row["target_id"], row["detail"],
            row["created_at"],
        )
        if expected != row["entry_hash"]:
            return {"ok": False, "entries": len(rows),
                    "broken_at": row["id"]}
        prev_hash = row["entry_hash"]
    return {"ok": True, "entries": len(rows), "broken_at": None}
