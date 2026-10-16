/* BlackBox dashboard - dependency-free vanilla JS.
 *
 * Talks to the /api endpoints, renders the learned behavioral graph as an
 * interactive SVG (BFS-layered layout, no libraries), and shows the live event
 * stream via EventSource.  All site-derived strings are escaped before they
 * reach the DOM.
 */
"use strict";

// ---------------------------------------------------------------- utilities
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(input) {
  const text = input === null || input === undefined ? "" : String(input);
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function truncate(input, limit) {
  const text = input === null || input === undefined ? "" : String(input);
  const max = Number(limit);
  if (!Number.isFinite(max) || max <= 0 || text.length <= max) return text;
  return max <= 1 ? "\u2026" : text.slice(0, max - 1) + "\u2026";
}

function baseName(ref) {
  if (!ref) return "";
  const parts = String(ref).split(/[\\/]/);
  return parts[parts.length - 1];
}

function fmtClock(epochSeconds) {
  if (!epochSeconds) return "--:--:--";
  const date = new Date(Number(epochSeconds) * 1000);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  return date.toTimeString().slice(0, 8);
}

function fmtIso(value) {
  if (!value) return "-";
  const text = String(value);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toISOString().replace("T", " ").slice(0, 19);
}

function pct(value) {
  const number = Number(value);
  if (Number.isNaN(number)) return "-";
  return (number * 100).toFixed(0) + "%";
}

function fixed(value, digits) {
  const number = Number(value);
  if (Number.isNaN(number)) return "-";
  return number.toFixed(digits === undefined ? 2 : digits);
}

function statusPill(status) {
  const text = String(status || "").toUpperCase();
  let kind = "";
  if (["VERIFIED", "CURRENT", "OK", "SUCCEEDED", "COMPLETED", "APPROVED"].includes(text)) kind = "ok";
  else if (["PROPOSED", "SUPPORTED", "STALE", "PAUSED", "QUEUED", "RUNNING", "WARN"].includes(text)) kind = "warn";
  else if (["REFUTED", "FAILED", "BLOCKED", "REJECTED", "DENIED", "CRITICAL", "ERROR"].includes(text)) kind = "bad";
  return `<span class="pill ${kind}">${esc(text || "?")}</span>`;
}

function riskPill(risk) {
  const text = String(risk || "").toUpperCase();
  const kind = text === "CRITICAL" ? "bad" : text === "HIGH" ? "bad" : text === "MEDIUM" ? "warn" : "ok";
  return `<span class="pill ${kind}">${esc(text || "-")}</span>`;
}

function bar(value, colour) {
  const width = Math.max(0, Math.min(100, Number(value) * 100 || 0));
  return `<span class="bar"><span style="width:${width}%;background:${colour || "var(--info)"}"></span></span>`;
}

function kv(pairs) {
  return (
    '<div class="kv">' +
    pairs
      .filter((pair) => pair && pair[1] !== undefined && pair[1] !== null && pair[1] !== "")
      .map((pair) => `<div class="k">${esc(pair[0])}</div><div class="v">${pair[1]}</div>`)
      .join("") +
    "</div>"
  );
}

function apiPath(path) {
  return "/api" + path;
}

async function api(path, options) {
  const opts = Object.assign({ headers: {} }, options || {});
  if (opts.body && !opts.headers["Content-Type"]) opts.headers["Content-Type"] = "application/json";
  const response = await fetch(apiPath(path), opts);
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (err) {
      payload = { raw: text };
    }
  }
  if (!response.ok) {
    const detail = payload && payload.detail ? (typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail)) : response.statusText;
    const error = new Error(`${response.status} ${detail}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

let noticeTimer = null;
function notice(message, kind) {
  const node = $("#notice");
  node.textContent = message || "";
  node.className = kind || "";
  if (noticeTimer) clearTimeout(noticeTimer);
  if (message) noticeTimer = setTimeout(() => { node.textContent = ""; node.className = ""; }, 9000);
}

// ---------------------------------------------------------------- app state
const S = {
  targets: [],
  target: null,
  view: "overview",
  graph: null,
  layout: null,
  viewBox: { x: 0, y: 0, w: 1000, h: 640 },
  events: [],
  stored: [],
  paused: false,
  byType: {},
  es: null,
  job: null,
  jobTimer: null,
  approvalsTimer: null,
  graphTimer: null,
  stateCache: new Map(),
  renderToken: 0,
  activeJobs: [],
};

const EVENT_BUFFER = 500;

// ---------------------------------------------------------------- targets
async function loadHealth() {
  try {
    const health = await api("/health");
    $("#health").textContent = `api: ${health.status} v${health.version}`;
    $("#health").className = "pill ok";
  } catch (err) {
    $("#health").textContent = "api: unreachable";
    $("#health").className = "pill bad";
  }
}

async function loadTargets(preferred) {
  const payload = await api("/targets");
  S.targets = payload.targets || [];
  renderTargetList();
  if (preferred) S.target = preferred;
  if (!S.target || !S.targets.some((item) => item.target_id === S.target)) {
    S.target = S.targets.length ? S.targets[0].target_id : null;
  }
  renderTargetList();
  if (S.target) {
    selectTarget(S.target);
  } else {
    $("#target-list").innerHTML = '<li class="faint">no targets registered</li>';
  }
}

function renderTargetList() {
  const list = $("#target-list");
  if (!S.targets.length) {
    list.innerHTML = '<li class="faint">no targets registered</li>';
    return;
  }
  list.innerHTML = S.targets
    .map((target) => {
      const stats = target.stats || {};
      const active = target.target_id === S.target ? " active" : "";
      const flags = [
        target.live ? "live" : "idle",
        `${stats.states || 0} states`,
        `${stats.transitions || 0} trans`,
        `${stats.workflows || 0} flows`,
        target.pending_approvals ? `${target.pending_approvals} appr` : null,
      ].filter(Boolean);
      return `<li class="target-item${active}" data-target="${esc(target.target_id)}">
        <div class="tid">${esc(target.target_id)}</div>
        <div class="tmeta">${esc(target.mode)} v${esc(target.model_version === null ? "?" : target.model_version)}</div>
        <div class="tmeta">${esc(flags.join(" \u00b7 "))}</div>
      </li>`;
    })
    .join("");
  $$("#target-list .target-item").forEach((node) => {
    node.addEventListener("click", () => selectTarget(node.dataset.target));
  });
}

function selectTarget(targetId) {
  if (S.target === targetId && S.graph) return;
  S.target = targetId;
  S.graph = null;
  S.layout = null;
  S.events = [];
  S.byType = {};
  S.job = null;
  S.stateCache.clear();
  S.renderToken += 1;
  if (S.jobTimer) { clearInterval(S.jobTimer); S.jobTimer = null; }
  renderTargetList();
  startStream();
  renderSelection();
  setView(S.view, true);
  if (S.approvalsTimer) loadApprovals();
}

// ---------------------------------------------------------------- stream
function setStreamPill(text, kind) {
  $("#stream-pill").textContent = text;
  $("#stream-pill").className = "pill " + (kind || "");
}

function startStream() {
  if (S.es) { S.es.close(); S.es = null; }
  if (!S.target) { setStreamPill("stream: off"); return; }
  const source = new EventSource(apiPath(`/targets/${encodeURIComponent(S.target)}/stream?replay=25`));
  S.es = source;
  source.onopen = () => setStreamPill("stream: live", "ok live");
  source.onerror = () => setStreamPill("stream: reconnecting", "warn");
  source.onmessage = (message) => {
    let event = null;
    try {
      event = JSON.parse(message.data);
    } catch (err) {
      return;
    }
    onEvent(event);
  };
}

function onEvent(event) {
  const type = event.type || "event";
  S.events.push(event);
  if (S.events.length > EVENT_BUFFER) S.events.shift();
  S.byType[type] = (S.byType[type] || 0) + 1;

  if (S.view === "logs" && !S.paused) appendLiveEvent(event);
  if (S.view === "metrics") renderEventCounters();
  if (S.view === "current") renderCurrent();
  if (type === "approval_requested") { loadApprovals(); refreshTargetsSoon(); }
  if (type === "new_state" || type === "action_result" || type === "exploration_finished") {
    if (S.view === "graph" && type !== "action_result") scheduleGraphReload();
    refreshTargetsSoon();
  }
  if (type === "job_finished" || type === "job_started") loadJobs();
}

let targetsRefreshTimer = null;
function refreshTargetsSoon() {
  if (targetsRefreshTimer) return;
  targetsRefreshTimer = setTimeout(async () => {
    targetsRefreshTimer = null;
    try {
      const payload = await api("/targets");
      S.targets = payload.targets || [];
      renderTargetList();
    } catch (err) { /* transient */ }
  }, 2500);
}

function scheduleGraphReload() {
  if (S.graphTimer) return;
  S.graphTimer = setTimeout(async () => {
    S.graphTimer = null;
    try { await renderGraph(); } catch (err) { notice("graph reload failed: " + err.message, "err"); }
  }, 4000);
}

// ---------------------------------------------------------------- views
function setView(name, force) {
  if (S.view === name && !force) return;
  S.view = name;
  $$("#tabs button").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === "view-" + name));
  S.renderToken += 1;
  const token = S.renderToken;
  const renderers = {
    overview: renderOverview,
    graph: renderGraph,
    current: renderCurrent,
    workflows: renderWorkflows,
    hypotheses: renderHypotheses,
    experiments: renderExperiments,
    tasks: renderTasks,
    logs: renderLogs,
    metrics: renderMetrics,
    approvals: renderApprovals,
  };
  const renderer = renderers[name];
  if (!renderer) return;
  if (!S.target) {
    $("#view-" + name).innerHTML = '<div class="muted-box">No target selected. Register one in the left rail.</div>';
    return;
  }
  Promise.resolve(renderer(token)).catch((err) => {
    if (token !== S.renderToken) return;
    $("#view-" + name).innerHTML = `<div class="muted-box">Could not load this view: ${esc(err.message)}</div>`;
  });
  if (name === "approvals") {
    if (S.approvalsTimer) clearInterval(S.approvalsTimer);
    S.approvalsTimer = setInterval(() => { if (S.view === "approvals") loadApprovals(); }, 5000);
  } else if (S.approvalsTimer) {
    clearInterval(S.approvalsTimer);
    S.approvalsTimer = null;
  }
}

function current(token) {
  return token === undefined || token === S.renderToken;
}

// -- overview --------------------------------------------------------------
async function renderOverview(token) {
  const host = $("#view-overview");
  host.innerHTML = '<div class="dim">loading model summary&hellip;</div>';
  const [model, jobs] = await Promise.all([
    api(`/targets/${encodeURIComponent(S.target)}/model`),
    api(`/targets/${encodeURIComponent(S.target)}/jobs?limit=8`),
  ]);
  if (!current(token)) return;
  S.model = model;
  S.activeJobs = jobs.jobs || [];
  const summary = model.summary || {};
  const stats = model.stats || {};
  const fingerprints = model.fingerprints || {};
  const versions = model.versions || [];
  host.innerHTML = `
    <h3 class="section">Target</h3>
    ${kv([
      ["target_id", `<span class="id">${esc(S.target)}</span>`],
      ["base_url", `<span class="id">${esc(model.base_url)}</span>`],
      ["live agent", model.live ? '<span class="pill ok">yes</span>' : '<span class="pill">no (stored model)</span>'],
      ["model version", `v${esc(summary.model_version)}`],
      ["stored versions", versions.length ? versions.map((item) => "v" + esc(item.version)).join(", ") : "-"],
    ])}
    <h3 class="section">Counts</h3>
    <div class="counters">
      ${counter("states", summary.states)}
      ${counter("transitions", summary.transitions)}
      ${counter("verified", summary.verified_transitions)}
      ${counter("workflows", summary.workflows)}
      ${counter("constraints", summary.constraints)}
      ${counter("hypotheses", summary.hypotheses)}
      ${counter("evidence", summary.evidence)}
      ${counter("experiments", summary.experiments)}
      ${counter("pages", summary.pages)}
    </div>
    <h3 class="section">Fingerprints</h3>
    ${kv([
      ["states current / stale", `${esc(fingerprints.states_current)} / ${esc(fingerprints.states_stale)}`],
      ["mean confidence", pct(fingerprints.mean_confidence)],
      ["mean visits", fixed(fingerprints.mean_visit_count, 1)],
      ["elements observed", esc(fingerprints.total_elements)],
      ["distinct url keys", esc(fingerprints.distinct_url_keys)],
      ["fingerprint signals", esc(fingerprints.fingerprint_signals)],
    ])}
    <h3 class="section">Control</h3>
    <div class="graph-toolbar">
      <label class="dim">max_actions <input id="explore-actions" type="text" value="40" style="width:52px"></label>
      <label class="dim">max_seconds <input id="explore-seconds" type="text" value="180" style="width:58px"></label>
      <button class="btn primary" id="btn-explore">start exploration</button>
      <button class="btn danger" id="btn-stop">stop</button>
      <button class="btn" id="btn-status">agent status</button>
      <button class="btn" id="btn-export">export model</button>
      <button class="btn" id="btn-mine">re-mine workflows</button>
    </div>
    <div id="status-pane"></div>
    <h3 class="section">Recent jobs</h3>
    <div id="jobs-pane">${renderJobsTable(S.activeJobs)}</div>
  `;
  $("#btn-explore").addEventListener("click", startExploration);
  $("#btn-stop").addEventListener("click", stopTarget);
  $("#btn-status").addEventListener("click", showAgentStatus);
  $("#btn-export").addEventListener("click", exportModel);
  $("#btn-mine").addEventListener("click", mineWorkflows);
}

function counter(label, value) {
  return `<span class="counter">${esc(label)} <b>${esc(value === undefined ? 0 : value)}</b></span>`;
}

function renderJobsTable(jobs) {
  if (!jobs || !jobs.length) return '<div class="muted-box">no jobs recorded for this target in this process</div>';
  return `<table class="tbl"><thead><tr>
      <th>job</th><th>kind</th><th>status</th><th>started</th><th class="num">seconds</th><th>detail</th>
    </tr></thead><tbody>
    ${jobs
      .map(
        (job) => `<tr>
        <td class="id">${esc(job.job_id)}</td>
        <td>${esc(job.kind)}</td>
        <td>${statusPill(job.status)}</td>
        <td class="mono">${esc(fmtClock(job.started_at))}</td>
        <td class="num">${esc(job.duration_seconds === null || job.duration_seconds === undefined ? "-" : job.duration_seconds)}</td>
        <td class="dim">${esc(truncate(job.error || jobSummary(job), 110))}</td>
      </tr>`
      )
      .join("")}
  </tbody></table>`;
}

function jobSummary(job) {
  const result = job.result;
  if (!result) return "";
  if (result.stop_reason) return `stop: ${result.stop_reason} (${result.actions_executed || 0} actions)`;
  if (typeof result.success === "boolean") return `success=${result.success} source=${result.plan_source || "?"}`;
  return "";
}

async function startExploration() {
  const body = {
    max_actions: Number($("#explore-actions").value) || 40,
    max_seconds: Number($("#explore-seconds").value) || 180,
    configuration_label: "dashboard",
  };
  try {
    const response = await api(`/targets/${encodeURIComponent(S.target)}/explore`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    notice(`exploration job ${response.job_id} started`, "ok");
    pollJob(response.job_id);
  } catch (err) {
    notice("explore failed: " + err.message, "err");
  }
}

async function stopTarget() {
  try {
    const response = await api(`/targets/${encodeURIComponent(S.target)}/stop`, { method: "POST" });
    notice(`stop requested (explorer present: ${response.explorer_present})`, "ok");
  } catch (err) {
    notice("stop failed: " + err.message, "err");
  }
}

async function showAgentStatus() {
  const pane = $("#status-pane");
  pane.innerHTML = '<div class="dim">asking the agent (this may start a browser)&hellip;</div>';
  try {
    const status = await api(`/targets/${encodeURIComponent(S.target)}/status`);
    const browser = status.browser || {};
    pane.innerHTML = `
      ${status.degraded ? `<div class="muted-box">degraded: ${esc(status.start_error || "unknown")}</div>` : ""}
      ${kv([
        ["mode", esc(status.mode)],
        ["browser alive", browser.alive ? '<span class="pill ok">yes</span>' : '<span class="pill bad">no</span>'],
        ["browser version", esc(browser.browser_version || "-")],
        ["pid", esc(browser.pid || "-")],
        ["restarts / crashes", `${esc(browser.restarts || 0)} / ${esc(browser.crashes || 0)}`],
        ["last error", esc(truncate(browser.last_error || "-", 120))],
        ["model version", esc((status.model || {}).model_version)],
        ["sandbox blocked", esc((status.sandbox || {}).blocked_count || 0)],
      ])}
      <pre class="code">${esc(JSON.stringify(status.policy || {}, null, 1))}</pre>`;
  } catch (err) {
    pane.innerHTML = `<div class="muted-box">status unavailable: ${esc(err.message)}</div>`;
  }
}

async function exportModel() {
  try {
    const response = await api(`/targets/${encodeURIComponent(S.target)}/model/export`, { method: "POST" });
    notice(`exported v${response.model_version} to ${response.path} (${response.bytes} bytes)`, "ok");
  } catch (err) {
    notice("export failed: " + err.message, "err");
  }
}

async function mineWorkflows() {
  try {
    const response = await api(`/targets/${encodeURIComponent(S.target)}/workflows/mine`, { method: "POST" });
    notice(`re-mined ${response.count} workflow(s)`, "ok");
    if (S.view === "workflows") renderWorkflows();
  } catch (err) {
    notice("mine failed: " + err.message, "err");
  }
}

async function loadJobs() {
  if (!S.target) return;
  try {
    const payload = await api(`/targets/${encodeURIComponent(S.target)}/jobs?limit=8`);
    S.activeJobs = payload.jobs || [];
    const pane = $("#jobs-pane");
    if (pane) pane.innerHTML = renderJobsTable(S.activeJobs);
  } catch (err) { /* transient */ }
}

// -- state graph -----------------------------------------------------------
function nodeWidth(node) {
  const label = String(node.label || node.url_key || node.id);
  return Math.max(88, Math.min(210, label.length * 6.1 + 30));
}

function computeLayout(graph) {
  const nodes = (graph.nodes || []).map((node) => Object.assign({}, node, { w: nodeWidth(node), h: 30 }));
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const adjacency = new Map(nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges || []) {
    if (byId.has(edge.source) && byId.has(edge.target)) adjacency.get(edge.source).push(edge.target);
  }
  const depths = new Map();
  const root = graph.root_state_id && byId.has(graph.root_state_id) ? graph.root_state_id : nodes.length ? nodes[0].id : null;
  if (root) {
    depths.set(root, 0);
    const queue = [root];
    let guard = 0;
    while (queue.length && guard++ < 20000) {
      const id = queue.shift();
      const depth = depths.get(id);
      for (const next of adjacency.get(id) || []) {
        if (!depths.has(next)) {
          depths.set(next, depth + 1);
          queue.push(next);
        }
      }
    }
  }
  const maxDepth = depths.size ? Math.max.apply(null, Array.from(depths.values())) : 0;
  for (const node of nodes) if (!depths.has(node.id)) depths.set(node.id, maxDepth + 1);

  const layers = new Map();
  for (const node of nodes) {
    const depth = depths.get(node.id);
    if (!layers.has(depth)) layers.set(depth, []);
    layers.get(depth).push(node.id);
  }
  // Deterministic initial order: most-visited first, then id.
  for (const ids of layers.values()) {
    ids.sort((a, b) => {
      const visitDiff = (byId.get(b).visit_count || 0) - (byId.get(a).visit_count || 0);
      return visitDiff !== 0 ? visitDiff : String(a).localeCompare(String(b));
    });
  }
  const layerDepths = Array.from(layers.keys()).sort((a, b) => a - b);
  const indexOf = (id) => {
    const depth = depths.get(id);
    return layers.has(depth) ? layers.get(depth).indexOf(id) : 0;
  };
  // Barycentre passes: pull each node towards the mean position of its neighbours.
  for (let pass = 0; pass < 4; pass += 1) {
    const downward = pass % 2 === 0;
    const order = downward ? layerDepths : layerDepths.slice().reverse();
    for (const depth of order) {
      const ids = layers.get(depth);
      const keys = new Map();
      ids.forEach((id, index) => {
        const neighbours = (graph.edges || [])
          .filter((edge) => (edge.source === id || edge.target === id))
          .map((edge) => (edge.source === id ? edge.target : edge.source))
          .filter((other) => other !== id && depths.get(other) !== depth);
        const positions = neighbours.map(indexOf);
        keys.set(id, positions.length ? positions.reduce((a, b) => a + b, 0) / positions.length : index);
      });
      ids.sort((a, b) => (keys.get(a) - keys.get(b)) || String(a).localeCompare(String(b)));
    }
  }
  const colGap = 320;
  const rowGap = 62;
  const usable = layerDepths.map((depth) => layers.get(depth).length);
  const maxRows = usable.length ? Math.max.apply(null, usable) : 1;
  layerDepths.forEach((depth, column) => {
    const ids = layers.get(depth);
    const offset = ((maxRows - ids.length) * rowGap) / 2;
    ids.forEach((id, row) => {
      const node = byId.get(id);
      node.x = 60 + column * colGap;
      node.y = 60 + offset + row * rowGap;
    });
  });

  // Edge geometry, fanning parallel edges so labels stay readable.
  const pairCount = new Map();
  const edges = (graph.edges || []).map((edge, position) => {
    const key = `${edge.source}->${edge.target}`;
    const seen = pairCount.get(key) || 0;
    pairCount.set(key, seen + 1);
    return Object.assign({}, edge, { position, fan: seen });
  });
  return { nodes, byId, edges, depths, layers, stats: graph.stats || {}, root, pairCount };
}

async function renderGraph(token) {
  const info = $("#graph-info");
  const graph = await api(`/targets/${encodeURIComponent(S.target)}/graph`);
  if (!current(token)) return;
  S.graph = graph;
  S.layout = computeLayout(graph);
  const stats = graph.stats || {};
  info.textContent = `${stats.states || 0} states, ${stats.transitions || 0} transitions, ${stats.verified || 0} verified, ${stats.workflows || 0} workflows`;
  drawGraph(true);
}

function markerId(colour) {
  return `arrow-${colour}`;
}

function edgeColour(edge) {
  const status = String(edge.status || "").toUpperCase();
  if (status === "VERIFIED") return "ok";
  if (status === "REFUTED") return "bad";
  if (status === "STALE") return "dim";
  return "warn";
}

function edgeStroke(colour) {
  return { ok: "#3fb950", warn: "#d29922", bad: "#f85149", dim: "#6e7681", info: "#58a6ff" }[colour] || "#6e7681";
}

function nodeStroke(node) {
  const status = String(node.status || "").toUpperCase();
  if (status === "CURRENT") return "#3fb950";
  if (status === "STALE") return "#d29922";
  return "#6e7681";
}

function edgePath(edge, layout) {
  const source = layout.byId.get(edge.source);
  const target = layout.byId.get(edge.target);
  if (!source || !target) return { d: "", labelX: 0, labelY: 0 };
  const sx = source.x + source.w;
  const sy = source.y + source.h / 2;
  const tx = target.x;
  const ty = target.y + target.h / 2;
  const fan = (edge.fan - ((layout.pairCount.get(`${edge.source}->${edge.target}`) || 1) - 1) / 2) * 20;
  if (source.id === target.id) {
    const loop = 30 + Math.abs(fan);
    const d = `M ${source.x + source.w * 0.65} ${source.y} C ${source.x + source.w * 0.65 + loop} ${source.y - loop - 20}, ${source.x + source.w * 0.35 - loop} ${source.y - loop - 20}, ${source.x + source.w * 0.35} ${source.y}`;
    return { d, labelX: source.x + source.w / 2, labelY: source.y - loop - 14 };
  }
  const midX = (sx + tx) / 2;
  const midY = (sy + ty) / 2 + fan;
  const d = `M ${sx} ${sy} Q ${midX} ${midY}, ${tx} ${ty}`;
  // Quadratic bezier midpoint (t = 0.5).
  const labelX = 0.25 * sx + 0.5 * midX + 0.25 * tx;
  const labelY = 0.25 * sy + 0.5 * midY + 0.25 * ty;
  return { d, labelX, labelY };
}

function drawGraph(fit) {
  const svg = $("#graph");
  if (!S.layout) return;
  const layout = S.layout;
  const colours = ["ok", "warn", "bad", "dim", "info"];
  const defs = `<defs>${colours
    .map(
      (colour) => `<marker id="${markerId(colour)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="${edgeStroke(colour)}"></path>
      </marker>`
    )
    .join("")}</defs>`;

  const edgeMarkup = layout.edges
    .map((edge) => {
      const colour = edgeColour(edge);
      const geometry = edgePath(edge, layout);
      if (!geometry.d) return "";
      const width = (1 + Math.max(0, Math.min(1, Number(edge.confidence) || 0)) * 2.6).toFixed(2);
      const selected = S.selected && S.selected.kind === "edge" && S.selected.id === edge.id ? " sel" : "";
      return `<g class="edge${selected}" data-edge-id="${esc(edge.id)}">
        <path d="${geometry.d}" stroke="${edgeStroke(colour)}" stroke-width="${width}" marker-end="url(#${markerId(colour)})"></path>
        <text x="${geometry.labelX}" y="${geometry.labelY - 3}" text-anchor="middle">${esc(truncate(edge.action, 26))}</text>
      </g>`;
    })
    .join("");

  const nodeMarkup = layout.nodes
    .map((node) => {
      const radius = Math.max(0, Math.min(1, (Number(node.visit_count) || 0) / 60));
      const height = 30 + radius * 8;
      const selected = S.selected && S.selected.kind === "node" && S.selected.id === node.id ? " sel" : "";
      const colour = nodeStroke(node);
      const label = truncate(node.label || node.url_key || node.id, 24);
      return `<g class="node${selected}" data-node-id="${esc(node.id)}">
        <rect x="${node.x}" y="${node.y}" width="${node.w}" height="${height}" rx="4" fill="#131920" stroke="${colour}"></rect>
        <text x="${node.x + node.w / 2}" y="${node.y + 13}" text-anchor="middle">${esc(label)}</text>
        <text x="${node.x + node.w / 2}" y="${node.y + 25}" text-anchor="middle" fill="#5d6873">${esc(truncate(node.url_key || node.id, 26))} \u00b7 ${esc(node.visit_count || 0)}v</text>
      </g>`;
    })
    .join("");

  svg.innerHTML = defs + edgeMarkup + nodeMarkup;
  if (fit) fitGraph();
  else applyViewBox();
}

function graphBounds() {
  const layout = S.layout;
  if (!layout || !layout.nodes.length) return { minX: 0, minY: 0, maxX: 600, maxY: 400 };
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const node of layout.nodes) {
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y - 30);
    maxX = Math.max(maxX, node.x + node.w);
    maxY = Math.max(maxY, node.y + 50);
  }
  return { minX, minY, maxX, maxY };
}

function aspect() {
  const rect = $("#graph").getBoundingClientRect();
  if (!rect.width || !rect.height) return 1.5;
  return rect.height / rect.width;
}

function applyViewBox() {
  const vb = S.viewBox;
  $("#graph").setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
}

function fitGraph() {
  const bounds = graphBounds();
  const pad = 40;
  const width = Math.max(240, bounds.maxX - bounds.minX + pad * 2);
  const height = Math.max(160, bounds.maxY - bounds.minY + pad * 2);
  const ratio = aspect();
  let w = width;
  if (w * ratio < height) w = height / ratio;
  S.viewBox = {
    w,
    h: w * ratio,
    x: (bounds.minX + bounds.maxX) / 2 - w / 2,
    y: (bounds.minY + bounds.maxY) / 2 - (w * ratio) / 2,
  };
  applyViewBox();
}

function zoomAt(clientX, clientY, factor) {
  const svg = $("#graph");
  const rect = svg.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const vb = S.viewBox;
  const px = vb.x + ((clientX - rect.left) / rect.width) * vb.w;
  const py = vb.y + ((clientY - rect.top) / rect.height) * vb.h;
  const nextW = Math.max(60, Math.min(16000, vb.w * factor));
  const scale = nextW / vb.w;
  S.viewBox = {
    w: nextW,
    h: vb.h * scale,
    x: px - (px - vb.x) * scale,
    y: py - (py - vb.y) * scale,
  };
  applyViewBox();
}

function initGraphInteractions() {
  const svg = $("#graph");
  let dragging = false;
  let lastX = 0;
  let lastY = 0;

  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomAt(event.clientX, event.clientY, Math.exp(event.deltaY * 0.0013));
  }, { passive: false });

  svg.addEventListener("mousedown", (event) => {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    svg.classList.add("dragging");
  });
  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const dx = ((event.clientX - lastX) / rect.width) * S.viewBox.w;
    const dy = ((event.clientY - lastY) / rect.height) * S.viewBox.h;
    lastX = event.clientX;
    lastY = event.clientY;
    S.viewBox.x -= dx;
    S.viewBox.y -= dy;
    applyViewBox();
  });
  window.addEventListener("mouseup", () => {
    dragging = false;
    svg.classList.remove("dragging");
  });

  svg.addEventListener("click", (event) => {
    const nodeEl = event.target.closest("[data-node-id]");
    if (nodeEl) {
      S.selected = { kind: "node", id: nodeEl.dataset.nodeId };
      drawGraph(false);
      openState(nodeEl.dataset.nodeId);
      return;
    }
    const edgeEl = event.target.closest("[data-edge-id]");
    if (edgeEl) {
      S.selected = { kind: "edge", id: edgeEl.dataset.edgeId };
      drawGraph(false);
      openTransition(edgeEl.dataset.edgeId);
    }
  });

  window.addEventListener("resize", () => {
    if (S.view !== "graph" || !S.layout) return;
    const ratio = aspect();
    const centreY = S.viewBox.y + S.viewBox.h / 2;
    S.viewBox.h = S.viewBox.w * ratio;
    S.viewBox.y = centreY - S.viewBox.h / 2;
    applyViewBox();
  });

  $("#graph-fit").addEventListener("click", fitGraph);
  $("#graph-in").addEventListener("click", () => {
    const rect = svg.getBoundingClientRect();
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, 0.8);
  });
  $("#graph-out").addEventListener("click", () => {
    const rect = svg.getBoundingClientRect();
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, 1.25);
  });
  $("#graph-reset").addEventListener("click", fitGraph);
}

// -- side panel ------------------------------------------------------------
function renderSelection() {
  $("#selection").innerHTML = S.target
    ? `target <span class="id">${esc(S.target)}</span>`
    : "nothing selected";
}

function renderElementList(elements) {
  if (!elements || !elements.length) return '<div class="faint" style="padding:4px">no elements recorded</div>';
  return elements
    .map(
      (element) => `<div>
        <span class="role">${esc(element.semantic_role || "UNKNOWN")}</span>
        <span class="lbl">${esc(element.accessible_name || element.visible_text || element.tag || "")}</span>
        ${element.value_state && element.value_state.value ? ` <span class="faint">= ${esc(truncate(element.value_state.value, 24))}</span>` : ""}
        ${element.enabled === false ? ' <span class="pill warn">disabled</span>' : ""}
      </div>`
    )
    .join("");
}

function transitionRow(transition, direction) {
  return `<div class="step">
      <div class="idx">${esc(direction)}</div>
      <div>
        <button class="btn tiny" data-transition="${esc(transition.transition_id)}">${esc(truncate(transition.action_description, 46))}</button>
        ${statusPill(transition.status)}
        <span class="faint mono">${esc(direction === "in" ? transition.source_state : transition.target_state)}</span>
      </div>
    </div>`;
}

async function openState(stateId) {
  const panel = $("#sidepanel");
  panel.innerHTML = `<h3 class="section">State</h3><div class="dim">loading <span class="id">${esc(stateId)}</span>&hellip;</div>`;
  try {
    const state = await api(`/targets/${encodeURIComponent(S.target)}/states/${encodeURIComponent(stateId)}`);
    S.stateCache.set(stateId, state);
    const shot = state.screenshot_filename
      ? `<img class="shot" src="${apiPath(`/targets/${encodeURIComponent(S.target)}/screenshots/${encodeURIComponent(state.screenshot_filename)}`)}" alt="state screenshot">`
      : '<div class="muted-box">no screenshot for this state</div>';
    panel.innerHTML = `
      <h3 class="section">State <span class="id">${esc(state.state_id)}</span></h3>
      ${kv([
        ["title", esc(state.title)],
        ["url", `<span class="id">${esc(state.url)}</span>`],
        ["url key", esc(state.fingerprint && state.fingerprint.url_key)],
        ["page", esc(state.page_id)],
        ["status", statusPill(state.status)],
        ["confidence", `${pct(state.confidence)} ${bar(state.confidence)}`],
        ["visits", esc(state.visit_count)],
        ["elements", esc((state.visible_elements || []).length)],
        ["dialogs", esc((state.dialogs || []).length)],
        ["forms", esc((state.forms || []).length)],
        ["first / last seen", `${esc(fmtIso(state.first_seen))}<br>${esc(fmtIso(state.last_seen))}`],
      ])}
      ${shot}
      <h3 class="section">Semantic summary</h3>
      <div class="mono">${esc(state.semantic_summary || "-")}</div>
      <h3 class="section">Visible elements (${esc((state.visible_elements || []).length)})</h3>
      <div class="elems">${renderElementList(state.visible_elements)}</div>
      <h3 class="section">Outgoing transitions (${esc((state.outgoing_transitions || []).length)})</h3>
      ${(state.outgoing_transitions || []).map((item) => transitionRow(item, "out")).join("") || '<div class="faint">none</div>'}
      <h3 class="section">Incoming transitions (${esc((state.incoming_transitions || []).length)})</h3>
      ${(state.incoming_transitions || []).map((item) => transitionRow(item, "in")).join("") || '<div class="faint">none</div>'}
    `;
    $$("#sidepanel [data-transition]").forEach((button) => {
      button.addEventListener("click", () => {
        S.selected = { kind: "edge", id: button.dataset.transition };
        if (S.view === "graph") drawGraph(false);
        openTransition(button.dataset.transition);
      });
    });
  } catch (err) {
    panel.innerHTML = `<h3 class="section">State</h3><div class="muted-box">${esc(err.message)}</div>`;
  }
}

async function openTransition(transitionId) {
  const panel = $("#sidepanel");
  panel.innerHTML = `<h3 class="section">Transition</h3><div class="dim">loading <span class="id">${esc(transitionId)}</span>&hellip;</div>`;
  try {
    const transition = await api(`/targets/${encodeURIComponent(S.target)}/transitions/${encodeURIComponent(transitionId)}`);
    const expected = transition.action_expected_effect || [];
    const observed = (transition.observed_effects || []).map((effect) => effect.kind);
    panel.innerHTML = `
      <h3 class="section">Transition <span class="id">${esc(transition.transition_id)}</span></h3>
      ${kv([
        ["action", `<span class="mono">${esc(transition.action_description)}</span>`],
        ["type / risk", `${esc(transition.action_type)} ${riskPill(transition.risk)}`],
        ["status", statusPill(transition.status)],
        ["confidence", `${pct(transition.confidence)} ${bar(transition.confidence, edgeStroke(edgeColour(transition)))}`],
        ["source", `<button class="btn tiny" data-state="${esc(transition.source_state)}">${esc(transition.source_state_label || transition.source_state)}</button>`],
        ["target", `<button class="btn tiny" data-state="${esc(transition.target_state)}">${esc(transition.target_state_label || transition.target_state)}</button>`],
        ["executions", `${esc(transition.execution_count)} (ok ${esc(transition.success_count)} / fail ${esc(transition.failure_count)}, rate ${pct(transition.success_rate)})`],
        ["updated", esc(fmtIso(transition.updated_at))],
      ])}
      <h3 class="section">Expected vs observed effects</h3>
      ${kv([
        ["expected", expected.length ? expected.map((item) => `<span class="pill">${esc(item)}</span>`).join(" ") : '<span class="faint">none declared</span>'],
        ["observed", observed.length ? observed.map((item) => `<span class="pill ok">${esc(item)}</span>`).join(" ") : '<span class="faint">none recorded</span>'],
      ])}
      <div class="elems">${(transition.observed_effects || [])
        .map((effect) => `<div><span class="role">${esc(effect.kind)}</span> <span class="lbl">${esc(truncate(effect.detail || effect.message || "", 90))}</span></div>`)
        .join("") || '<div class="faint" style="padding:4px">no effect detail</div>'}</div>
      <h3 class="section">Preconditions</h3>
      <div class="mono dim">${esc((transition.preconditions || []).join("; ") || "none learned")}</div>
      <h3 class="section">Postconditions</h3>
      <div class="mono dim">${esc((transition.postconditions || []).join("; ") || "none learned")}</div>
      <h3 class="section">Evidence (${esc((transition.evidence || []).length)})</h3>
      <div id="evidence-pane">
        ${(transition.evidence || [])
          .map(
            (item) => `<div class="card">
              <div class="card-head">
                <button class="btn tiny" data-evidence="${esc(item.evidence_id)}">${esc(item.evidence_id)}</button>
                <span class="pill">${esc(item.kind)}</span>
                <span class="faint mono">${esc(fmtIso(item.created_at))}</span>
              </div>
              <div class="mono dim">${esc(truncate(item.message, 160))}</div>
            </div>`
          )
          .join("") || '<div class="faint">no evidence attached to this transition</div>'}
      </div>
      <div id="evidence-detail"></div>
    `;
    $$("#sidepanel [data-state]").forEach((button) => {
      button.addEventListener("click", () => {
        S.selected = { kind: "node", id: button.dataset.state };
        if (S.view === "graph") drawGraph(false);
        openState(button.dataset.state);
      });
    });
    $$("#sidepanel [data-evidence]").forEach((button) => {
      button.addEventListener("click", () => showEvidence(button.dataset.evidence));
    });
  } catch (err) {
    panel.innerHTML = `<h3 class="section">Transition</h3><div class="muted-box">${esc(err.message)}</div>`;
  }
}

async function showEvidence(evidenceId) {
  const target = $("#evidence-detail") || $("#sidepanel");
  try {
    const evidence = await api(`/targets/${encodeURIComponent(S.target)}/evidence/${encodeURIComponent(evidenceId)}`);
    target.innerHTML = `
      <h3 class="section">Evidence <span class="id">${esc(evidence.evidence_id)}</span></h3>
      ${kv([
        ["kind", esc(evidence.kind)],
        ["message", esc(evidence.message)],
        ["transition", esc(evidence.transition_id || "-")],
        ["created", esc(fmtIso(evidence.created_at))],
        ["observations", esc((evidence.observations || []).join(", ") || "-")],
        ["notes", esc((evidence.notes || []).join(", ") || "-")],
      ])}
      <div class="elems">${(evidence.effects || [])
        .map((effect) => `<div><span class="role">${esc(effect.kind)}</span> <span class="lbl">${esc(truncate(effect.detail || effect.message || "", 100))}</span></div>`)
        .join("") || '<div class="faint" style="padding:4px">no effects</div>'}</div>`;
  } catch (err) {
    target.innerHTML = `<div class="muted-box">${esc(err.message)}</div>`;
  }
}

// -- current state ---------------------------------------------------------
async function renderCurrent(token) {
  const host = $("#view-current");
  const lastState = [...S.events].reverse().find((event) => event.type === "new_state");
  const lastResult = [...S.events].reverse().find((event) => event.type === "action_result");
  const lastProposal = [...S.events].reverse().find((event) => event.type === "action_proposed");
  const recent = S.events.slice(-14).reverse();

  let screenshot = "";
  if (lastState && lastState.payload && lastState.payload.state) {
    const stateId = lastState.payload.state.state_id;
    let state = S.stateCache.get(stateId);
    if (!state) {
      try {
        state = await api(`/targets/${encodeURIComponent(S.target)}/states/${encodeURIComponent(stateId)}`);
        S.stateCache.set(stateId, state);
      } catch (err) {
        state = null;
      }
    }
    if (!current(token)) return;
    if (state && state.screenshot_filename) {
      screenshot = `<img class="shot" src="${apiPath(`/targets/${encodeURIComponent(S.target)}/screenshots/${encodeURIComponent(state.screenshot_filename)}`)}" alt="latest state screenshot">`;
    }
  }

  const stateInfo = lastState && lastState.payload.state ? lastState.payload.state : null;
  host.innerHTML = `
    <h3 class="section">Live view ${S.paused ? '<span class="pill warn">stream paused in Logs</span>' : ""}</h3>
    <div class="grid2">
      <div>
        ${kv([
          ["last state", stateInfo ? `<span class="id">${esc(stateInfo.state_id)}</span>` : "-"],
          ["label", esc(stateInfo ? stateInfo.label : "-")],
          ["url", stateInfo ? `<span class="id">${esc(stateInfo.url)}</span>` : "-"],
          ["elements", esc(stateInfo ? stateInfo.elements : "-")],
          ["summary", esc(stateInfo ? truncate(stateInfo.summary, 200) : "-")],
          ["last action", lastResult ? esc(lastResult.payload.action) : "-"],
          ["action status", lastResult ? statusPill(lastResult.payload.status) : "-"],
          ["verified", lastResult ? (lastResult.payload.verified ? '<span class="pill ok">yes</span>' : '<span class="pill bad">no</span>') : "-"],
          ["locator", lastResult ? esc(lastResult.payload.locator) : "-"],
          ["duration", lastResult ? esc(fixed(lastResult.payload.duration_ms, 1)) + " ms" : "-"],
          ["next proposal", lastProposal ? esc(lastProposal.payload.action) : "-"],
        ])}
        <h3 class="section">Effects of the last action</h3>
        <div class="elems">${lastResult && (lastResult.payload.effects || []).length
          ? lastResult.payload.effects
              .map((effect) => `<div><span class="role">${esc(effect.kind)}</span> <span class="lbl">${esc(truncate(effect.detail || effect.message || "", 90))}</span></div>`)
              .join("")
          : '<div class="faint" style="padding:4px">no action result yet</div>'}</div>
      </div>
      <div>
        ${screenshot || '<div class="muted-box">no screenshot yet for the current state</div>'}
      </div>
    </div>
    <h3 class="section">Recent live events</h3>
    <div class="log-scroll">${recent.map(eventRow).join("") || '<div class="faint" style="padding:5px">no events yet - start an exploration or a task</div>'}</div>
  `;
}

function eventRow(event) {
  return `<div class="evrow">
    <span class="ts">${esc(fmtClock(event.ts))}</span>
    <span class="type">${esc(event.type)}</span>
    <span class="msg" title="${esc(event.summary)}">${esc(event.summary)}</span>
  </div>`;
}

