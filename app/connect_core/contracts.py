"""M&Q AI CONNECT — narration contracts and citation validation.

Every intelligence speaks through the same contract: a plain-language
answer plus claims, each claim carrying the record/chunk IDs it rests
on. Validation is programmatic and blocking — an answer that cites an
ID outside the supplied evidence, or states a number the evidence does
not contain, is rejected and never shown.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


@dataclass
class Narration:
    answer: str
    claims: list = field(default_factory=list)  # [{text, record_ids[]}]


class NarrationInvalid(Exception):
    pass


_NUM_RE = re.compile(r"\$?\d[\d,]*\.?\d*\s?%?")


def _numbers(text: str) -> set:
    out = set()
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group(0).replace(",", "").replace("$", "").strip()
        try:
            out.add(float(raw.rstrip("%")))
        except ValueError:
            pass
    return out


def parse_narration(raw_text: str) -> Narration:
    """Parse the model's strict-JSON narration; anything else is invalid."""
    try:
        body = json.loads(raw_text)
    except Exception as exc:
        raise NarrationInvalid("narration is not JSON") from exc
    if not isinstance(body, dict) or set(body) - {"answer", "claims"}:
        raise NarrationInvalid("narration schema")
    answer = body.get("answer")
    claims = body.get("claims")
    if not isinstance(answer, str) or not answer.strip():
        raise NarrationInvalid("empty answer")
    if not isinstance(claims, list):
        raise NarrationInvalid("claims not a list")
    for c in claims:
        if not isinstance(c, dict) or set(c) != {"text", "record_ids"}:
            raise NarrationInvalid("claim schema")
        if not isinstance(c["text"], str) or not c["text"].strip():
            raise NarrationInvalid("claim text")
        if not isinstance(c["record_ids"], list) or not c["record_ids"]:
            raise NarrationInvalid("claim without citations")
        if any(not isinstance(r, (str, int)) for r in c["record_ids"]):
            raise NarrationInvalid("citation type")
    return Narration(answer=answer.strip(), claims=claims)


def validate_narration(n: Narration, allowed_ids: set,
                       allowed_numbers: set) -> None:
    """Blocking check: citations exist in the evidence; numbers match it."""
    for c in n.claims:
        for rid in c["record_ids"]:
            if rid not in allowed_ids and str(rid) not in {str(a) for a in allowed_ids}:
                raise NarrationInvalid(f"citation {rid!r} not in evidence")
    stated = _numbers(n.answer)
    for c in n.claims:
        stated |= _numbers(c["text"])
    allowed = {float(a) for a in allowed_numbers}
    for v in stated:
        if not any(abs(v - a) < 1e-9 for a in allowed):
            raise NarrationInvalid(f"number {v} not in evidence")
