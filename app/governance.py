"""Governance Center surface (module 10).

The foundation built the machinery — approval gates, the hash-chained
audit log, RBAC. This module presents it as one governance view:
approval counts by state, audit-chain verification, the role model,
the consequential actions under gate, and the project scopes present
in the registry (project isolation is by scope label; no data crosses
scopes in any aggregate produced by this system).
"""

from . import audit as audit_mod
from .db import utcnow
from .rbac import ROLES
from .services import CONSEQUENTIAL_ACTIONS


def governance_summary(conn) -> dict:
    by_status = {status: 0 for status in
                 ("pending", "approved", "rejected", "expired")}
    for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM approvals"
            " GROUP BY status"):
        by_status[row["status"]] = row["n"]
    scopes = set()
    for table, column in (("agents", "owner_project"),
                          ("teams", "project_scope"),
                          ("tasks", "project_scope"),
                          ("usage_records", "project_scope")):
        for row in conn.execute(
                f"SELECT DISTINCT {column} AS scope FROM {table}"):
            if row["scope"]:
                scopes.add(row["scope"])
    return {
        "as_of": utcnow(),
        "approvals_by_status": by_status,
        "pending_approvals": by_status["pending"],
        "consequential_actions_gated": list(CONSEQUENTIAL_ACTIONS),
        "roles": list(ROLES),
        "role_model": "owner decides approvals and demo admin;"
                      " operator writes records; viewer is read-only"
                      " (header-based foundation mechanism — see"
                      " README for its stated limits)",
        "audit": audit_mod.verify_chain(conn),
        "project_scopes": sorted(scopes),
        "project_isolation": "records carry a project scope and are"
                             " never republished across scopes; no"
                             " external project's data is read by"
                             " this system",
    }
