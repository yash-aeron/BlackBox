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

