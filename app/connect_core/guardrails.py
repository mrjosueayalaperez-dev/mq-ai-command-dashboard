"""M&Q AI CONNECT — guardrails shared by every intelligence.

Four layers, per the founder-adopted Twin Intelligence memo:
provider hard limit (founder-set, outside this code) + in-app monthly
cost ceiling (here) + per-identity throttling (here) + kill switch
(here). Plus a usage ledger: counts and verified costs only — never
question or answer text.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field

DEFAULT_MONTHLY_CEILING_MICRO_USD = 10_000_000  # $10.00 — memo ceiling


class KilledError(Exception):
    pass


class CeilingError(Exception):
    pass


class ThrottleError(Exception):
    pass


def kill_switch_engaged() -> bool:
    return os.environ.get("MQ_INTELLIGENCE_KILLED", "") == "1"


@dataclass
class UsageLedger:
    """SQLite ledger of intelligence calls: agent, model, tokens, cost.

    Lives inside each product's OWN database (data separation): the
    caller passes its product's connection. Stores no content.
    """
    conn: sqlite3.Connection

    def init(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS intelligence_usage (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 ts REAL NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL,
                 input_tokens INTEGER, output_tokens INTEGER,
                 cost_micro_usd INTEGER, cost_verified INTEGER NOT NULL,
                 outcome TEXT NOT NULL)""")
        self.conn.commit()

    def record(self, agent: str, model: str, usage: dict | None,
               cost_micro_usd: int | None, outcome: str) -> None:
        i = usage.get("input_tokens") if isinstance(usage, dict) else None
        o = usage.get("output_tokens") if isinstance(usage, dict) else None
        self.conn.execute(
            "INSERT INTO intelligence_usage (ts, agent, model, input_tokens,"
            " output_tokens, cost_micro_usd, cost_verified, outcome)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), agent, model, i, o, cost_micro_usd,
             1 if cost_micro_usd is not None else 0, outcome))
        self.conn.commit()

    def month_spend_micro_usd(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        lt = time.localtime(now)
        start = time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_micro_usd), 0) FROM intelligence_usage"
            " WHERE ts >= ? AND cost_verified = 1", (start,)).fetchone()
        return int(row[0])


@dataclass
class Throttle:
    """Per-identity: minimum interval + hourly cap. Clock injectable."""
    min_interval_s: float = 3.0
    hourly_cap: int = 60
    _last: dict = field(default_factory=dict)
    _hour: dict = field(default_factory=dict)
    clock: object = field(default=time.time, repr=False)

    def check(self, identity: str) -> None:
        now = float(self.clock())
        if now - self._last.get(identity, -1e18) < self.min_interval_s:
            raise ThrottleError("too fast — wait a moment")
        hour = int(now // 3600)
        key = (identity, hour)
        self._hour[key] = self._hour.get(key, 0) + 1
        if self._hour[key] > self.hourly_cap:
            raise ThrottleError("hourly question limit reached")
        self._last[identity] = now


def guard_before_call(ledger: UsageLedger,
                      ceiling_micro_usd: int = DEFAULT_MONTHLY_CEILING_MICRO_USD
                      ) -> None:
    if kill_switch_engaged():
        raise KilledError("intelligence is switched off")
    if ledger.month_spend_micro_usd() >= ceiling_micro_usd:
        raise CeilingError("monthly intelligence cost ceiling reached")
