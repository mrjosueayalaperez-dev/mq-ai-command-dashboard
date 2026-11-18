"""Model Economics (module 9) and Performance Center (module 8).

Both modules are arithmetic over records this system keeps — nothing
is invented:

- Prices live in an editable per-model table that starts EMPTY. With
  no rate set, a usage record's estimated cost stays null and is
  reported as "rate not set" — never as a fabricated number.
- Estimated and actual costs are separate columns, separate totals,
  and are never summed together.
- Performance figures are computed from execution records only. With
  no executions recorded, rates are null and an explicit
  "no executions recorded yet" note is returned instead of numbers.
- Demo and live records are always reported in separate blocks.
"""

import sqlite3

from fastapi import HTTPException

from . import audit
from .db import utcnow
from .services import get_agent
from .work import get_task

SOURCES = ("live", "demo")


# ------------------------------------------------------------------ prices

def _price_detail(conn, price_id: int) -> dict:
    row = conn.execute("SELECT * FROM model_prices WHERE id = ?",
                       (price_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "price not found")
    price = dict(row)
    price["rate_set"] = (price["input_per_1m_minor"] is not None
                         and price["output_per_1m_minor"] is not None)
    return price


def list_prices(conn) -> list[dict]:
    out = []
    for cfg in conn.execute(
            "SELECT * FROM model_configs ORDER BY display_name"):
        row = conn.execute(
            "SELECT * FROM model_prices WHERE model_config_id = ?",
            (cfg["id"],)).fetchone()
        entry = {
            "model_config_id": cfg["id"],
            "display_name": cfg["display_name"],
            "provider": cfg["provider"],
            "model_name": cfg["model_name"],
            "input_per_1m_minor": row["input_per_1m_minor"]
            if row else None,
            "output_per_1m_minor": row["output_per_1m_minor"]
            if row else None,
            "currency": row["currency"] if row else "USD",
            "rate_set": bool(row)
            and row["input_per_1m_minor"] is not None
            and row["output_per_1m_minor"] is not None,
        }
        out.append(entry)
    return out


def upsert_price(conn, identity, body: dict) -> dict:
    if "model_config_id" not in body:
        raise HTTPException(422, "model_config_id is required")
    cfg = conn.execute("SELECT id FROM model_configs WHERE id = ?",
                       (body["model_config_id"],)).fetchone()
    if cfg is None:
        raise HTTPException(422, "unknown model_config_id")
    for field in ("input_per_1m_minor", "output_per_1m_minor"):
        value = body.get(field)
        if value is not None and (not isinstance(value, int)
                                  or value < 0):
            raise HTTPException(422, f"{field} must be a non-negative"
                                     " integer (minor units) or null")
    now = utcnow()
    existing = conn.execute(
        "SELECT id FROM model_prices WHERE model_config_id = ?",
        (body["model_config_id"],)).fetchone()
    if existing:
        conn.execute(
            "UPDATE model_prices SET input_per_1m_minor = ?,"
            " output_per_1m_minor = ?, currency = ?, updated_by = ?,"
            " updated_at = ? WHERE id = ?",
            (body.get("input_per_1m_minor"),
             body.get("output_per_1m_minor"),
             body.get("currency", "USD"), identity.actor, now,
             existing["id"]))
        price_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO model_prices (model_config_id,"
            " input_per_1m_minor, output_per_1m_minor, currency,"
            " updated_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (body["model_config_id"], body.get("input_per_1m_minor"),
             body.get("output_per_1m_minor"),
             body.get("currency", "USD"), identity.actor, now, now))
        price_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "set_model_price", "model_config",
                 body["model_config_id"],
                 {"fields": ["input_per_1m_minor",
                             "output_per_1m_minor"]})
    conn.commit()
    return _price_detail(conn, price_id)


def _price_for(conn, model_config_id):
    if model_config_id is None:
        return None
    return conn.execute(
        "SELECT * FROM model_prices WHERE model_config_id = ?",
        (model_config_id,)).fetchone()


def estimate_cost(conn, model_config_id, input_tokens: int,
                  output_tokens: int):
    """Estimated cost in minor units, or None when no full rate is
    set. Rounding is half-up to the nearest minor unit."""
    price = _price_for(conn, model_config_id)
    if price is None or price["input_per_1m_minor"] is None \
            or price["output_per_1m_minor"] is None:
        return None
    numerator = (input_tokens * price["input_per_1m_minor"]
                 + output_tokens * price["output_per_1m_minor"])
    return (numerator + 500_000) // 1_000_000


# ------------------------------------------------------------------- usage

def create_usage(conn, identity, body: dict) -> dict:
    agent_id = body.get("agent_id")
    if agent_id is not None:
        get_agent(conn, agent_id)
    task_id = body.get("task_id")
    if task_id is not None:
        get_task(conn, task_id)
    model_config_id = body.get("model_config_id")
    if model_config_id is not None:
        cfg = conn.execute("SELECT id FROM model_configs WHERE id = ?",
                           (model_config_id,)).fetchone()
        if cfg is None:
            raise HTTPException(422, "unknown model_config_id")
    input_tokens = body.get("input_tokens", 0)
    output_tokens = body.get("output_tokens", 0)
    for field, value in (("input_tokens", input_tokens),
                         ("output_tokens", output_tokens)):
        if not isinstance(value, int) or value < 0:
            raise HTTPException(422, f"{field} must be a non-negative"
                                     " integer")
    data_source = body.get("data_source", "live")
    if data_source not in SOURCES:
        raise HTTPException(422, "data_source must be 'live' or 'demo'")
    estimated = body.get("estimated_cost_minor")
    if estimated is None:
        estimated = estimate_cost(conn, model_config_id, input_tokens,
                                  output_tokens)
    actual = body.get("actual_cost_minor")
    for field, value in (("estimated_cost_minor", estimated),
                         ("actual_cost_minor", actual)):
        if value is not None and (not isinstance(value, int)
                                  or value < 0):
            raise HTTPException(422, f"{field} must be a non-negative"
                                     " integer or null")
    occurred_at = body.get("occurred_at") or utcnow()
    cur = conn.execute(
        "INSERT INTO usage_records (agent_id, task_id, execution_id,"
        " model_config_id, project_scope, input_tokens, output_tokens,"
        " estimated_cost_minor, actual_cost_minor, occurred_at,"
        " data_source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
        " ?, ?, ?)",
        (agent_id, task_id, body.get("execution_id"), model_config_id,
         body.get("project_scope", "mq-ecosystem"), input_tokens,
         output_tokens, estimated, actual, occurred_at, data_source,
         utcnow()),
    )
    usage_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "record_usage", "usage_record",
                 usage_id,
                 {"fields": ["input_tokens", "output_tokens"],
                  "agent_id": agent_id, "task_id": task_id})
    conn.commit()
    return _usage_detail(conn, usage_id)


def _usage_detail(conn, usage_id: int) -> dict:
    row = conn.execute("SELECT * FROM usage_records WHERE id = ?",
                       (usage_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "usage record not found")
    usage = dict(row)
    price = _price_for(conn, usage["model_config_id"])
    usage["rate_set"] = bool(price) \
        and price["input_per_1m_minor"] is not None \
        and price["output_per_1m_minor"] is not None
    usage["cost_note"] = (
        None if usage["estimated_cost_minor"] is not None
        else "rate not set — no estimate computed")
    return usage


def list_usage(conn, agent_id: int | None = None,
               task_id: int | None = None,
               project_scope: str | None = None,
               source: str | None = None) -> list[dict]:
    query = "SELECT id FROM usage_records"
    clauses, params = [], []
    if agent_id is not None:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    if project_scope is not None:
        clauses.append("project_scope = ?")
        params.append(project_scope)
    if source is not None:
        clauses.append("data_source = ?")
        params.append(source)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    return [_usage_detail(conn, r["id"])
            for r in conn.execute(query, params)]


def _usage_block(conn, source: str, agent_id=None, project_scope=None):
    clauses = ["data_source = ?"]
    params: list = [source]
    if agent_id is not None:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if project_scope is not None:
        clauses.append("project_scope = ?")
        params.append(project_scope)
    where = " AND ".join(clauses)
    row = conn.execute(
        f"SELECT COUNT(*) AS records,"
        f" COALESCE(SUM(input_tokens), 0) AS in_tokens,"
        f" COALESCE(SUM(output_tokens), 0) AS out_tokens,"
        f" SUM(estimated_cost_minor) AS est,"
        f" SUM(actual_cost_minor) AS act,"
        f" SUM(CASE WHEN estimated_cost_minor IS NULL THEN 1 ELSE 0"
        f" END) AS unrated FROM usage_records WHERE {where}",
        params).fetchone()
    return {
        "records": row["records"],
        "input_tokens": row["in_tokens"],
        "output_tokens": row["out_tokens"],
        "estimated_cost_minor": row["est"],
        "actual_cost_minor": row["act"],
        "unrated_records": row["unrated"] or 0,
    }


def economics_summary(conn, agent_id=None, project_scope=None) -> dict:
    return {
        "as_of": utcnow(),
        "by_source": {
            source: _usage_block(conn, source, agent_id, project_scope)
            for source in SOURCES
        },
        "note": "estimated and actual costs are reported separately"
                " and never summed together; records without a set"
                " rate carry no estimate",
    }


# ----------------------------------------------------------------- budgets

def _budget_status(conn, budget: dict) -> dict:
    clauses = ["1 = 1"]
    params: list = []
    if budget["scope_type"] == "agent" and budget["scope_id"]:
        clauses.append("agent_id = ?")
        params.append(int(budget["scope_id"]))
    elif budget["scope_type"] == "task" and budget["scope_id"]:
        clauses.append("task_id = ?")
        params.append(int(budget["scope_id"]))
    elif budget["scope_type"] == "project" and budget["scope_id"]:
        clauses.append("project_scope = ?")
        params.append(budget["scope_id"])
    where = " AND ".join(clauses)
    spent = 0
    bases = set()
    for row in conn.execute(
            f"SELECT estimated_cost_minor, actual_cost_minor"
            f" FROM usage_records WHERE {where}", params):
        if row["actual_cost_minor"] is not None:
            spent += row["actual_cost_minor"]
            bases.add("actual")
        elif row["estimated_cost_minor"] is not None:
            spent += row["estimated_cost_minor"]
            bases.add("estimated")
    basis = ("none" if not bases else bases.pop()
             if len(bases) == 1 else "mixed")
    limit = budget["limit_minor"]
    threshold = limit * budget["alert_threshold_pct"] // 100
    if limit and spent >= limit:
        status = "exceeded"
    elif limit and spent >= threshold:
        status = "warning"
    else:
        status = "ok"
    return {
        **budget,
        "spent_minor": spent,
        "spent_basis": basis,
        "remaining_minor": limit - spent,
        "utilization_pct": (round(spent * 100 / limit, 1)
                            if limit else None),
        "status": status,
        "alert": status in ("warning", "exceeded"),
    }


def create_budget(conn, identity, body: dict) -> dict:
    scope_type = body.get("scope_type", "")
    if scope_type not in ("agent", "task", "project", "global"):
        raise HTTPException(422, "scope_type must be agent, task,"
                                 " project, or global")
    limit = body.get("limit_minor")
    if not isinstance(limit, int) or limit <= 0:
        raise HTTPException(422, "limit_minor must be a positive"
                                 " integer")
    threshold = body.get("alert_threshold_pct", 80)
    if not isinstance(threshold, int) or not 1 <= threshold <= 100:
        raise HTTPException(422, "alert_threshold_pct must be an"
                                 " integer 1..100")
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO budgets (scope_type, scope_id, period,"
        " limit_minor, alert_threshold_pct, created_by, created_at,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (scope_type,
         None if body.get("scope_id") is None
         else str(body["scope_id"]),
         body.get("period", "monthly"), limit, threshold,
         identity.actor, now, now),
    )
    budget_id = int(cur.lastrowid)
    audit.append(conn, identity.actor, "create_budget", "budget",
                 budget_id, {"fields": ["scope_type", "limit_minor"]})
    conn.commit()
    return get_budget(conn, budget_id)


def get_budget(conn, budget_id: int) -> dict:
    row = conn.execute("SELECT * FROM budgets WHERE id = ?",
                       (budget_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "budget not found")
    return _budget_status(conn, dict(row))


def list_budgets(conn) -> list[dict]:
    return [_budget_status(conn, dict(r)) for r in conn.execute(
        "SELECT * FROM budgets ORDER BY id")]


# -------------------------------------------------------------- performance

def _performance_block(conn, source: str, agent_id=None) -> dict:
    clauses = ["e.data_source = ?"]
    params: list = [source]
    if agent_id is not None:
        clauses.append("e.agent_id = ?")
        params.append(agent_id)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT e.* FROM executions e WHERE {where}",
        params).fetchall()
    total = len(rows)
    finished = [r for r in rows if r["status"] != "running"]
    completed = [r for r in rows if r["status"] == "completed"]
    failed = [r for r in rows if r["status"] == "failed"]
    durations = [r["duration_ms"] for r in finished
                 if r["duration_ms"] is not None]
    decided = len(completed) + len(failed)
    per_agent: dict[int, dict] = {}
    for r in rows:
        if r["agent_id"] is None:
            continue
        entry = per_agent.setdefault(
            r["agent_id"],
            {"agent_id": r["agent_id"], "executions": 0,
             "completed": 0, "failed": 0, "busy_ms": 0,
             "open_tasks": 0, "running_executions": 0})
        entry["executions"] += 1
        if r["status"] == "completed":
            entry["completed"] += 1
        if r["status"] == "failed":
            entry["failed"] += 1
        if r["status"] == "running":
            entry["running_executions"] += 1
        if r["duration_ms"]:
            entry["busy_ms"] += r["duration_ms"]
    total_busy = sum(e["busy_ms"] for e in per_agent.values())
    for entry in per_agent.values():
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE assigned_agent_id"
            " = ? AND status IN ('routed', 'running', 'blocked')",
            (entry["agent_id"],)).fetchone()
        entry["open_tasks"] = row["n"]
        entry["workload"] = entry["open_tasks"] \
            + entry["running_executions"]
        entry["utilization_pct"] = (
            round(entry["busy_ms"] * 100 / total_busy, 1)
            if total_busy else None)
    by_day: dict[str, int] = {}
    for r in finished:
        if r["ended_at"]:
            day = r["ended_at"][:10]
            by_day[day] = by_day.get(day, 0) + 1
    return {
        "executions_recorded": total,
        "running": sum(1 for r in rows if r["status"] == "running"),
        "completed": len(completed),
        "failed": len(failed),
        "success_rate": (round(len(completed) / decided, 4)
                         if decided else None),
        "failure_rate": (round(len(failed) / decided, 4)
                         if decided else None),
        "avg_duration_ms": (round(sum(durations) / len(durations), 1)
                            if durations else None),
        "total_busy_ms": total_busy,
        "throughput_by_day": by_day,
        "per_agent": sorted(per_agent.values(),
                            key=lambda e: e["agent_id"]),
        "note": (None if total else
                 "no executions recorded yet — no performance figures"
                 " exist to report"),
    }


def performance(conn, agent_id=None) -> dict:
    return {
        "as_of": utcnow(),
        "by_source": {
            source: _performance_block(conn, source, agent_id)
            for source in SOURCES
        },
        "note": "figures are computed from recorded executions only;"
                " demo and live records are reported separately and"
                " never blended",
    }
