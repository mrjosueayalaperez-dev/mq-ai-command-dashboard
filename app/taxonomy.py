"""Status taxonomy for the M&Q AI Command Dashboard.

The eight connection/verification states from the specification (Section 6)
are the ONLY legal values for agent and model-config connection statuses.
Promotion to ``verified`` or ``operational`` is evidence-gated: it requires a
linked passing evidence record. Promotion to ``operational`` additionally
requires the governance approval flow (see app.services / app.main).
"""

STATUSES = (
    "demo_simulated",
    "configured",
    "connected",
    "verified",
    "operational",
    "degraded",
    "disconnected",
    "failed",
)

STATUS_SET = frozenset(STATUSES)

#: Statuses that may only be granted with a linked passing evidence record.
EVIDENCE_GATED = frozenset({"verified", "operational"})

#: Statuses that may only be granted through the approval flow (owner-decided).
APPROVAL_GATED = frozenset({"operational"})

#: Lifecycle states for a registry entry (separate from connection status).
LIFECYCLE_STATUSES = ("active", "paused", "deactivated")


def is_valid_status(value: str) -> bool:
    return value in STATUS_SET


def requires_evidence(value: str) -> bool:
    return value in EVIDENCE_GATED


def requires_approval(value: str) -> bool:
    return value in APPROVAL_GATED
