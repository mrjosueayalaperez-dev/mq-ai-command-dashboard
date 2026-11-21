"""M&Q AI CONNECT — shared provider adapter (standards library).

One way for every M&Q intelligence to reach a model provider:
- OpenAI Responses API shape, `store: false` on every call.
- The model ID is configuration, never hard-coded in product logic.
- Transport is injected: production uses HTTPS via urllib; tests use a
  stub. No transport + no key = the intelligence is UNAVAILABLE and the
  product must say so honestly instead of improvising.
- Cost is computed ONLY from a fully-typed usage object returned by the
  provider, priced at the published rates below (verified 2026-11-19
  against OpenAI's official gpt-6-luna page). Any other usage shape is
  UNVERIFIED and carries no cost figure.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field

ENDPOINT = "https://api.openai.com/v1/responses"

# Published Standard short-context rates, micro-USD per 1M tokens.
RATES_MICRO_PER_1M = {
    "uncached_input": 100_000,      # $0.10
    "cached_input": 10_000,         # $0.01
    "cache_write_30m": 125_000,     # $0.125
    "cache_write_12h": 250_000,     # $0.25
    "output": 500_000,              # $0.50
}


class IntelligenceUnavailable(Exception):
    """No provider configured (no key / killed / ceiling) — say so."""


class ProviderError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class ProviderResult:
    text: str
    model: str
    usage: dict | None
    cost_micro_usd: int | None  # None = UNVERIFIED (usage schema incomplete)
    raw_status: str


def compute_cost_micro_usd(usage) -> int | None:
    """Verified cost from a strict usage object, else None (UNVERIFIED).

    Mirrors the Step 1 procedure rules: total == input + output; input
    details carry exactly cached_tokens + cache-write TTL buckets;
    output details carry only reasoning_tokens <= output.
    """
    if not isinstance(usage, dict):
        return None
    if set(usage) != {"input_tokens", "output_tokens", "total_tokens",
                      "input_tokens_details", "output_tokens_details"}:
        return None
    i, o, t = usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
           for v in (i, o, t)):
        return None
    if t != i + o:
        return None
    idd = usage["input_tokens_details"]
    if not isinstance(idd, dict) or set(idd) != {"cached_tokens", "cache_creation"}:
        return None
    cached = idd["cached_tokens"]
    cc = idd["cache_creation"]
    if isinstance(cached, bool) or not isinstance(cached, int) or cached < 0:
        return None
    if not isinstance(cc, dict) or set(cc) != {"ttl_30m_tokens", "ttl_12h_tokens"}:
        return None
    w30, w12 = cc["ttl_30m_tokens"], cc["ttl_12h_tokens"]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
           for v in (w30, w12)):
        return None
    if cached + w30 + w12 > i:
        return None
    odd = usage["output_tokens_details"]
    if not isinstance(odd, dict) or set(odd) != {"reasoning_tokens"}:
        return None
    r = odd["reasoning_tokens"]
    if isinstance(r, bool) or not isinstance(r, int) or r < 0 or r > o:
        return None
    uncached = i - cached - w30 - w12
    micro = (uncached * RATES_MICRO_PER_1M["uncached_input"]
             + cached * RATES_MICRO_PER_1M["cached_input"]
             + w30 * RATES_MICRO_PER_1M["cache_write_30m"]
             + w12 * RATES_MICRO_PER_1M["cache_write_12h"]
             + o * RATES_MICRO_PER_1M["output"])
    return -(-micro // 1_000_000)  # ceil to whole micro-USD


def _default_transport(url, headers, payload, timeout_s):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 method="POST")
    for k, v in headers.items():
        req.add_header(k, v)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}


@dataclass
class ProviderClient:
    """A configured provider door. Disabled unless a key is supplied."""
    model: str
    api_key: str | None = None
    endpoint: str = ENDPOINT
    timeout_s: int = 30
    max_output_tokens: int = 800
    transport: object = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str) -> ProviderResult:
        if not self.api_key:
            raise IntelligenceUnavailable("no provider key configured")
        transport = self.transport or _default_transport
        payload = {
            "model": self.model,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        try:
            status, body = transport(self.endpoint, headers, payload,
                                     self.timeout_s)
        except Exception as exc:  # network, timeout, TLS — one refusal path
            raise ProviderError("transport", type(exc).__name__) from exc
        if status != 200:
            raise ProviderError(f"http_{status}")
        if not isinstance(body, dict) or body.get("status") != "completed":
            raise ProviderError("response_status")
        if body.get("model") != self.model:
            raise ProviderError("model_mismatch")
        text = _extract_text(body)
        if not text:
            raise ProviderError("empty_output")
        usage = body.get("usage")
        return ProviderResult(text=text, model=self.model, usage=usage,
                              cost_micro_usd=compute_cost_micro_usd(usage),
                              raw_status=body["status"])


def _extract_text(body: dict) -> str:
    parts = []
    for item in body.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "output_text":
                    if isinstance(c.get("text"), str):
                        parts.append(c["text"])
    return "".join(parts).strip()
