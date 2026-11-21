"""Command Analyst — the Command Dashboard's own intelligence.

Hard constraint (founder-adopted Nov 16/19): NO write or execution
tools. Deterministic code computes every number; the model only
narrates a typed digest, with mandatory record IDs, validated
programmatically before anything is shown. Cite-or-refuse: a narration
citing an ID outside the digest, or stating a number the digest does
not contain, is rejected and the deterministic summary is shown
instead, labeled as records — never as AI.

The analyst reads ONLY this dashboard's own database. Demo and live
records are never blended: the digest is built for one data_source.
Feature flag: MQ_ANALYST_ENABLED=1 (default OFF). Provider key comes
from the environment (OPENAI_API_KEY) — never from the database,
records, or prompts.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

from . import audit as audit_mod
from . import services
from .connect_core import (CeilingError, IntelligenceUnavailable,
                           KilledError, NarrationInvalid, ProviderClient,
                           ProviderError, Throttle, ThrottleError,
                           UsageLedger, guard_before_call, parse_narration,
                           screen, validate_narration)

AGENT_SLUG = "command-analyst"
CAPABILITY_SLUG = "executive-analysis"
MODEL_NAME = os.environ.get("MQ_ANALYST_MODEL", "gpt-6-luna")

SYSTEM = (
    "You are the Command Analyst for the M&Q AI Command Dashboard. You "
    "narrate ONLY the digest JSON provided — never invent records, "
    "numbers, statuses, or events. Reply with strict JSON only: "
    "{\"answer\": str, \"claims\": [{\"text\": str, \"record_ids\": "
    "[\"section:id\", ...]}]}. Every claim must cite digest record IDs "
    "of the form section:id. Quote numbers exactly as the digest gives "
    "them. If the digest cannot answer, say so plainly in the answer "
    "with an empty claims list. Describe agents exactly by their "
    "recorded status — registered or configured is NOT connected.")

_throttle = Throttle()


def enabled() -> bool:
    return os.environ.get("MQ_ANALYST_ENABLED", "") == "1"


def build_provider(transport=None) -> ProviderClient:
    return ProviderClient(model=MODEL_NAME,
                          api_key=os.environ.get("OPENAI_API_KEY"),
                          transport=transport)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(conn, sql, args=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ------------------------------------------------------------- digests
def registry_digest(conn, source: str) -> dict:
    agents = _rows(conn, "SELECT id, slug, display_name, role,"
                         " lifecycle_status, connection_status, data_source"
                         " FROM agents WHERE data_source = ? ORDER BY id",
                   (source,))
    by_status = {}
    for a in agents:
        by_status[a["connection_status"]] = \
            by_status.get(a["connection_status"], 0) + 1
    teams = _rows(conn, "SELECT id, slug, display_name FROM teams ORDER BY id")
    return {"section": "registry",
            "records": [{"ref": f"agent:{a['id']}", **a} for a in agents]
                       + [{"ref": f"team:{t['id']}", **t} for t in teams],
            "numbers": {"agents_total": len(agents),
                        "teams_total": len(teams),
                        **{f"agents_{k}": v for k, v in by_status.items()}}}


def approvals_digest(conn, source: str) -> dict:
    rows = _rows(conn, "SELECT id, action_type, target_type, target_id,"
                       " requested_by, status, created_at FROM approvals"
                       " ORDER BY id DESC LIMIT 50")
    pending = [r for r in rows if r["status"] == "pending"]
    return {"section": "approvals",
            "records": [{"ref": f"approval:{r['id']}", **r} for r in rows],
            "numbers": {"approvals_total": len(rows),
                        "approvals_pending": len(pending)}}


def audit_digest(conn, source: str) -> dict:
    rows = _rows(conn, "SELECT id, actor, action, target_type, target_id,"
                       " created_at FROM audit_log ORDER BY id DESC"
                       " LIMIT 25")
    chain = audit_mod.verify_chain(conn)
    return {"section": "audit",
            "records": [{"ref": f"audit:{r['id']}", **r} for r in rows],
            "numbers": {"audit_entries_total": chain.get("entries", 0),
                        "audit_chain_valid": 1 if chain.get("ok") else 0}}


def activity_digest(conn, source: str) -> dict:
    incidents = _rows(conn, "SELECT id, title, severity, status, created_at"
                            " FROM incidents WHERE data_source = ?"
                            " ORDER BY id DESC LIMIT 25", (source,))
    health = _rows(conn, "SELECT id, subject_type, subject_id, result,"
                         " checked_at FROM health_checks"
                         " WHERE data_source = ? ORDER BY id DESC LIMIT 25",
                   (source,))
    events = _rows(conn, "SELECT id, event_type, subject_type, subject_id,"
                         " created_at FROM events WHERE data_source = ?"
                         " ORDER BY id DESC LIMIT 25", (source,))
    fails = sum(1 for h in health if h["result"] == "fail")
    return {"section": "activity",
            "records": [{"ref": f"incident:{r['id']}", **r} for r in incidents]
                       + [{"ref": f"health:{r['id']}", **r} for r in health]
                       + [{"ref": f"event:{r['id']}", **r} for r in events],
            "numbers": {"incidents_total": len(incidents),
                        "incidents_open": sum(1 for r in incidents
                                              if r["status"] != "resolved"),
                        "health_checks_total": len(health),
                        "health_failures": fails,
                        "events_total": len(events)}}


def work_digest(conn, source: str) -> dict:
    tasks = _rows(conn, "SELECT id, title, status, data_source FROM tasks"
                        " WHERE data_source = ? ORDER BY id DESC LIMIT 50",
                  (source,))
    execs = _rows(conn, "SELECT id, task_id, status FROM executions"
                        " WHERE data_source = ? ORDER BY id DESC LIMIT 50",
                  (source,))
    done = sum(1 for e in execs if e["status"] == "completed")
    return {"section": "work",
            "records": [{"ref": f"task:{r['id']}", **r} for r in tasks]
                       + [{"ref": f"execution:{r['id']}", **r} for r in execs],
            "numbers": {"tasks_total": len(tasks),
                        "tasks_open": sum(1 for t in tasks
                                          if t["status"] not in
                                          ("completed", "cancelled")),
                        "executions_total": len(execs),
                        "executions_completed": done}}


def economics_digest(conn, source: str) -> dict:
    usage = _rows(conn, "SELECT id, agent_id, input_tokens, output_tokens,"
                        " actual_cost_minor, estimated_cost_minor,"
                        " occurred_at FROM usage_records"
                        " WHERE data_source = ? ORDER BY id DESC LIMIT 50",
                  (source,))
    budgets = _rows(conn, "SELECT id, scope_type, scope_id, limit_minor"
                          " FROM budgets ORDER BY id")
    return {"section": "economics",
            "records": [{"ref": f"usage:{r['id']}", **r} for r in usage]
                       + [{"ref": f"budget:{r['id']}", **r} for r in budgets],
            "numbers": {"usage_records_total": len(usage),
                        "input_tokens_total": sum(r["input_tokens"] or 0
                                                  for r in usage),
                        "output_tokens_total": sum(r["output_tokens"] or 0
                                                   for r in usage),
                        "budgets_total": len(budgets)}}


BUILDERS = {"registry": registry_digest, "approvals": approvals_digest,
            "audit": audit_digest, "activity": activity_digest,
            "work": work_digest, "economics": economics_digest}

_SECTION_KEYWORDS = {
    "approvals": ("approval", "approvals", "pending", "waiting", "decide"),
    "activity": ("fail", "failed", "failure", "incident", "health",
                 "broke", "error", "degraded"),
    "economics": ("cost", "spend", "spent", "budget", "token", "price"),
    "work": ("task", "queue", "work", "execution", "progress"),
    "audit": ("audit", "who", "changed", "history", "chain"),
    "registry": ("agent", "agents", "team", "status", "stuck",
                 "connected", "registered"),
}


def route_sections(question: str) -> list[str]:
    q = (question or "").lower()
    picked = [s for s, kws in _SECTION_KEYWORDS.items()
              if any(k in q for k in kws)]
    return picked or list(BUILDERS)


def build_digest(conn, question: str, source: str = "live") -> dict:
    sections = [BUILDERS[s](conn, source) for s in route_sections(question)]
    record_ids, numbers = [], {}
    for s in sections:
        record_ids += [r["ref"] for r in s["records"]]
        numbers.update(s["numbers"])
    payload = {"sections": sections, "record_ids": record_ids,
               "numbers": numbers, "data_source": source,
               "generated_at": _now()}
    payload["digest_hash"] = hashlib.sha256(json.dumps(
        {k: payload[k] for k in ("sections", "record_ids", "numbers")},
        sort_keys=True, default=str).encode()).hexdigest()
    return payload


def deterministic_summary(digest: dict) -> str:
    n = digest["numbers"]
    parts = []
    if "agents_total" in n:
        parts.append(f"{n['agents_total']} agents are registered"
                     f" ({n.get('agents_configured', 0)} configured,"
                     f" {n.get('agents_connected', 0)} connected).")
    if "approvals_pending" in n:
        parts.append(f"{n['approvals_pending']} approvals are pending.")
    if "incidents_open" in n:
        parts.append(f"{n['incidents_open']} incidents are open;"
                     f" {n.get('health_failures', 0)} recent health"
                     f" checks failed.")
    if "tasks_open" in n:
        parts.append(f"{n['tasks_open']} tasks are open;"
                     f" {n.get('executions_completed', 0)} of"
                     f" {n.get('executions_total', 0)} recent executions"
                     f" completed.")
    if "usage_records_total" in n:
        parts.append(f"{n['usage_records_total']} usage records;"
                     f" {n.get('budgets_total', 0)} budgets on file.")
    if "audit_entries_total" in n:
        parts.append(f"Audit chain holds {n['audit_entries_total']}"
                     f" entries and verifies"
                     f" {'valid' if n.get('audit_chain_valid') else 'INVALID'}.")
    return " ".join(parts) or "The records do not cover that question yet."


# ----------------------------------------------------------------- ask
def status(conn) -> dict:
    provider = build_provider()
    row = conn.execute("SELECT connection_status FROM agents WHERE slug = ?",
                       (AGENT_SLUG,)).fetchone()
    return {"agent": AGENT_SLUG, "enabled": enabled(),
            "registered": row is not None,
            "connection_status": row[0] if row else None,
            "provider_available": provider.available,
            "state": ("disabled" if not enabled() else
                      "configured" if not provider.available else
                      "ready-unverified")}


def ask(conn, identity, question: str, provider: ProviderClient | None = None,
        source: str = "live") -> dict:
    base = {"agent": AGENT_SLUG, "ai_generated": False}
    if not enabled():
        return {**base, "status": "disabled",
                "text": "The Command Analyst is switched off."}
    hit = screen(question, "en")
    if hit:
        return {**base, "status": "refused_screen", "text": hit.refusal,
                "detail": hit.kind}
    digest = build_digest(conn, question, source)
    summary = deterministic_summary(digest)
    if not digest["record_ids"] and not digest["numbers"]:
        return {**base, "status": "not_covered",
                "text": "The dashboard's records do not cover that"
                        " question yet.", "digest_hash": digest["digest_hash"]}
    provider = provider or build_provider()
    if not provider.available:
        return {**base, "status": "not_connected", "mode": "records",
                "text": summary, "digest_hash": digest["digest_hash"],
                "record_ids": digest["record_ids"]}
    ledger = UsageLedger(conn)
    ledger.init()
    try:
        _throttle.check(identity.actor)
        guard_before_call(ledger)
        result = provider.complete(
            SYSTEM, "Digest:\n" + json.dumps(
                {"sections": digest["sections"],
                 "numbers": digest["numbers"]}, default=str)
            + f"\n\nFounder question: {question}")
        narration = parse_narration(result.text)
        validate_narration(narration, set(digest["record_ids"]),
                           set(digest["numbers"].values()))
        ledger.record(AGENT_SLUG, result.model, result.usage,
                      result.cost_micro_usd, "answered")
        return {**base, "status": "answered", "mode": "ai",
                "ai_generated": True, "text": narration.answer,
                "claims": narration.claims,
                "digest_hash": digest["digest_hash"],
                "record_ids": digest["record_ids"]}
    except (ThrottleError, KilledError, CeilingError) as exc:
        return {**base, "status": "unavailable", "mode": "records",
                "text": summary, "detail": type(exc).__name__,
                "digest_hash": digest["digest_hash"]}
    except (NarrationInvalid, ProviderError, IntelligenceUnavailable) as exc:
        ledger.record(AGENT_SLUG, provider.model, None, None,
                      f"error:{type(exc).__name__}")
        return {**base, "status": "error", "mode": "records",
                "text": summary, "detail": type(exc).__name__,
                "digest_hash": digest["digest_hash"]}


# ------------------------------------------------------- drafts & registry
def draft_assignment(conn, identity, body: dict) -> dict:
    """Propose a task as a PENDING APPROVAL. Creates no task, routes
    nothing; a human decides through the existing approvals flow."""
    if not enabled():
        # Phase 2 flag-consistency correction: ask() always honored
        # MQ_ANALYST_ENABLED; draft did not. Same refusal semantics —
        # the route maps this ValueError to HTTP 422. See
        # FLAG-FIX-NOTE.md (Phase 2 workspace).
        raise ValueError("The Command Analyst is switched off.")
    title = (body.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    capability = (body.get("required_capability") or "").strip()
    suggested = None
    if capability:
        row = conn.execute(
            "SELECT a.id FROM agents a JOIN agent_capabilities ac"
            " ON ac.agent_id = a.id JOIN capabilities c"
            " ON c.id = ac.capability_id WHERE c.slug = ?"
            " ORDER BY a.id LIMIT 1", (capability,)).fetchone()
        suggested = row[0] if row else None
    if suggested is None:
        row = conn.execute("SELECT id FROM agents ORDER BY id LIMIT 1"
                           ).fetchone()
        suggested = row[0] if row else None
    if suggested is None:
        raise ValueError("no agents registered to propose work for")
    payload = {"title": title,
               "description": (body.get("description") or "").strip(),
               "required_capability": capability or None,
               "suggested_agent_id": suggested,
               "source": "command-analyst-draft"}
    approval = services.create_approval(conn, identity, {
        "action_type": "analyst_task_proposal", "target_type": "agent",
        "target_id": suggested, "reason": json.dumps(payload)})
    audit_mod.append(conn, identity.actor, "analyst.draft_proposed",
                     "approval", approval["id"], {"title": title})
    conn.commit()
    return {"approval": approval, "proposal": payload,
            "note": "Pending human approval — no task was created."}


def register_analyst(conn, actor: str = "system") -> dict:
    """Idempotent self-registration at `configured` — a registry record
    only. No call has been made; the notes say so."""
    if not enabled():
        # Phase 2 flag-consistency correction: register did not check
        # MQ_ANALYST_ENABLED while ask() did. Refuse with ask()'s
        # disabled semantics and write nothing. See FLAG-FIX-NOTE.md.
        return {"agent_id": None, "connection_status": None,
                "status": "disabled",
                "text": "The Command Analyst is switched off."}
    now = _now()
    conn.execute(
        "INSERT INTO capabilities (slug, display_name, description,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(slug) DO NOTHING",
        (CAPABILITY_SLUG, "Executive Analysis",
         "Narration of dashboard record digests with validated citations.",
         now, now))
    conn.execute(
        "INSERT INTO agents (slug, display_name, role, description,"
        " owner_project, lifecycle_status, connection_status, data_source,"
        " notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?,"
        " ?, ?, ?) ON CONFLICT(slug) DO NOTHING",
        (AGENT_SLUG, "Command Analyst",
         "Executive analyst — narration only; no write or execution tools",
         "Answers the founder's questions from this dashboard's own"
         " records via deterministic digests and validated narration.",
         "mq-ai-command-dashboard", "active", "configured", "live",
         "Registry record only — intelligence NOT connected. No provider"
         " call has been made from this product.", now, now))
    conn.execute(
        "INSERT INTO model_configs (provider, model_name, display_name,"
        " config_status, notes, created_at, updated_at)"
        " SELECT 'openai', ?, 'GPT-6 Luna (analyst)', 'configured',"
        " 'Pointer only — the key lives in the server environment,"
        " never in this database.', ?, ?"
        " WHERE NOT EXISTS (SELECT 1 FROM model_configs"
        " WHERE provider = 'openai' AND model_name = ?)",
        (MODEL_NAME, now, now, MODEL_NAME))
    agent = conn.execute("SELECT id FROM agents WHERE slug = ?",
                         (AGENT_SLUG,)).fetchone()
    cap = conn.execute("SELECT id FROM capabilities WHERE slug = ?",
                       (CAPABILITY_SLUG,)).fetchone()
    if agent and cap:
        conn.execute(
            "INSERT INTO agent_capabilities (agent_id, capability_id)"
            " VALUES (?, ?) ON CONFLICT DO NOTHING", (agent[0], cap[0]))
    audit_mod.append(conn, actor, "analyst.registered", "agent",
                     agent[0] if agent else None,
                     {"status": "configured"})
    conn.commit()
    return {"agent_id": agent[0] if agent else None,
            "connection_status": "configured"}
