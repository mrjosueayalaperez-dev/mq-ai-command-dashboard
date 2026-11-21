"""Command Analyst — deterministic review of structured signal records.

Phase 2, Task 6. The MNQ Dashboard's strategies emit a unified,
versioned signal record (signal contract, schema v1.0). This module is
the Analyst's consumer of that contract: a purely deterministic review
that explains — from the record's own fields, inventing nothing —
why a strategy qualified or rejected, checks the record for internal
contradictions and data-quality problems, and keeps Strategy A and
Strategy B strictly separate.

Hard constraints, same as the rest of the Analyst:

- NO provider is connected. Every review carries the verbatim label
  ``DETERMINISTIC_FALLBACK — PROVIDER_NOT_CONNECTED`` as its
  ``explanation_source``. No live model call is made, simulated, or
  claimed anywhere in this module.
- The Analyst explains and questions; it is never an execution
  authority. A record the deterministic strategy REJECTED stays
  rejected: a review may agree with the rejection or raise concerns
  about it, but it can never reclassify the record as qualified.
  ``reclassified`` is therefore always False and the review always
  echoes the record's own classification unchanged.
- Numbers and facts in the explanation are quoted only from the
  record itself (cite-or-refuse, applied to signal records).

This module reads no database and writes nothing; it consumes one
signal-contract record (a dict) or a batch of them.

Dialect normalization (battery finding F-3): two field dialects of the
same contract exist. The signal pipeline emits
``classification.classification`` + ``qualification_status``,
dict-shaped ``gate_results``, nested ``vwap{value, relationship}``,
``data_freshness``, and a bias vocabulary of "aligned" / "not aligned"
(relative to the signal direction). This consumer's rules engine reads
``classification.direction`` + ``status``, list-shaped ``gate_results``,
flat ``vwap_relationship``, ``freshness``, and bias "up"/"down".
``_normalize_pipeline_dialect`` maps the pipeline dialect into the
internal shape at the top of every review; records already in the
internal dialect pass through unchanged. A bias value that cannot be
resolved to a direction is surfaced as a data-quality finding — never
silently treated as consistent.
"""
from __future__ import annotations

SCHEMA_VERSION = "1.0"

#: Verbatim on every review output. No provider is connected; this
#: deterministic review is the fallback, and it says so.
EXPLANATION_SOURCE = "DETERMINISTIC_FALLBACK — PROVIDER_NOT_CONNECTED"

DISPOSITIONS = ("CONSISTENT", "QUALIFIED_WITH_CONCERNS",
                "CONTRADICTION_DETECTED", "INSUFFICIENT_DATA")

#: Canonical strategy versions known to this consumer. A record whose
#: strategy_version does not match its strategy_id is a contradiction:
#: the record claims one strategy's identity with another's version.
KNOWN_STRATEGY_VERSIONS = {"STRATEGY_B": "MQU-SB-001 v1.0"}

#: Fields without which a record cannot be reviewed at all.
ESSENTIAL_FIELDS = ("signal_id", "schema_version", "strategy_id",
                    "strategy_version")

#: Risk fields that constitute a populated risk record. Populated risk
#: under an UNAPPROVED risk policy is a contradiction: the paper
#: execution gate must refuse such a record (Phase 2 order, Task 5).
RISK_FIELDS = ("entry", "stop", "target", "quantity",
               "max_simulated_exposure")


def _classification(record: dict) -> dict:
    c = record.get("classification")
    return c if isinstance(c, dict) else {}


def _market(record: dict) -> dict:
    m = record.get("market_state")
    return m if isinstance(m, dict) else {}


def _risk(record: dict) -> dict:
    r = record.get("risk")
    return r if isinstance(r, dict) else {}


def _gates(record: dict) -> list:
    gates = _classification(record).get("gate_results")
    return gates if isinstance(gates, list) else []


def _fmt(value) -> str:
    return str(value)


#: Pipeline VWAP relationship phrases -> internal vocabulary.
_VWAP_RELATIONSHIP_MAP = {
    "price above VWAP": "above",
    "price below VWAP": "below",
    "above": "above",
    "below": "below",
}


def _normalize_pipeline_dialect(record: dict):
    """Map a pipeline-native signal record into the internal dialect
    this module's rules read (finding F-3). Returns (record, findings):
    a shallow-copied record (the caller's record is never mutated) and
    a list of data-quality findings produced by normalization itself.

    Each field is normalized independently and only when it is in the
    pipeline shape, so records already in the internal (fixture)
    dialect pass through byte-identical in behavior.
    """
    findings: list = []
    cls_in = record.get("classification")
    market_in = record.get("market_state")
    if not isinstance(cls_in, dict) and not isinstance(market_in, dict):
        return record, findings
    out = dict(record)
    cls = dict(cls_in) if isinstance(cls_in, dict) else {}
    market = dict(market_in) if isinstance(market_in, dict) else {}

    # ---- classification: direction + status
    if isinstance(cls_in, dict):
        if "direction" not in cls and cls.get("classification") in (
                "LONG", "SHORT", "NO_TRADE"):
            cls["direction"] = cls["classification"]
        if "status" not in cls:
            qs = cls.get("qualification_status")
            if isinstance(qs, str) and qs.startswith("qualified"):
                # "qualified" and "qualified_alert_reported" both mean
                # the deterministic layer qualified the record.
                cls["status"] = "QUALIFIED"
            elif qs == "rejected":
                cls["status"] = "REJECTED"
            elif qs is not None:
                cls["status"] = "NOT_QUALIFIED"
        gates = cls.get("gate_results")
        if isinstance(gates, dict):
            cls["gate_results"] = [
                {"gate": name, "result": result}
                for name, result in gates.items()
            ]
        out["classification"] = cls

    if isinstance(market_in, dict):
        # ---- VWAP: nested {value, relationship} -> flat fields
        vwap = market.get("vwap")
        if isinstance(vwap, dict):
            if "vwap_relationship" not in market:
                rel = _VWAP_RELATIONSHIP_MAP.get(vwap.get("relationship"))
                if rel is not None:
                    market["vwap_relationship"] = rel
            market["vwap"] = vwap.get("value")
        # ---- freshness: FRESH/STALE -> fresh/stale
        if "freshness" not in market and market.get("data_freshness") in (
                "FRESH", "STALE"):
            market["freshness"] = market["data_freshness"].lower()
        # ---- bias vocabulary: "aligned" is relative to the direction
        bias = market.get("bias_5m")
        if isinstance(bias, str) and bias not in ("up", "down"):
            direction = cls.get("direction")
            resolved = None
            if bias == "aligned":
                resolved = {"LONG": "up", "SHORT": "down"}.get(direction)
            elif bias.startswith("not aligned"):
                resolved = {"LONG": "down", "SHORT": "up"}.get(direction)
            if resolved is not None:
                market["bias_5m"] = resolved
            else:
                findings.append(
                    f"market_state.bias_5m value {bias!r} (pipeline"
                    " vocabulary) could not be resolved to an up/down"
                    " direction against the record's classification;"
                    " bias consistency was not evaluated.")
        out["market_state"] = market
    return out, findings


def _explain(record: dict, cited: list) -> str:
    """Plain-language account of the record, built ONLY from its own
    gate results and stated reasons. Nothing is inferred."""
    cls = _classification(record)
    strategy = record.get("strategy_id", "unknown strategy")
    version = record.get("strategy_version", "")
    direction = cls.get("direction", "NO_TRADE")
    status = cls.get("status", "UNKNOWN")
    parts = [f"{strategy} ({version}) record"
             f" {record.get('signal_id', '?')} was classified"
             f" {direction} with status {status}."]
    cited += ["strategy_id", "strategy_version", "signal_id",
              "classification.direction", "classification.status"]
    for gate in _gates(record):
        name = gate.get("gate", "?")
        result = gate.get("result", "?")
        reason = gate.get("reason")
        line = f"Gate {name}: {result}"
        if reason:
            line += f" — {reason}"
        parts.append(line + ".")
        cited.append(f"classification.gate_results[{name}]")
    for key, label in (("qualification_reasons", "Qualification"),
                       ("rejection_reasons", "Rejection"),
                       ("missing_data_reasons", "Missing data"),
                       ("stale_data_reasons", "Stale data")):
        for reason in cls.get(key) or []:
            parts.append(f"{label}: {reason}.")
            cited.append(f"classification.{key}")
    if status == "REJECTED":
        parts.append("The Analyst does not reclassify rejections: this"
                     " record remains rejected regardless of this"
                     " review.")
    return " ".join(parts)


def review_signal(record: dict) -> dict:
    """Deterministically review one signal-contract record (schema
    v1.0). Returns a structured review; never mutates the record and
    never changes its classification."""
    if not isinstance(record, dict):
        record = {}
    # Dialect normalization first (finding F-3): pipeline-native
    # records are mapped into the internal shape the rules below read;
    # normalization findings join the review's data-quality findings.
    record, norm_findings = _normalize_pipeline_dialect(record)
    cls = _classification(record)
    market = _market(record)
    risk = _risk(record)
    direction = cls.get("direction")
    status = cls.get("status")
    cited: list = []
    contradictions: list = []
    findings: list = []
    findings.extend(norm_findings)

    # ---------------------------------------------------- sufficiency
    missing = [f for f in ESSENTIAL_FIELDS if not record.get(f)]
    if not cls:
        missing.append("classification")
    if not market:
        missing.append("market_state")
    if record.get("schema_version") not in (None, SCHEMA_VERSION):
        findings.append(
            f"schema_version is {record.get('schema_version')!r},"
            f" not the {SCHEMA_VERSION} contract this consumer"
            " implements.")

    # -------------------------------------------------- data quality
    freshness = market.get("freshness")
    if freshness is not None:
        cited.append("market_state.freshness")
    if freshness == "stale":
        if status == "QUALIFIED":
            contradictions.append(
                "Record is QUALIFIED on stale data"
                " (market_state.freshness = 'stale'); a stale feed"
                " must not generate a valid signal.")
        else:
            findings.append(
                "Data was stale (market_state.freshness = 'stale');"
                " the record's own stale_data_reasons are the stated"
                " basis.")
    candle_closed = market.get("candle_closed")
    if candle_closed is not None:
        cited.append("market_state.candle_closed")
    if candle_closed is False and status == "QUALIFIED":
        contradictions.append(
            "Record is QUALIFIED while market_state.candle_closed is"
            " false — an open candle was treated as a closed"
            " confirmation.")
    elif candle_closed is False:
        findings.append("Confirming candle is still open"
                        " (market_state.candle_closed = false).")
    for field in ("price", "bias_5m", "vwap_relationship"):
        if market.get(field) is None:
            findings.append(f"market_state.{field} is missing.")
    not_evaluable = [g.get("gate", "?") for g in _gates(record)
                     if g.get("result") == "NOT_EVALUABLE"]
    if not_evaluable:
        findings.append(
            "Gates not evaluable from the available data: "
            + ", ".join(not_evaluable) + ".")

    # ------------------------------------------------- contradictions
    bias = market.get("bias_5m")
    vwap_rel = market.get("vwap_relationship")
    if bias is not None:
        cited.append("market_state.bias_5m")
    if vwap_rel is not None:
        cited.append("market_state.vwap_relationship")
    if direction == "LONG":
        if bias == "down":
            contradictions.append(
                "Classification is LONG while the 5-minute bias is"
                " down (market_state.bias_5m = 'down').")
        if vwap_rel == "below":
            contradictions.append(
                "Classification is LONG while price is below VWAP"
                " (market_state.vwap_relationship = 'below').")
    if direction == "SHORT":
        if bias == "up":
            contradictions.append(
                "Classification is SHORT while the 5-minute bias is"
                " up (market_state.bias_5m = 'up').")
        if vwap_rel == "above":
            contradictions.append(
                "Classification is SHORT while price is above VWAP"
                " (market_state.vwap_relationship = 'above').")
    failed = [g.get("gate", "?") for g in _gates(record)
              if g.get("result") == "FAIL"]
    if status == "QUALIFIED" and failed:
        contradictions.append(
            "Record is QUALIFIED while required gate(s) failed: "
            + ", ".join(failed) + ".")
    populated_risk = [f for f in RISK_FIELDS if risk.get(f) is not None]
    approval = risk.get("risk_approval_status")
    if approval is not None:
        cited.append("risk.risk_approval_status")
    if populated_risk and approval != "APPROVED":
        contradictions.append(
            "Risk fields are populated ("
            + ", ".join(f"risk.{f}" for f in populated_risk)
            + f") while risk.risk_approval_status is"
            f" {_fmt(approval)!r}, not 'APPROVED'. No simulated order"
            " may be created from this record.")
        cited += [f"risk.{f}" for f in populated_risk]
    expected_version = KNOWN_STRATEGY_VERSIONS.get(
        record.get("strategy_id"))
    if expected_version and record.get("strategy_version") \
            != expected_version:
        contradictions.append(
            f"strategy_id {record.get('strategy_id')!r} carries"
            f" strategy_version {record.get('strategy_version')!r};"
            f" the canonical version is {expected_version!r} —"
            " strategy identity/version mismatch.")
    nested_strategy = cls.get("strategy_id")
    if nested_strategy and nested_strategy != record.get("strategy_id"):
        contradictions.append(
            f"classification.strategy_id {nested_strategy!r} disagrees"
            f" with the record's strategy_id"
            f" {record.get('strategy_id')!r}.")

    # ---------------------------------------------------- disposition
    # Findings qualify a QUALIFICATION (QUALIFIED_WITH_CONCERNS). On a
    # record the strategy already REJECTED, the same findings are the
    # documented basis for the rejection — fail-closed behavior the
    # review agrees with — so they do not downgrade the disposition.
    if missing:
        disposition = "INSUFFICIENT_DATA"
    elif contradictions:
        disposition = "CONTRADICTION_DETECTED"
    elif findings and status == "QUALIFIED":
        disposition = "QUALIFIED_WITH_CONCERNS"
    else:
        disposition = "CONSISTENT"

    return {
        "signal_id": record.get("signal_id"),
        "schema_version": record.get("schema_version"),
        "strategy_id": record.get("strategy_id"),
        "strategy_version": record.get("strategy_version"),
        # The record's own classification, echoed unchanged. The
        # Analyst NEVER reclassifies — least of all a rejection.
        "record_direction": direction,
        "record_status": status,
        "reclassified": False,
        "disposition": disposition,
        "explanation": _explain(record, cited),
        "explanation_source": EXPLANATION_SOURCE,
        "contradictions": contradictions,
        "data_quality_findings": findings,
        "missing_fields": missing,
        "cited_fields": sorted(set(cited)),
    }


def review_batch(records: list) -> dict:
    """Review a batch of signal records. Strategy A and Strategy B
    records are reviewed under their own rules and are never blended:
    no gate, reason, or rule from one strategy is applied to the
    other, and a batch spanning both says so explicitly."""
    reviews = [review_signal(r) for r in records]
    strategies = sorted({r.get("strategy_id") for r in records
                         if isinstance(r, dict) and r.get("strategy_id")})
    separation = None
    if len(strategies) > 1:
        separation = (
            "Strategy separation: this batch contains "
            + " and ".join(strategies)
            + ". Each record was reviewed strictly under its own"
            " strategy's gates and reasons; no Strategy A rule was"
            " applied to a Strategy B record or vice versa, and no"
            " blended classification was produced.")
    seen_ids, seen_keys, duplicates = set(), set(), []
    for r in records:
        if not isinstance(r, dict):
            continue
        sid, key = r.get("signal_id"), r.get("idempotency_key")
        if sid and sid in seen_ids:
            duplicates.append(f"duplicate signal_id {sid!r}")
        if key and key in seen_keys:
            duplicates.append(f"duplicate idempotency_key {key!r}")
        seen_ids.add(sid)
        seen_keys.add(key)
    return {
        "reviews": reviews,
        "count": len(reviews),
        "strategies_present": strategies,
        "strategy_separation": separation,
        "batch_findings": duplicates,
        "explanation_source": EXPLANATION_SOURCE,
    }
