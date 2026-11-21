"""Correction Round 2 — field-level trace verification (Zelator).

The Analyst must see the pipeline's canonical record EXACTLY: the same
VWAP (value + relationship), the same bias (in normalized form), the
same price, the same strategy identity, the same timestamp. This test
loads the battery's REAL pipeline output fixture
(phase2/battery/fixtures/pipeline_b_long.json — produced by running
the actual SignalPipeline, not hand-written) and compares the
Analyst-side normalized values against values READ FROM THE FIXTURE at
test time. Nothing is hard-coded from the fixture: if the pipeline's
output or the normalization ever drifts, this test fails.

Also pinned: the verbatim provider label, reclassified=False, and the
non-mutation guarantee (reviewing a record never alters the canonical
record as stored).
"""
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import analyst_signals  # noqa: E402

LABEL = "DETERMINISTIC_FALLBACK — PROVIDER_NOT_CONNECTED"

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "battery" / "fixtures" / "pipeline_b_long.json"
)


def _fixture():
    return json.loads(FIXTURE_PATH.read_text())


def _expected_vwap_relationship(fixture):
    rel = fixture["market_state"]["vwap"]["relationship"]
    if "above" in rel:
        return "above"
    if "below" in rel:
        return "below"
    raise AssertionError(f"unrecognized fixture VWAP relationship: {rel!r}")


def _expected_bias(fixture):
    bias = fixture["market_state"]["bias_5m"]
    direction = fixture["classification"]["classification"]
    if bias in ("up", "down"):
        return bias
    if bias == "aligned":
        return {"LONG": "up", "SHORT": "down"}[direction]
    if isinstance(bias, str) and bias.startswith("not aligned"):
        return {"LONG": "down", "SHORT": "up"}[direction]
    raise AssertionError(f"unrecognized fixture bias vocabulary: {bias!r}")


def _expected_status(fixture):
    qs = fixture["classification"]["qualification_status"]
    if qs.startswith("qualified"):
        return "QUALIFIED"
    if qs == "rejected":
        return "REJECTED"
    return "NOT_QUALIFIED"


def test_normalized_record_matches_pipeline_fixture_field_for_field():
    fixture = _fixture()
    normalized, norm_findings = analyst_signals._normalize_pipeline_dialect(
        copy.deepcopy(fixture))

    fm = fixture["market_state"]
    nm = normalized["market_state"]
    # VWAP: the Analyst sees the exact value, and the relationship in
    # its internal vocabulary — mapped from the fixture's own phrase.
    assert nm["vwap"] == fm["vwap"]["value"]
    assert nm["vwap_relationship"] == _expected_vwap_relationship(fixture)
    # Bias: exact normalized form of the fixture's bias, resolved
    # against the fixture's own classification.
    assert nm["bias_5m"] == _expected_bias(fixture)
    # Price: bit-for-bit the pipeline's number.
    assert nm["price"] == fm["price"]
    # Freshness vocabulary: FRESH -> fresh.
    assert nm["freshness"] == fm["data_freshness"].lower()
    # Strategy identity and version: unchanged.
    assert normalized["strategy_id"] == fixture["strategy_id"]
    assert normalized["strategy_version"] == fixture["strategy_version"]
    # Timestamps: unchanged, both the record's and the candle's.
    assert normalized["data_timestamp"] == fixture["data_timestamp"]
    assert nm["candle_timestamp"] == fm["candle_timestamp"]
    # Classification: direction and status mapped from the fixture's.
    ncls = normalized["classification"]
    assert ncls["direction"] == fixture["classification"]["classification"]
    assert ncls["status"] == _expected_status(fixture)
    # Gate results: same gates, same results, list-shaped.
    fixture_gates = fixture["classification"]["gate_results"]
    normalized_gates = {g["gate"]: g["result"] for g in ncls["gate_results"]}
    assert normalized_gates == fixture_gates
    # This clean qualified fixture must normalize without findings.
    assert norm_findings == []


def test_review_of_pipeline_fixture_consumes_the_exact_values():
    fixture = _fixture()
    review = analyst_signals.review_signal(copy.deepcopy(fixture))

    assert review["signal_id"] == fixture["signal_id"]
    assert review["strategy_id"] == fixture["strategy_id"]
    assert review["strategy_version"] == fixture["strategy_version"]
    assert review["record_direction"] == fixture["classification"]["classification"]
    assert review["record_status"] == _expected_status(fixture)
    # The review actually consumed the normalized market fields.
    assert "market_state.bias_5m" in review["cited_fields"]
    assert "market_state.vwap_relationship" in review["cited_fields"]
    # The F-3 symptom must stay dead: no claim that the VWAP
    # relationship is missing on a pipeline-native record.
    assert not any(
        "vwap_relationship" in f and "missing" in f
        for f in review["data_quality_findings"]
    )
    assert review["disposition"] == "CONSISTENT"
    assert review["contradictions"] == []
    # Standing invariants.
    assert review["explanation_source"] == LABEL
    assert review["reclassified"] is False


def test_review_does_not_mutate_the_canonical_record():
    fixture = _fixture()
    before = copy.deepcopy(fixture)
    analyst_signals.review_signal(fixture)
    assert fixture == before
