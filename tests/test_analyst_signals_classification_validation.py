"""Classification completeness validation — fail-closed correction.

Zelator's outstanding requirement from the Analyst review (restated
Nov 21, 2026): the classification validator must reject missing or
invalid direction/status fields rather than allowing an incomplete
classification to reach CONSISTENT.

The defect, in app/analyst_signals.py review_signal(): the sufficiency
check (``missing``) covered only ESSENTIAL_FIELDS plus wholly absent
classification / market_state blocks. It never examined
classification.direction or classification.status themselves, so a
record whose classification block was present but carried no
direction, an out-of-vocabulary direction, no status, or an
out-of-vocabulary status fell through every contradiction rule (they
key on LONG/SHORT and QUALIFIED) and landed on CONSISTENT.

The recognized vocabularies are the module's own — the values
_normalize_pipeline_dialect maps the pipeline dialect into and the
internal dialect carries:

- direction: "LONG", "SHORT", "NO_TRADE"
- status:    "QUALIFIED", "REJECTED", "NOT_QUALIFIED"

An incomplete or out-of-vocabulary classification is a record that
cannot be reviewed at all, so it takes the existing INSUFFICIENT_DATA
disposition, with the offending field named in missing_fields (and,
when a value was present but invalid, a data-quality finding quoting
that value). Well-formed records classify exactly as before — the
baseline pins below cover every shipped fixture.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import analyst_signals  # noqa: E402

FIXTURES = json.loads(open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures",
    "signal_records.json")).read())


def fixture(name):
    return json.loads(json.dumps(FIXTURES[name]))  # deep copy


def _mutated(name, **classification_changes):
    rec = fixture(name)
    for key, value in classification_changes.items():
        if value is _DELETE:
            rec["classification"].pop(key, None)
        else:
            rec["classification"][key] = value
    return rec


class _Delete:
    pass


_DELETE = _Delete()


# ------------------------------------------- the four defect cases
def test_missing_direction_fails_closed():
    out = analyst_signals.review_signal(
        _mutated("strategy_b_qualified_long", direction=_DELETE))
    assert out["record_direction"] is None  # echoed honestly, unchanged
    assert out["disposition"] == "INSUFFICIENT_DATA"
    assert "classification.direction" in out["missing_fields"]


def test_invalid_direction_fails_closed():
    out = analyst_signals.review_signal(
        _mutated("strategy_b_qualified_long", direction="SIDEWAYS"))
    assert out["record_direction"] == "SIDEWAYS"
    assert out["disposition"] == "INSUFFICIENT_DATA"
    assert "classification.direction" in out["missing_fields"]
    assert any("SIDEWAYS" in f for f in out["data_quality_findings"])


def test_missing_status_fails_closed():
    out = analyst_signals.review_signal(
        _mutated("strategy_b_qualified_long", status=_DELETE))
    assert out["record_status"] is None
    assert out["disposition"] == "INSUFFICIENT_DATA"
    assert "classification.status" in out["missing_fields"]


def test_invalid_status_fails_closed():
    out = analyst_signals.review_signal(
        _mutated("strategy_b_qualified_long", status="PENDING"))
    assert out["record_status"] == "PENDING"
    assert out["disposition"] == "INSUFFICIENT_DATA"
    assert "classification.status" in out["missing_fields"]
    assert any("PENDING" in f for f in out["data_quality_findings"])


def test_pipeline_dialect_unresolvable_direction_fails_closed():
    """Pipeline dialect: classification.classification outside
    LONG/SHORT/NO_TRADE cannot be normalized into a direction, so the
    classification is incomplete and must not reach CONSISTENT."""
    rec = fixture("strategy_b_qualified_long")
    rec["classification"] = {
        "classification": "FLAT",
        "qualification_status": "qualified",
        "gate_results": {},
    }
    out = analyst_signals.review_signal(rec)
    assert out["record_direction"] is None
    assert out["disposition"] == "INSUFFICIENT_DATA"
    assert "classification.direction" in out["missing_fields"]


# ------------------------------- well-formed records: no change
def test_wellformed_fixtures_classify_exactly_as_before():
    expected = {
        "strategy_b_qualified_long": "CONSISTENT",
        "strategy_b_midrange_no_trade": "CONSISTENT",
        "stale_data_rejection": "CONSISTENT",
        "strategy_a_qualified_short_alert": "QUALIFIED_WITH_CONCERNS",
        "contradictory_long": "CONTRADICTION_DETECTED",
        "risk_unapproved_populated": "CONTRADICTION_DETECTED",
        "incomplete_record": "INSUFFICIENT_DATA",
    }
    for name, disposition in expected.items():
        out = analyst_signals.review_signal(fixture(name))
        assert out["disposition"] == disposition, name
        assert out["reclassified"] is False, name


def test_not_qualified_status_vocabulary_is_accepted():
    """NOT_QUALIFIED is produced by the module's own normalization for
    pipeline records; it is in-vocabulary and must not fail closed."""
    rec = _mutated("strategy_b_midrange_no_trade", status="NOT_QUALIFIED")
    out = analyst_signals.review_signal(rec)
    assert "classification.status" not in out["missing_fields"]
    assert out["disposition"] == "CONSISTENT"
