"""M&Q AI CONNECT shared intelligence standards (library, not a service).

CONNECT is a library + registry + scope pattern: each product vendors
this package and keeps its own data, keys, and switches. Nothing here
holds cross-product state.
"""
from .provider import (ENDPOINT, ProviderClient, ProviderError,
                       ProviderResult, IntelligenceUnavailable,
                       compute_cost_micro_usd)
from .guardrails import (UsageLedger, Throttle, guard_before_call,
                         kill_switch_engaged, KilledError, CeilingError,
                         ThrottleError, DEFAULT_MONTHLY_CEILING_MICRO_USD)
from .screen import screen, ScreenHit, REFUSALS
from .contracts import Narration, NarrationInvalid, parse_narration, validate_narration

__all__ = [
    "ENDPOINT", "ProviderClient", "ProviderError", "ProviderResult",
    "IntelligenceUnavailable", "compute_cost_micro_usd",
    "UsageLedger", "Throttle", "guard_before_call", "kill_switch_engaged",
    "KilledError", "CeilingError", "ThrottleError",
    "DEFAULT_MONTHLY_CEILING_MICRO_USD",
    "screen", "ScreenHit", "REFUSALS",
    "Narration", "NarrationInvalid", "parse_narration", "validate_narration",
]
