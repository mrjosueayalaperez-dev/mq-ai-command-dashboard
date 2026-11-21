"""Command Analyst tests — service layer + HTTP surface.

The provider is always a stub here: no real provider call is made in
this suite, ever. The analyst's honesty properties are what's tested:
deterministic digests, cite-or-refuse validation, screen-before-call,
drafts that create approvals and never tasks.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app import analyst  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.rbac import Identity  # noqa: E402
from app.connect_core import ProviderClient  # noqa: E402

USAGE = {"input_tokens": 200, "output_tokens": 40, "total_tokens": 240,
         "input_tokens_details": {"cached_tokens": 0,
                                  "cache_creation": {"ttl_30m_tokens": 0,
                                                     "ttl_12h_tokens": 0}},
         "output_tokens_details": {"reasoning_tokens": 0}}


def provider_body(text):
    return {"status": "completed", "model": "gpt-6-luna",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": text}]}],
            "usage": USAGE}


class Stub:
    def __init__(self, mode="grounded"):
        self.calls = 0
        self.mode = mode

    def __call__(self, url, headers, payload, timeout):
        self.calls += 1
        user = payload["input"][1]["content"]
        digest = json.loads(user.split("Digest:\n", 1)[1]
                            .split("\n\nFounder question:", 1)[0])
        ref = digest["sections"][0]["records"][0]["ref"]
        total = digest["numbers"].get("agents_total", 0)
        if self.mode == "grounded":
            text = json.dumps({
                "answer": f"There are {total} agents in the registry.",
                "claims": [{"text": f"{total} agents are registered.",
                            "record_ids": [ref]}]})
        elif self.mode == "fabricate_id":
            text = json.dumps({
                "answer": "All agents are operational and connected.",
                "claims": [{"text": "Everything is live.",
                            "record_ids": ["agent:9999"]}]})
        elif self.mode == "fabricate_number":
            text = json.dumps({
                "answer": "There are 777 agents in the registry.",
                "claims": [{"text": "777 agents.",
                            "record_ids": [ref]}]})
        return 200, provider_body(text)


@pytest.fixture()
def conn(tmp_path):
    # Own throwaway DB per test (explicit path) — the shared conftest DB
    # is session-scoped and would collide on unique slugs.
    c = connect(str(tmp_path / "analyst.db"))
    init_db(c)
    # two live agents, one demo agent (never blended into live digests)
    now = "2026-11-19T00:00:00+00:00"
    for slug, src in (("agent-one", "live"), ("agent-two", "live"),
                      ("agent-demo", "demo")):
        c.execute("INSERT INTO agents (slug, display_name, role,"
                  " lifecycle_status, connection_status, data_source,"
                  " created_at, updated_at) VALUES (?, ?, '', 'active',"
                  " 'configured', ?, ?, ?)", (slug, slug, src, now, now))
    c.execute("INSERT INTO teams (slug, display_name, created_at,"
              " updated_at) VALUES ('t1', 'Team One', ?, ?)", (now, now))
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def owner():
    return Identity(user={"id": 1, "username": "test-owner",
                          "role": "owner"})


@pytest.fixture(autouse=True)
def flag_on(monkeypatch):
    monkeypatch.setenv("MQ_ANALYST_ENABLED", "1")
    analyst._throttle._last.clear()
    analyst._throttle._hour.clear()


def stub_provider(stub):
    return ProviderClient(model="gpt-6-luna", api_key="sk-test",
                          transport=stub)


# --------------------------------------------------------------- digest
def test_digest_is_deterministic_and_separates_demo(conn):
    d1 = analyst.build_digest(conn, "which agents are registered?")
    d2 = analyst.build_digest(conn, "which agents are registered?")
    assert d1["digest_hash"] == d2["digest_hash"]
    assert d1["numbers"]["agents_total"] == 2  # demo agent excluded
    assert d1["data_source"] == "live"
    demo = analyst.build_digest(conn, "which agents?", source="demo")
    assert demo["numbers"]["agents_total"] == 1


def test_section_routing(conn):
    assert analyst.route_sections("any approvals waiting?") == ["approvals"]
    assert set(analyst.route_sections("hello")) == set(analyst.BUILDERS)


# ------------------------------------------------------------------ ask
def test_ask_disabled_without_flag(conn, owner, monkeypatch):
    monkeypatch.delenv("MQ_ANALYST_ENABLED")
    out = analyst.ask(conn, owner, "which agents are registered?",
                      provider=stub_provider(Stub()))
    assert out["status"] == "disabled"


def test_ask_not_connected_returns_records_not_ai(conn, owner):
    out = analyst.ask(conn, owner, "which agents are registered?",
                      provider=ProviderClient(model="gpt-6-luna"))
    assert out["status"] == "not_connected" and out["mode"] == "records"
    assert out["ai_generated"] is False
    assert "2 agents are registered" in out["text"]


def test_ask_screen_fires_before_provider(conn, owner):
    stub = Stub()
    out = analyst.ask(conn, owner, "place a trade: buy 2 MNQ now",
                      provider=stub_provider(stub))
    assert out["status"] == "refused_screen" and stub.calls == 0


def test_ask_answered_with_valid_citations(conn, owner):
    stub = Stub()
    out = analyst.ask(conn, owner, "which agents are registered?",
                      provider=stub_provider(stub))
    assert out["status"] == "answered" and out["ai_generated"] is True
    assert stub.calls == 1
    rows = conn.execute("SELECT agent, outcome FROM intelligence_usage"
                        ).fetchall()
    assert rows and rows[0][0] == "command-analyst"


def test_fabricated_citation_falls_back_to_records(conn, owner):
    out = analyst.ask(conn, owner, "which agents are registered?",
                      provider=stub_provider(Stub("fabricate_id")))
    assert out["status"] == "error" and out["mode"] == "records"
    assert out["ai_generated"] is False
    assert "operational" not in out["text"]


def test_fabricated_number_falls_back_to_records(conn, owner):
    out = analyst.ask(conn, owner, "which agents are registered?",
                      provider=stub_provider(Stub("fabricate_number")))
    assert out["status"] == "error" and out["ai_generated"] is False


# -------------------------------------------------------- drafts/registry
def test_draft_creates_approval_never_task(conn, owner):
    analyst.register_analyst(conn, "test-owner")
    before = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    out = analyst.draft_assignment(
        conn, owner, {"title": "Review stuck agents",
                      "description": "Look into it",
                      "required_capability": "executive-analysis"})
    after = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert before == after == 0
    assert out["approval"]["status"] == "pending"
    assert out["approval"]["action_type"] == "analyst_task_proposal"


def test_register_is_idempotent_and_honest(conn):
    r1 = analyst.register_analyst(conn, "test")
    r2 = analyst.register_analyst(conn, "test")
    assert r1["agent_id"] == r2["agent_id"]
    row = conn.execute("SELECT connection_status, notes FROM agents"
                       " WHERE slug = 'command-analyst'").fetchone()
    assert row[0] == "configured" and "NOT connected" in row[1]
    n = conn.execute("SELECT COUNT(*) FROM agents"
                     " WHERE slug = 'command-analyst'").fetchone()[0]
    assert n == 1


# ------------------------------------------------------------------ HTTP
def test_http_status_and_owner_gate(client, raw_client):
    r = raw_client.get("/api/analyst/status")
    assert r.status_code in (401, 403)
    r = client.get("/api/analyst/status")
    assert r.status_code == 200
    body = r.json()
    assert body["agent"] == "command-analyst" and body["enabled"] is True


def test_http_ask_not_connected(client):
    r = client.post("/api/analyst/ask",
                    json={"question": "which agents are registered?"})
    assert r.status_code == 200
    assert r.json()["status"] in ("not_connected", "not_covered")
