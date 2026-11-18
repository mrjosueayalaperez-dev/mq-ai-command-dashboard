"use strict";

// M&Q AI Command Dashboard — foundation front end.
// Read-only Executive Overview. All interface chrome comes from the
// four-language catalogs in /i18n/<lang>.json; data comes from
// /api/overview. Demo data is labeled demo — never blended, never
// presented as connected or operational.

// Base path this UI is served under: "" at the site root, or a mount
// prefix such as "/command" when the app is mounted under a path prefix
// (the combined PythonAnywhere deployment). Derived at runtime from
// this script's own URL, so the same files work in both layouts.
const BASE = (() => {
  const src = (document.currentScript || {}).src || "";
  const p = new URL(src, window.location.href).pathname;
  return p.endsWith("/app.js") ? p.slice(0, -"/app.js".length) : "";
})();

const STATUSES = [
  "demo_simulated", "configured", "connected", "verified",
  "operational", "degraded", "disconnected", "failed",
];

let catalog = {};
let currentLang = "en";

async function loadCatalog(lang) {
  const res = await fetch(`${BASE}/i18n/${lang}.json`);
  catalog = await res.json();
  currentLang = lang;
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (catalog[key]) el.textContent = catalog[key];
  });
}

function t(key) {
  return catalog[key] || key;
}

function renderOverview(data) {
  document.getElementById("as-of").textContent = data.as_of || "—";
  const sourceEl = document.getElementById("data-source");
  sourceEl.textContent =
    data.data_source === "demo" ? t("label_source_demo")
    : data.data_source === "mixed" ? t("label_source_mixed")
    : t("label_source_live");

  const banner = document.getElementById("demo-banner");
  banner.hidden = !data.demo_data_present;

  document.getElementById("total-agents").textContent = data.total_agents;
  document.getElementById("team-count").textContent = data.teams.length;
  document.getElementById("pending-count").textContent =
    data.pending_approval_count;

  const grid = document.getElementById("status-grid");
  grid.innerHTML = "";
  for (const status of STATUSES) {
    const cell = document.createElement("div");
    cell.className = "status-cell" + (status === "demo_simulated" ? " is-demo" : "");
    const n = document.createElement("span");
    n.className = "n";
    n.textContent = data.counts_by_status[status] ?? 0;
    const s = document.createElement("span");
    s.className = "s";
    s.textContent = t(`status_${status}`);
    cell.append(n, s);
    grid.appendChild(cell);
  }

  const teamList = document.getElementById("team-list");
  teamList.innerHTML = "";
  for (const team of data.teams) {
    const li = document.createElement("li");
    li.textContent = `${team.display_name} — ${team.member_count} ` +
      `${t("label_members")}`;
    teamList.appendChild(li);
  }

  const approvalList = document.getElementById("approval-list");
  approvalList.innerHTML = "";
  document.getElementById("no-approvals").hidden =
    data.pending_approvals.length > 0;
  for (const ap of data.pending_approvals) {
    const li = document.createElement("li");
    li.textContent = `#${ap.id} · ${ap.action_type} · ` +
      `${t("label_agent")} #${ap.target_id} · ${ap.requested_by}`;
    approvalList.appendChild(li);
  }

  const auditList = document.getElementById("audit-list");
  auditList.innerHTML = "";
  for (const ev of data.recent_audit) {
    const li = document.createElement("li");
    li.textContent = `${ev.created_at} · ${ev.actor} · ${ev.action} · ` +
      `${ev.target_type} #${ev.target_id ?? "—"}`;
    auditList.appendChild(li);
  }
}

async function refresh() {
  const res = await apiFetch("/api/overview");
  renderOverview(await res.json());
  await refreshOperations();
  await renderNetwork();
}

document.getElementById("lang-select").addEventListener("change", async (e) => {
  await loadCatalog(e.target.value);
  await refresh();
});

// ------------------------------------------------------------ view switching

document.querySelectorAll(".view-link").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".view-link").forEach((b) =>
      b.classList.toggle("is-active", b === btn));
    for (const view of ["command", "operations", "network"]) {
      document.getElementById(`view-${view}`).hidden =
        view !== btn.dataset.view;
    }
  });
});

// --------------------------------------- Operations Control Room (Concept B)

function sourceLabel(source) {
  return source === "demo" ? t("label_source_demo")
    : source === "system" ? t("label_source_live") + " · audit"
    : t("label_source_live");
}

async function refreshOperations() {
  const [activity, executions, performance, economics, prices,
         connections, reliability] = await Promise.all([
    apiFetch("/api/activity?limit=30").then((r) => r.json()),
    apiFetch("/api/executions").then((r) => r.json()),
    apiFetch("/api/performance").then((r) => r.json()),
    apiFetch("/api/economics/summary").then((r) => r.json()),
    apiFetch("/api/model-prices").then((r) => r.json()),
    apiFetch("/api/connections").then((r) => r.json()),
    apiFetch("/api/reliability/summary").then((r) => r.json()),
  ]);
  document.getElementById("ops-as-of").textContent = activity.as_of;

  const counts = activity.counts_by_source;
  document.getElementById("activity-counts").textContent =
    `${t("label_source_live")}: ${counts.live} · ` +
    `${t("label_source_demo")}: ${counts.demo} · ` +
    `audit: ${counts.system}`;
  const actList = document.getElementById("activity-list");
  actList.innerHTML = "";
  for (const entry of activity.entries) {
    const li = document.createElement("li");
    if (entry.data_source === "demo") li.className = "is-demo";
    li.textContent = `${entry.occurred_at} · ${entry.source_label} · ` +
      `${entry.event_type} · ${entry.detail}`;
    actList.appendChild(li);
  }

  const execList = document.getElementById("execution-list");
  execList.innerHTML = "";
  for (const ex of executions.slice(0, 20)) {
    const li = document.createElement("li");
    if (ex.data_source === "demo") li.className = "is-demo";
    li.textContent = `#${ex.id} · task #${ex.task_id} · ` +
      `${ex.status}` +
      (ex.duration_ms != null ? ` · ${ex.duration_ms} ms` : "") +
      (ex.data_source === "demo" ? ` · ${t("label_source_demo")}` : "");
    execList.appendChild(li);
  }

  const perfBox = document.getElementById("performance-summary");
  perfBox.innerHTML = "";
  for (const source of ["live", "demo"]) {
    const block = performance.by_source[source];
    const card = document.createElement("div");
    card.className = "card";
    const value = document.createElement("span");
    value.className = "card-value";
    value.textContent = block.executions_recorded;
    const label = document.createElement("span");
    label.className = "card-label";
    label.textContent =
      `${t("label_executions")} · ${sourceLabel(source)}` +
      (block.executions_recorded === 0
        ? ` — ${t("label_no_executions")}`
        : block.success_rate != null
          ? ` — ${(block.success_rate * 100).toFixed(1)}% ✓`
          : "");
    card.append(value, label);
    perfBox.appendChild(card);
  }

  const econBox = document.getElementById("economics-summary");
  econBox.innerHTML = "";
  for (const source of ["live", "demo"]) {
    const block = economics.by_source[source];
    const card = document.createElement("div");
    card.className = "card";
    const est = document.createElement("span");
    est.className = "card-value";
    est.textContent = block.estimated_cost_minor ?? "—";
    const label = document.createElement("span");
    label.className = "card-label";
    label.textContent = `${t("label_estimated")} · ${sourceLabel(source)}` +
      ` · ${t("label_actual")}: ${block.actual_cost_minor ?? "—"}` +
      (block.unrated_records ? ` · ${t("label_rate_not_set")}: ${block.unrated_records}` : "");
    card.append(est, label);
    econBox.appendChild(card);
  }

  const priceList = document.getElementById("price-list");
  priceList.innerHTML = "";
  for (const price of prices) {
    const li = document.createElement("li");
    li.textContent = `${price.display_name} — ` +
      (price.rate_set
        ? `${price.input_per_1m_minor} / ${price.output_per_1m_minor} ${price.currency}`
        : t("label_rate_not_set"));
    priceList.appendChild(li);
  }

  const connList = document.getElementById("connection-list");
  connList.innerHTML = "";
  for (const conn of connections) {
    const li = document.createElement("li");
    if (conn.data_source === "demo") li.className = "is-demo";
    li.textContent = `${conn.display_name} · ${conn.kind} · ` +
      `${t(`status_${conn.health_status}`)}` +
      (conn.data_source === "demo" ? ` · ${t("label_source_demo")}` : "");
    connList.appendChild(li);
  }

  document.getElementById("service-status").textContent =
    reliability.service_status;
  document.getElementById("open-incidents").textContent =
    reliability.open_incidents;
  const incList = document.getElementById("incident-list");
  incList.innerHTML = "";
  for (const inc of reliability.incidents) {
    const li = document.createElement("li");
    if (inc.data_source === "demo") li.className = "is-demo";
    li.textContent = `#${inc.id} · ${inc.title} · ${inc.status} · ` +
      `${inc.severity}`;
    incList.appendChild(li);
  }

  await renderWorkflowPicker();
}

// ------------------------------------------------------- Workflow Visualizer

const SVGNS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

async function renderWorkflowPicker() {
  const select = document.getElementById("workflow-select");
  const workflows = await apiFetch("/api/workflows").then((r) => r.json());
  select.innerHTML = "";
  for (const wf of workflows) {
    const opt = document.createElement("option");
    opt.value = wf.id;
    opt.textContent = wf.display_name;
    select.appendChild(opt);
  }
  if (workflows.length) await renderWorkflowGraph(workflows[0].id);
}

document.getElementById("workflow-select")
  .addEventListener("change", (e) => renderWorkflowGraph(e.target.value));

async function renderWorkflowGraph(workflowId) {
  const box = document.getElementById("workflow-graph");
  box.innerHTML = "";
  // Overlay the most recent execution's step states when one exists.
  const executions = await apiFetch("/api/executions").then((r) => r.json());
  const latest = executions.find((e) => e.workflow_id === Number(workflowId));
  const url = `/api/workflows/${workflowId}/graph` +
    (latest ? `?execution_id=${latest.id}` : "");
  const graph = await apiFetch(url).then((r) => r.json());
  const svg = svgEl("svg", { width: graph.width, height: graph.height,
                             class: "wf-svg" });
  const defs = svgEl("defs", {});
  const marker = svgEl("marker", { id: "wf-arrow", markerWidth: 8,
    markerHeight: 8, refX: 7, refY: 3, orient: "auto" });
  marker.appendChild(svgEl("path", { d: "M0,0 L8,3 L0,6 Z",
                                     class: "wf-arrow" }));
  defs.appendChild(marker);
  svg.appendChild(defs);
  const byKey = Object.fromEntries(graph.nodes.map((n) => [n.key, n]));
  for (const edge of graph.edges) {
    const a = byKey[edge.from], b = byKey[edge.to];
    svg.appendChild(svgEl("line", {
      x1: a.x + 120, y1: a.y + 20, x2: b.x - 4, y2: b.y + 20,
      class: "wf-edge", "marker-end": "url(#wf-arrow)",
    }));
  }
  for (const node of graph.nodes) {
    const g = svgEl("g", { class: `wf-node ${node.status || ""}` });
    g.appendChild(svgEl("rect", { x: node.x, y: node.y, width: 120,
                                  height: 40, rx: 6 }));
    const text = svgEl("text", { x: node.x + 60, y: node.y + 25,
                                 "text-anchor": "middle" });
    text.textContent = node.label +
      (node.status ? ` · ${node.status}` : "");
    g.appendChild(text);
    svg.appendChild(g);
  }
  box.appendChild(svg);
}

// ------------------------------------------------ Intelligence Network (C)

async function renderNetwork() {
  const graph = await apiFetch("/api/network-graph").then((r) => r.json());
  const box = document.getElementById("network-graph");
  box.innerHTML = "";
  const teamPos = new Map();
  const agentPos = new Map();
  graph.teams.forEach((team, i) =>
    teamPos.set(team.id, { x: 30, y: 40 + i * 90 }));
  graph.agents.forEach((agent, i) =>
    agentPos.set(agent.id, { x: 340, y: 40 + i * 70 }));
  const height = Math.max(graph.teams.length * 90,
                          graph.agents.length * 70) + 60;
  const svg = svgEl("svg", { width: 640, height, class: "wf-svg" });
  for (const m of graph.memberships) {
    const a = teamPos.get(m.team_id), b = agentPos.get(m.agent_id);
    if (a && b) svg.appendChild(svgEl("line", {
      x1: a.x + 150, y1: a.y + 20, x2: b.x - 4, y2: b.y + 18,
      class: "wf-edge",
    }));
  }
  for (const edge of graph.communication_edges) {
    const a = agentPos.get(edge.from_agent_id);
    const b = agentPos.get(edge.to_agent_id);
    if (!a || !b) continue;
    const path = svgEl("path", {
      d: `M ${a.x + 150} ${a.y + 18} C ${a.x + 220} ${a.y - 20}, ` +
         `${b.x - 60} ${b.y + 50}, ${b.x} ${b.y + 18}`,
      class: "wf-edge comm",
    });
    svg.appendChild(path);
  }
  for (const team of graph.teams) {
    const p = teamPos.get(team.id);
    const g = svgEl("g", { class: "wf-node team" });
    g.appendChild(svgEl("rect", { x: p.x, y: p.y, width: 150,
                                  height: 40, rx: 6 }));
    const text = svgEl("text", { x: p.x + 75, y: p.y + 25,
                                 "text-anchor": "middle" });
    text.textContent = team.display_name;
    g.appendChild(text);
    svg.appendChild(g);
  }
  for (const agent of graph.agents) {
    const p = agentPos.get(agent.id);
    const g = svgEl("g", { class: "wf-node" +
      (agent.data_source === "demo" ? " demo" : "") });
    g.appendChild(svgEl("rect", { x: p.x, y: p.y, width: 150,
                                  height: 36, rx: 6 }));
    const text = svgEl("text", { x: p.x + 75, y: p.y + 22,
                                 "text-anchor": "middle" });
    text.textContent = agent.display_name;
    g.appendChild(text);
    svg.appendChild(g);
  }
  box.appendChild(svg);

  const list = document.getElementById("network-edges");
  list.innerHTML = "";
  const nameOf = (id) => (graph.agents.find((a) => a.id === id) || {})
    .display_name || `#${id}`;
  for (const edge of graph.communication_edges) {
    const li = document.createElement("li");
    li.textContent = `${nameOf(edge.from_agent_id)} → ` +
      `${nameOf(edge.to_agent_id)} · ${edge.count} ` +
      `${t("label_members") === "members" ? "messages" : "messages"}`;
    list.appendChild(li);
  }
}


// ------------------------------------------------------------- real login
// Cookie session (HttpOnly) set by POST /api/auth/login; the CSRF token
// comes back in the login body and from /api/auth/me, and rides on every
// state-changing request as X-CSRF-Token. Any 401 returns the user to
// the sign-in gate — the server refuses the call either way.

let csrfToken = null;
let pendingChallenge = null;
let useRecovery = false;

async function apiFetch(url, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = { ...(opts.headers || {}) };
  if (csrfToken && method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  const res = await fetch(url.startsWith("/") ? BASE + url : url,
                          { ...opts, headers });
  if (res.status === 401 && !url.startsWith("/api/auth/")) showAuth();
  return res;
}

function showAuth() {
  document.getElementById("auth-overlay").hidden = false;
  document.getElementById("user-chip").hidden = true;
  document.getElementById("login-form").hidden = false;
  document.getElementById("verify-form").hidden = true;
  document.getElementById("bootstrap-form").hidden = true;
  pendingChallenge = null;
  checkBootstrap();
}

// First-run bootstrap: on a fresh install no owner account exists, so
// signing in is impossible. Ask the server; if no owner exists, swap
// the sign-in form for the one-time owner-creation form. Once an owner
// exists the server refuses creation and this form is never shown.
async function checkBootstrap() {
  try {
    const res = await fetch(BASE + "/api/auth/bootstrap");
    if (!res.ok) return;
    const body = await res.json();
    if (body.owner_exists === false) {
      document.getElementById("login-form").hidden = true;
      document.getElementById("verify-form").hidden = true;
      document.getElementById("bootstrap-form").hidden = false;
    }
  } catch (e) { /* leave the sign-in form in place */ }
}

function hideAuth(username) {
  document.getElementById("auth-overlay").hidden = true;
  document.getElementById("user-chip").hidden = false;
  document.getElementById("user-name").textContent = username;
}

function authError(id) {
  const el = document.getElementById(id);
  el.textContent = t("auth_failed");
  el.hidden = false;
}

async function finishLogin(body) {
  if (body.csrf_token) csrfToken = body.csrf_token;
  hideAuth(body.user.username);
  await refresh();
}

async function checkSession() {
  const res = await fetch(BASE + "/api/auth/me");
  if (!res.ok) { showAuth(); return; }
  const body = await res.json();
  if (body.csrf_token) csrfToken = body.csrf_token;
  hideAuth(body.user.username);
  await refresh();
}

document.getElementById("login-form")
  .addEventListener("submit", async (e) => {
    e.preventDefault();
    document.getElementById("login-error").hidden = true;
    const res = await fetch(BASE + "/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("login-username").value,
        password: document.getElementById("login-password").value,
        mode: "cookie",
      }),
    });
    if (!res.ok) { authError("login-error"); return; }
    const body = await res.json();
    if (body.two_step_required) {
      pendingChallenge = body.challenge;
      document.getElementById("login-form").hidden = true;
      document.getElementById("verify-form").hidden = false;
      return;
    }
    await finishLogin(body);
  });

document.getElementById("bootstrap-form")
  .addEventListener("submit", async (e) => {
    e.preventDefault();
    const errEl = document.getElementById("bootstrap-error");
    errEl.hidden = true;
    const password = document.getElementById("bootstrap-password").value;
    if (password !== document.getElementById("bootstrap-confirm").value) {
      errEl.textContent = t("auth_password_mismatch");
      errEl.hidden = false;
      return;
    }
    const res = await fetch(BASE + "/api/auth/bootstrap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("bootstrap-username").value,
        password: password,
        mode: "cookie",
      }),
    });
    if (!res.ok) {
      // The server's detail names the exact rule that failed (password
      // policy, or "an owner already exists" if setup raced sign-in).
      let detail = null;
      try { detail = (await res.json()).detail; } catch (err) { detail = null; }
      errEl.textContent = detail || t("auth_failed");
      errEl.hidden = false;
      if (res.status === 403) checkBootstrap();
      return;
    }
    await finishLogin(await res.json());
  });

document.getElementById("verify-toggle")
  .addEventListener("click", () => {
    useRecovery = !useRecovery;
    document.getElementById("verify-code-label").hidden = useRecovery;
    document.getElementById("verify-recovery-label").hidden = !useRecovery;
    document.getElementById("verify-toggle").textContent =
      t(useRecovery ? "auth_use_authenticator" : "auth_use_recovery");
  });

document.getElementById("verify-back")
  .addEventListener("click", () => showAuth());

document.getElementById("verify-form")
  .addEventListener("submit", async (e) => {
    e.preventDefault();
    document.getElementById("verify-error").hidden = true;
    const payload = { challenge: pendingChallenge };
    if (useRecovery) {
      payload.recovery_code =
        document.getElementById("verify-recovery").value;
    } else {
      payload.code = document.getElementById("verify-code").value;
    }
    const res = await fetch(BASE + "/api/auth/login/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) { authError("verify-error"); return; }
    await finishLogin(await res.json());
  });

document.getElementById("sign-out")
  .addEventListener("click", async () => {
    await apiFetch("/api/auth/logout", { method: "POST" });
    csrfToken = null;
    showAuth();
  });

(async () => {
  await loadCatalog("en");
  await checkSession();
})();
