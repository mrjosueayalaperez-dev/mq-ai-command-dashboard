"""Analyst signal-contract consumer tests + feature-flag consistency.

Phase 2, Task 6. Two parts:

1. app/analyst_signals.py — deterministic review of unified signal
   records (schema v1.0) against tests/fixtures/signal_records.json.
   No provider exists in these tests and none is simulated: every
   review must carry the verbatim fallback label, and a rejected
   record must never come back reclassified as qualified.

2. Feature-flag consistency (Phase 2 local correction): Phase 1 found
   MQ_ANALYST_ENABLED was honored by ask() but NOT by register/draft.
   The intended contract is consistency across the Analyst routes —
   all three refuse while the flag is off, with ask()'s semantics.
   On-state behavior is covered by tests/test_analyst.py; the
   off-state proofs live here. See FLAG-FIX-NOTE.md.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from app import analyst, analyst_signals  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.rbac import Identity  # noqa: E402

FIXTURES = json.loads(open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures",
    "signal_records.json")).read())

LABEL = "DETERMINISTIC_FALLBACK — PROVIDER_NOT_CONNECTED"


def fixture(name):
    return json.loads(json.dumps(FIXTURES[name]))  # deep copy


# ------------------------------------------------------- signal review
def test_strategy_b_qualified_long_is_consistent():
    out = analyst_signals.review_signal(fixture("strategy_b_qualified_long"))
    assert out["disposition"] == "CONSISTENT"
    assert out["contradictions"] == []
    assert out["record_direction"] == "LONG"
    assert out["record_status"] == "QUALIFIED"
    assert "lower boundary" in out["explanation"]


def test_strategy_b_midrange_rejection_agreed_never_flipped():
    out = analyst_signals.review_signal(
        fixture("strategy_b_midrange_no_trade"))
    assert out["disposition"] == "CONSISTENT"
    assert out["record_status"] == "REJECTED"
    assert out["record_direction"] == "NO_TRADE"
    assert out["reclassified"] is False
    assert "mid-range" in out["explanation"]


def test_strategy_a_qualified_short_with_not_evaluable_gates():
    out = analyst_signals.review_signal(
        fixture("strategy_a_qualified_short_alert"))
    assert out["disposition"] == "QUALIFIED_WITH_CONCERNS"
    assert out["contradictions"] == []
    assert any("not evaluable" in f for f in out["data_quality_findings"])
    assert out["record_status"] == "QUALIFIED"


def test_stale_data_rejection_is_consistent_with_finding():
    out = analyst_signals.review_signal(fixture("stale_data_rejection"))
    assert out["disposition"] == "CONSISTENT"
    assert out["record_status"] == "REJECTED"
    assert out["reclassified"] is False
    assert any("stale" in f for f in out["data_quality_findings"])


def test_contradictory_record_is_caught():
    out = analyst_signals.review_signal(fixture("contradictory_long"))
    assert out["disposition"] == "CONTRADICTION_DETECTED"
    text = " ".join(out["contradictions"])
    assert "bias" in text and "VWAP" in text and "QUALIFIED" in text


def test_populated_risk_under_unapproved_policy_is_flagged():
    out = analyst_signals.review_signal(
        fixture("risk_unapproved_populated"))
    assert out["disposition"] == "CONTRADICTION_DETECTED"
    assert any("risk_approval_status" in c for c in out["contradictions"])
    assert any("No simulated order" in c for c in out["contradictions"])


def test_incomplete_record_is_insufficient_data():
    out = analyst_signals.review_signal(fixture("incomplete_record"))
    assert out["disposition"] == "INSUFFICIENT_DATA"
    assert "classification" in out["missing_fields"]
    assert "market_state" in out["missing_fields"]


def test_fallback_label_verbatim_on_every_review():
    for name, record in FIXTURES.items():
        if name.startswith("_"):
            continue
        out = analyst_signals.review_signal(record)
        assert out["explanation_source"] == LABEL
        assert out["explanation_source"] == \
            analyst_signals.EXPLANATION_SOURCE


def test_rejected_records_are_never_reclassified():
    for name in ("strategy_b_midrange_no_trade", "stale_data_rejection"):
        out = analyst_signals.review_signal(fixture(name))
        assert out["record_status"] == "REJECTED"
        assert out["reclassified"] is False
        assert "remains rejected" in out["explanation"]


def test_batch_keeps_strategies_separate():
    batch = analyst_signals.review_batch([
        fixture("strategy_a_qualified_short_alert"),
        fixture("strategy_b_qualified_long"),
        fixture("strategy_b_midrange_no_trade")])
    assert batch["count"] == 3
    assert batch["strategies_present"] == ["STRATEGY_A", "STRATEGY_B"]
    assert batch["strategy_separation"] is not None
    assert "no Strategy A rule was applied" in \
        batch["strategy_separation"]
    assert batch["explanation_source"] == LABEL
    by_id = {r["signal_id"]: r for r in batch["reviews"]}
    # Each record judged under its own strategy's gates only.
    assert by_id["fix-a-short-003"]["disposition"] == \
        "QUALIFIED_WITH_CONCERNS"
    assert by_id["fix-b-long-001"]["disposition"] == "CONSISTENT"
    assert by_id["fix-b-mid-002"]["record_status"] == "REJECTED"


def test_batch_flags_duplicate_events():
    rec = fixture("strategy_b_qualified_long")
    batch = analyst_signals.review_batch([rec, dict(rec)])
    assert any("duplicate" in f for f in batch["batch_findings"])


# --------------------------------- pipeline-dialect normalization (F-3)
#
# Battery finding F-3: the pipeline emits classification.classification
# + qualification_status, dict-shaped gate_results, nested
# vwap{value, relationship}, and bias values like "aligned", while this
# consumer's rules read direction/status, list-shaped gates, flat
# vwap_relationship, and bias "up"/"down" — so contradiction detection
# was inert on pipeline-native records. The normalization layer in
# analyst_signals bridges the dialects; these tests pin it against the
# actual pipeline-produced fixture from the Phase 2 battery.

PIPELINE_FIXTURE = os.path.expanduser(
    "~/workspace/phase2/battery/fixtures/pipeline_b_long.json")


def _pipeline_record():
    assert os.path.exists(PIPELINE_FIXTURE), (
        "pipeline_b_long.json missing — run the battery pipeline half "
        "(test_battery.py, dash venv) first; it produces the record "
        "consumed here")
    return json.load(open(PIPELINE_FIXTURE))


def test_f3_pipeline_native_qualified_long_is_consistent():
    rec = _pipeline_record()
    assert rec["classification"]["classification"] == "LONG"  # pipeline dialect
    out = analyst_signals.review_signal(rec)
    assert out["disposition"] == "CONSISTENT"
    assert out["contradictions"] == []
    assert out["record_direction"] == "LONG"
    assert out["record_status"] == "QUALIFIED"
    assert not any("vwap_relationship is missing" in f
                   for f in out["data_quality_findings"]), (
        out["data_quality_findings"])


def test_f3_pipeline_native_contradiction_detected():
    """The battery scenario-18 mutation, applied in the pipeline
    dialect only: bias down + price below VWAP on a qualified LONG."""
    mut = json.loads(json.dumps(_pipeline_record()))
    mut["market_state"]["bias_5m"] = "down"
    mut["market_state"]["vwap"] = {"value": 99999.0,
                                   "relationship": "price below VWAP"}
    out = analyst_signals.review_signal(mut)
    assert out["disposition"] == "CONTRADICTION_DETECTED"
    assert any("bias" in c for c in out["contradictions"])
    assert any("VWAP" in c for c in out["contradictions"])


def test_f3_unresolvable_bias_is_a_finding_not_silence():
    """A bias value that cannot be resolved to a direction must be
    surfaced as a data-quality finding, never silently consistent."""
    mut = json.loads(json.dumps(_pipeline_record()))
    mut["market_state"]["bias_5m"] = "not evaluated"
    out = analyst_signals.review_signal(mut)
    assert any("bias_5m" in f and "could not be resolved" in f
               for f in out["data_quality_findings"])


def test_f3_normalization_does_not_mutate_the_record():
    rec = _pipeline_record()
    before = json.dumps(rec, sort_keys=True)
    analyst_signals.review_signal(rec)
    assert json.dumps(rec, sort_keys=True) == before


# ------------------------------------------------ feature-flag routes
@pytest.fixture()
def conn(tmp_path):
    c = connect(str(tmp_path / "flag.db"))
    init_db(c)
    now = "2026-11-20T00:00:00+00:00"
    c.execute("INSERT INTO agents (slug, display_name, role,"
              " lifecycle_status, connection_status, data_source,"
              " created_at, updated_at) VALUES ('agent-one', 'Agent"
              " One', '', 'active', 'configured', 'live', ?, ?)",
              (now, now))
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def owner():
    return Identity(user={"id": 1, "username": "test-owner",
                          "role": "owner"})


@pytest.fixture()
def flag_off(monkeypatch):
    monkeypatch.delenv("MQ_ANALYST_ENABLED", raising=False)


def test_flag_off_ask_refuses(conn, owner, flag_off):
    out = analyst.ask(conn, owner, "which agents are registered?")
    assert out["status"] == "disabled"
    assert out["text"] == "The Command Analyst is switched off."


def test_flag_off_register_refuses_and_writes_nothing(conn, flag_off):
    out = analyst.register_analyst(conn, "test-owner")
    assert out["status"] == "disabled"
    assert out["text"] == "The Command Analyst is switched off."
    n = conn.execute("SELECT COUNT(*) FROM agents"
                     " WHERE slug = 'command-analyst'").fetchone()[0]
    assert n == 0


def test_flag_off_draft_refuses_and_creates_no_approval(conn, owner,
                                                       flag_off):
    with pytest.raises(ValueError, match="switched off"):
        analyst.draft_assignment(conn, owner, {"title": "Anything"})
    n = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
    assert n == 0


def test_flag_off_http_routes_refuse(client, monkeypatch):
    monkeypatch.delenv("MQ_ANALYST_ENABLED", raising=False)
    r = client.post("/api/analyst/ask",
                    json={"question": "which agents are registered?"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    r = client.post("/api/analyst/register")
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    r = client.post("/api/analyst/draft", json={"title": "Anything"})
    assert r.status_code == 422
    assert "switched off" in r.json()["detail"]
