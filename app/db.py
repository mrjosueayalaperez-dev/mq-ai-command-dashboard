"""SQLite storage for the M&Q AI Command Dashboard foundation.

Single-file SQLite database (WAL mode). The path comes from the
``MQ_CMD_DB`` environment variable, defaulting to ``data/dashboard.db``
relative to the project root. Every telemetry-bearing row carries a
mandatory ``data_source`` of ``live`` or ``demo`` — there is no default
that hides demo data.
"""

import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    project_scope TEXT NOT NULL DEFAULT 'mq-ecosystem',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    config_status TEXT NOT NULL DEFAULT 'demo_simulated',
    cost_input_per_1m INTEGER,
    cost_output_per_1m INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    owner_project TEXT NOT NULL DEFAULT 'mq-ecosystem',
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    connection_status TEXT NOT NULL DEFAULT 'demo_simulated',
    data_source TEXT NOT NULL DEFAULT 'live' CHECK (data_source IN ('live', 'demo')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id INTEGER NOT NULL REFERENCES teams(id),
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    assignment_role TEXT NOT NULL DEFAULT '',
    UNIQUE (team_id, agent_id)
);

CREATE TABLE IF NOT EXISTS agent_capabilities (
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    capability_id INTEGER NOT NULL REFERENCES capabilities(id),
    proficiency_note TEXT NOT NULL DEFAULT '',
    UNIQUE (agent_id, capability_id)
);

CREATE TABLE IF NOT EXISTS agent_model_assignments (
    agent_id INTEGER NOT NULL REFERENCES agents(id),
    model_config_id INTEGER NOT NULL REFERENCES model_configs(id),
    purpose TEXT NOT NULL DEFAULT 'primary',
    UNIQUE (agent_id, model_config_id, purpose)
);

CREATE TABLE IF NOT EXISTS evidence_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'agent',
    target_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'manual_test',
    result TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
    detail TEXT NOT NULL DEFAULT '',
    recorded_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT 'agent',
    target_id INTEGER NOT NULL,
    requested_by TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    decided_by TEXT,
    decided_at TEXT,
    decision_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    prev_hash TEXT NOT NULL
);
"""

# Phase 2 schema: tasks/executions/workflows, communications, events,
# gateway connections, usage/prices/budgets, reliability. All tables are
# records this system itself keeps — no external system is connected.
# Every telemetry-bearing table carries a mandatory data_source.
PHASE2_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    project_scope TEXT NOT NULL DEFAULT 'mq-ecosystem',
    requested_by TEXT NOT NULL DEFAULT '',
    required_capability_id INTEGER REFERENCES capabilities(id),
    assigned_agent_id INTEGER REFERENCES agents(id),
    assigned_team_id INTEGER REFERENCES teams(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'routed', 'running', 'blocked',
                          'completed', 'failed', 'cancelled')),
    priority TEXT NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('low', 'normal', 'high', 'critical')),
    routing_reason TEXT NOT NULL DEFAULT '',
    due_at TEXT,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routing_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    capability_id INTEGER REFERENCES capabilities(id),
    chosen_agent_id INTEGER REFERENCES agents(id),
    chosen_team_id INTEGER REFERENCES teams(id),
    outcome TEXT NOT NULL CHECK (outcome IN ('routed', 'unrouted')),
    reason TEXT NOT NULL DEFAULT '',
    candidates TEXT NOT NULL DEFAULT '[]',
    decided_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    agent_id INTEGER REFERENCES agents(id),
    workflow_id INTEGER,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed', 'blocked',
                          'cancelled')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms INTEGER,
    outcome_summary TEXT NOT NULL DEFAULT '',
    error_detail TEXT,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    definition TEXT NOT NULL DEFAULT '{"nodes": [], "transitions": []}',
    version INTEGER NOT NULL DEFAULT 1,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL REFERENCES executions(id),
    node_key TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    step_order INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed',
                          'blocked')),
    started_at TEXT,
    ended_at TEXT,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (execution_id, node_key)
);

CREATE TABLE IF NOT EXISTS communications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id),
    from_agent_id INTEGER REFERENCES agents(id),
    to_agent_id INTEGER REFERENCES agents(id),
    from_actor TEXT,
    channel TEXT NOT NULL DEFAULT 'dashboard',
    summary TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    context_ref TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'info'
        CHECK (severity IN ('info', 'warning', 'critical')),
    occurred_at TEXT NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    source_label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'api'
        CHECK (kind IN ('model', 'tool', 'api', 'webhook', 'repo',
                        'messaging', 'other')),
    provider TEXT NOT NULL DEFAULT '',
    config_ref TEXT NOT NULL DEFAULT '',
    config_notes TEXT NOT NULL DEFAULT '',
    access_boundary TEXT NOT NULL DEFAULT '',
    health_status TEXT NOT NULL DEFAULT 'configured',
    last_checked_at TEXT,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connection_health_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id INTEGER NOT NULL REFERENCES connections(id),
    result TEXT NOT NULL CHECK (result IN ('pass', 'fail', 'degraded')),
    detail TEXT NOT NULL DEFAULT '',
    checked_by TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_config_id INTEGER NOT NULL UNIQUE
        REFERENCES model_configs(id),
    input_per_1m_minor INTEGER,
    output_per_1m_minor INTEGER,
    currency TEXT NOT NULL DEFAULT 'USD',
    updated_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER REFERENCES agents(id),
    task_id INTEGER REFERENCES tasks(id),
    execution_id INTEGER REFERENCES executions(id),
    model_config_id INTEGER REFERENCES model_configs(id),
    project_scope TEXT NOT NULL DEFAULT 'mq-ecosystem',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_minor INTEGER,
    actual_cost_minor INTEGER,
    occurred_at TEXT NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL
        CHECK (scope_type IN ('agent', 'task', 'project', 'global')),
    scope_id TEXT,
    period TEXT NOT NULL DEFAULT 'monthly',
    limit_minor INTEGER NOT NULL,
    alert_threshold_pct INTEGER NOT NULL DEFAULT 80,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL
        CHECK (subject_type IN ('service', 'connection', 'agent')),
    subject_id INTEGER,
    check_type TEXT NOT NULL DEFAULT 'status_review',
    result TEXT NOT NULL CHECK (result IN ('pass', 'fail', 'degraded')),
    detail TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL,
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL DEFAULT 'system',
    subject_id INTEGER,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'warning'
        CHECK (severity IN ('info', 'warning', 'critical')),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'acknowledged', 'resolved')),
    opened_by TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    resolution_note TEXT NOT NULL DEFAULT '',
    data_source TEXT NOT NULL DEFAULT 'live'
        CHECK (data_source IN ('live', 'demo')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


# Real-login schema (app/auth.py, app/totp.py). Users hold an argon2id
# password hash — never a password. Sessions store only the SHA-256 of
# the session token. The throttle table stores only hashes of the
# account / client-address keys — never a raw address. Two-step factors
# store the TOTP secret AES-256-GCM-encrypted (key: MQ_CMD_TOTP_KEY or
# the totp.key file beside the database); recovery codes are salted
# SHA-256 hashes; login challenges store only the token hash.
AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT,
    role TEXT NOT NULL CHECK (role IN ('owner', 'operator', 'viewer')),
    password_hash TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    password_changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK (kind IN ('cookie', 'bearer')),
    csrf_token TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_reason TEXT
);

CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS auth_throttle (
    key_hash TEXT PRIMARY KEY,
    failures INTEGER NOT NULL DEFAULT 0,
    window_start TEXT NOT NULL,
    locked_until TEXT,
    lockouts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS totp_factors (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    secret_enc TEXT NOT NULL,
    key_check TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    enabled_at TEXT,
    last_used_step INTEGER NOT NULL DEFAULT -1
);

CREATE TABLE IF NOT EXISTS recovery_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    salt TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS login_challenges (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL CHECK (kind IN ('cookie', 'bearer')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    consumed_at TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> str:
    env = os.environ.get("MQ_CMD_DB")
    if env:
        return env
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "dashboard.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    target = path or db_path()
    if target != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executescript(PHASE2_SCHEMA)
    conn.executescript(AUTH_SCHEMA)
    conn.commit()
