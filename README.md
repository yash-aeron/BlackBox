# BlackBox

> Can an AI learn the **behavioral structure** of an unfamiliar website through
> black-box interaction, and does that learned model make future task execution
> more reliable and efficient than repeatedly reasoning from the current page?

BlackBox is a research-grade implementation built to answer that question with
measurements rather than assertions. It explores a website the way a person does
— looking at the page and using it — and writes down what it *observed*, not what
it *guessed*. The API is the boundary it never crosses.

**The learned behavioral model is the product.** Task execution is the test of it.

---

## 1. Results at a glance

All numbers below were produced by this repository on this machine. Raw reports
are in `artifacts/benchmarks/`; nothing is hand-written or estimated.

Environment: Python 3.12.10 · Chrome 153.0.8010.12 driven over CDP · headless ·
no LLM provider configured (deterministic path only) · the four bundled demo
sites on `127.0.0.1:3001-3004`.

### 1.1 Baseline vs learned model (CRM, 15 tasks)

`python -m blackbox.benchmarks.runner --target demo_crm --mode compare`

| mode | success rate | mean actions / task | wasted actions | mean planning | recoveries | model reuse |
| --- | --- | --- | --- | --- | --- | --- |
| **baseline** (observe → decide → act, no memory) | 0.200 | 24.40 | 21.27 | 81.6 ms | 29 | 0% |
| **learned model** (workflow/graph reuse, no exploration) | 0.267 | **2.40** | **0.00** | **1.5 ms** | **4** | 53% |
| **learned + targeted exploration** | 0.267 | 11.80 | 0.00 | 2.5 ms | 4 | 53% |

* **90.2% fewer actions** and **53× lower planning latency** than the baseline.
* **7× fewer recoveries**: the baseline thrashes (21 of its 24 actions produce no
  observable effect); the learned model does not take actions it knows are inert.
* Success rate is **+6.7 points**, and it is low in absolute terms (27%). That is
  the honest headline: this is a coverage limitation of the learned model, not a
  flattering metric. Section 3.2 shows what causes it and Section 6.1 what it
  would take to fix.

### 1.2 What the model learned without any site-specific code

From `python tools/inspect_workflows.py --target demo_crm`:

```
* create_customer  conf=0.84
    0. CLICK  CLICK Add customer
    1. TYPE   TYPE Company  = 'BlackBox Labs'       (parameter: company)
    2. TYPE   TYPE Email *  = 'blackbox.sample1@…'  (parameter: email)
    3. TYPE   TYPE Name *   = 'BlackBox Sample'     (parameter: name)
    4. TYPE   TYPE Revenue  = '43'                  (parameter: revenue)
    5. CLICK  CLICK Save customer -> Customer created.

* save_setting  conf=0.83
    0. CLICK   CLICK Settings
    1. UNCHECK UNCHECK Email notifications          (parameter: email_notifications)
    2. CLICK   CLICK Save settings -> Settings saved.
```

CRM model at the end of that exploration run: **7 states, 98 transitions, 44
verified, 2 workflows, 228 evidence records, 30 experiments**. (The database keeps
accumulating as further experiments run; `python tools/model_summary.py` prints
the current totals.) The graph was learned from observation only: the workflow
exists because the browser once showed `Customer created.` after those inputs, and
the miner bound that success to the data entry that preceded it.

### 1.3 A new task executed from the learned model

Task: *"Create a customer named Yash Malhotra with email yash@example.com"* — never
executed during exploration, run with `allow_exploration=false`:

```
success=True  plan_source=workflow  actions=6  steps=6/6  model_reuse=True
note: reused learned workflow w_b5485c1c71e6 (create_customer)
note: parameters using synthesized defaults: company, revenue
success conditions:
  [OK  ] TEXT_PRESENT('Customer created.')  evidence: searched 417 chars of visible text
```

The task's `name` and `email` entities were substituted into the workflow's
parameters; `company` and `revenue` kept their learned values.

### 1.4 Failure injection

`python -m blackbox.benchmarks.faults.inject --target demo_crm --task crm_create_customer`
— a proxy rewrites what the browser receives; the agent does not know it is there.
Each fault is applied to a target task that normally succeeds, and three
properties are checked: no false success, no verification without evidence, and
safe termination inside the authorized origin.

| fault injected | times fired | task still succeeded | handled correctly |
| --- | --- | --- | --- |
| label renamed ("Add customer" → "New record") | 2 | **yes** | yes |
| form field renamed (`name` → `full_name`) | 1 | **yes** | yes |
| control removed (the Name input) | 1 | no | yes |
| control duplicated (the Save button) | 1 | **yes** | yes |
| render delayed by 1.8 s per response | 4 | **yes** | yes |
| unexpected modal dialog injected | 4 | no | yes |
| navigation rewritten to a foreign origin | 2 | no | yes |
| script binding broken | 1 | no | yes |
| HTTP 500 on `app.js` | 1 | no | yes |

**9 of 9 faults injected and handled: the run either recovered with a real
success (4 cases) or terminated safely (5 cases), and in every one of them
`no_false_success = true` and `no_unfounded_verification = true`** — no task was
reported as done, and no transition was marked verified, without the browser
having shown it.

### 1.5 Learning curve

`BLACKBOX_DB=sqlite:///artifacts/curve.db python -m blackbox.benchmarks.runner
--target demo_crm --learning-curve --budgets 10 25 50 100 150 200`
— a fresh model, explored in increments; after each increment four probe tasks run
against the model with **no exploration allowed**.

| exploration actions | states | verified transitions | workflows | probe task success |
| --- | --- | --- | --- | --- |
| 10 | 3 | 5 | 0 | 0.00 |
| 25 | 4 | 15 | 0 | 0.25 |
| 50 | 6 | 33 | 0 | 0.25 |
| 100 | 7 | 70 | 0 | 0.25 |
| 150 (40 spent) | 7 | 70 | 0 | 0.25 |
| 200 (40 spent) | 7 | 70 | 0 | 0.25 |

The curve rises from 0 to 0.25 and then **plateaus**: the explorer stops when it
stops making progress (40 actions without a new state or verified transition), so
later increments add nothing. The create-customer workflow *is* reachable — the
longer CRM run in section 1.2 learned it in 140 actions — but this specific
budget ladder does not reach it reliably. Reporting the plateau is more useful
than hiding it: it is the clearest evidence that **exploration policy, not model
representation, is the limiting factor** (see Section 6.1).

### 1.6 Ablations

`python -m blackbox.benchmarks.runner --target demo_crm --ablations` — the same
eight tasks, with progressively more of the learned model available to the
planner:

| configuration | success rate | mean actions | planning | model reuse |
| --- | --- | --- | --- | --- |
| 1. no model (reactive baseline) | 0.25 | 23.0 | 44.7 ms | 0% |
| 2. persistent **states** only | **0.00** | 30.0 | 0.08 ms | 0% |
| 3. states + **transitions** | **0.50** | **8.1** | 2.6 ms | 62.5% |
| 4. states + transitions + **workflows** | 0.50 | 8.1 | 2.5 ms | 62.5% |

What this says, and it is not flattering to every component:

* **Transitions are where the value is.** Going from no model to
  states+transitions doubles success (0.25 → 0.50) and cuts actions by 65%
  (23.0 → 8.1), while planning drops from 45 ms to 2.6 ms.
* **States alone are worth nothing for execution** — and are *worse than the
  baseline* (0.00 vs 0.25, 30 vs 23 actions). Knowing where you are without
  knowing how to get anywhere makes the agent explore, spend its budget, and
  still have no plan.
* **Workflows add no measurable gain on this subset.** On these eight tasks the
  shortest-route rule prefers graph routes, and the workflow path shows up mainly
  on multi-step goals — the single-task runs in §1.3 and §1.4 do execute with
  `plan_source=workflow`. Claiming a workflow benefit from this table would be
  over-reading it.

### 1.7 Tests

```
python -m pytest tests -q -m "not browser and not e2e"   # 48 passed
python -m pytest tests -q -m "browser"                   # 5 passed
python -m pytest tests -q -m "e2e"                       # 4 passed
```

The suite covers fingerprinting and similarity, element normalisation, locator
generation, transition detection, hypothesis management, precondition and
postcondition learning, action ranking, safety classification, graph search,
workflow extraction, model serialisation and matching, recovery, prompt-injection
handling, metrics, the benchmark task library, the CDP browser layer and
end-to-end exploration of two demos.

---

## 2. Why this is not "HTML scraper → LLM → clicks"

| Conventional agent | BlackBox |
| --- | --- |
| Decides from the current page each time | Writes a persistent behavioral model and plans over it |
| An LLM chooses the next click | Deterministic ranking by expected information gain; the LLM is optional and never in the control path |
| Success = the script finished | Success = the declared success conditions were observed *and* every step's predicted effect was verified |
| Knowledge lives in the prompt or the code | Knowledge is a graph of states and transitions, each carrying evidence ids |
| "It worked yesterday" is unknowable | Confidence is a function of independent success with decay; website changes mark knowledge STALE instead of deleting it |

---

## 3. How it works

### 3.1 The loop

```
OBSERVE → BUILD STATE → IDENTIFY UNKNOWN BEHAVIOR → GENERATE CANDIDATES
→ SAFETY FILTER → RANK BY INFORMATION GAIN → EXECUTE → OBSERVE RESULT
→ COMPARE STATES → IDENTIFY EFFECTS → UPDATE HYPOTHESES → UPDATE GRAPH
```

1. **Observe** the settled page. Loading indicators are waited out, so a
   mid-render frame is never mistaken for an application state.
2. **Build a state** from a multi-signal fingerprint (URL, title, text shingles,
   element structure, accessibility structure, form filled-ness, filter values,
   dialogs, pagination, screenshot dHash) and match it against known states.
3. **Generate candidates**: every visible actionable control, typed values
   synthesised from the field's own semantics (`email`, `phone`, `pattern`,
   `min`/`max`), plus the experiment that would test an open hypothesis.
4. **Filter for safety** (LOW/MEDIUM/HIGH/CRITICAL from the control's *meaning*).
5. **Rank by information gain**: novelty, hypothesis value, capability of the
   control, minus risk, repetition and known-no-effect penalties.
6. **Execute** with real mouse/keyboard events, bounded retries, locator
   regeneration and recovery.
7. **Compare** observations (`diff_states`) and **attribute** effects.
8. **Update** the graph, hypotheses, constraints and evidence.

### 3.2 State identity is not the URL

`/customers` with no filter and `/customers` with a lead filter are different
states (`s_55eee504b8d7` vs `s_e2d3bafeb954` in the learned CRM model). Volatile
differences — timestamps, generated ids, opaque tokens — are masked before
comparison so they cannot split a state, and typing is deliberately *not* a new
state: it changes a form's filled-ness (an effect) without inventing a screen.

This is also why coverage, not correctness, limits section 1.1: a state the
explorer never reached cannot be planned through.

### 3.3 Hypotheses are not knowledge

```
Observation: Save customer is disabled
Hypothesis:  Name and Email may be required        [PROPOSED]
Experiment:  type into Name, then Email
Observation: Save customer becomes enabled
Verified:    name != empty AND email != empty → Save enabled   [VERIFIED]
```

Statuses: `PROPOSED → SUPPORTED → VERIFIED`, `REFUTED` on contradiction, `STALE`
when the site changes. On the four-step wizard the explorer proposed **25
hypotheses** from disabled controls and validation messages in a 31-action run.

### 3.4 Preconditions learned from failure

`python tools/list_transitions.py` shows constraints derived from observed
blocking messages, for example *"Select a date range before exporting."* →
`date_range != null` on `CLICK Export report`, or *"This task is blocked by an
incomplete task."* → a dependency precondition. They are parsed from what the
browser displayed; unparsed messages are kept as low-confidence opaque
preconditions rather than discarded.

### 3.5 Workflows are mined, not written

A workflow needs (a) a **success** effect — a confirmation message or an observed
data change — never mere navigation; (b) the data-entry steps that happened in
that state *before* the success, taken from evidence timestamps; (c) the route
from the entry state; and (d) at least one parameter, lifted from the literal
value that was typed. `verification_count` only rises when the whole workflow is
executed again.

### 3.6 Planning and replanning

The planner prefers, in order: a **learned workflow** matching the goal, or a
**graph route** to a state satisfying the task's declared success conditions —
whichever is shorter, preferring safer and higher-confidence transitions. With
no route it returns *"exploration required"* plus the region to explore, and
`Replanner` retries, routes around the failure, or runs **targeted** exploration
(never a full re-exploration, and never more than a bounded number of replans).

### 3.7 Verification and recovery

Every step is verified against the effect the model predicted. A step that
silently did nothing counts as a failure. Recovery is bounded:
`OBSERVE → COMPARE → REASSESS → RETRY OR ALTERNATE → REPLAN → SAFE STOP`, with a
default of two retries per action, and an origin violation is always a safe stop.

---

## 4. Safety architecture

| Control | Where it is enforced | What it produces |
| --- | --- | --- |
| Only registered origins | `browser/sandbox.py`, checked before and after every navigation | `blocked_log` with reasons |
| No direct API calls by the agent | `observation/network_guard.py`, refuses `fetch`/`XHR`/`WebSocket`/`sendBeacon` in agent JavaScript | `blocked_agent_requests` + the refused snippet |
| No API traffic reconstruction | The guard refuses CDP commands that expose bodies; observed requests are counted **by resource type only** — no URLs, headers or bodies are stored | `observed_by_type`, `external_origins_seen` (origins only) |
| Risky actions need a decision | `exploration/safety.py`: LOW autonomous, MEDIUM/HIGH/CRITICAL per registration; unattended runs refuse and record | `approval_requested` / `human_decision` evidence |
| Page text is never an instruction | Page content only ever feeds deterministic logic or a *validated structured output* call; there is no path from page text to a command | prompt-injection tests |
| Credentials are not persisted | Password fields are observed as `null`; cookie values are never stored (names only); session snapshots keep names and counts | `ValueState(value=None)`, `cookie_names` |

Unattended runs (the default here) refuse anything above the permitted risk
immediately and record why, instead of blocking on a human who is not there.

---

## 5. Setup and commands

### 5.1 Install

```bash
python -m pip install -r requirements.txt          # or: pip install -e ".[dev]"
cp .env.example .env                               # optional; every value has a default
```

BlackBox needs **no database server** (SQLite is the default) and **no browser
package**: it drives Chromium over the DevTools Protocol with `aiohttp`. If
`playwright` is installed it can be used instead, behind the same interface.
PostgreSQL and Redis are optional deployment extras:

```bash
docker compose up -d
BLACKBOX_DB=postgresql://blackbox:blackbox@127.0.0.1:5432/blackbox python -m blackbox.agent.main status demo_crm
```

Chromium is located automatically: `BLACKBOX_CHROMIUM`, then the Playwright
browser cache, then the system install locations.

### 5.2 Run the demo websites

```bash
python -m blackbox.demo.server          # all four on 3001-3004
python -m blackbox.demo.server --app crm --port 3001   # a single one
```

### 5.3 Explore a target

```bash
python -m blackbox.agent.main explore demo_crm --max-actions 120 --export artifacts/models/demo_crm.json
python -m blackbox.agent.main explore demo_crm --headed        # watch it work
python -m blackbox.agent.main status demo_crm                  # browser, model and policy state
python -m blackbox.agent.main mine demo_crm                    # re-mine workflows, no browser needed
```

### 5.4 Run a task against the learned model

```bash
python -m blackbox.agent.main task demo_crm \
  "Create a customer named Yash with email yash@example.com" \
  --expect-text "Customer created." --no-explore
```

### 5.5 Benchmarks

```bash
# full task suite, learned model only (no exploration)
python -m blackbox.benchmarks.runner --target demo_crm --mode learned

# baseline vs learned vs learned+targeted-exploration
python -m blackbox.benchmarks.runner --target demo_crm --mode compare

# learning curve: exploration budget vs task success (fresh model each run)
BLACKBOX_DB=sqlite:///artifacts/curve.db \
python -m blackbox.benchmarks.runner --target demo_crm --learning-curve \
  --budgets 10 25 50 100 --probe-tasks crm_open_add_dialog crm_pagination_next crm_create_customer

# ablations: no model / states only / states+transitions / full
python -m blackbox.benchmarks.runner --target demo_crm --ablations

# failure injection through a response-rewriting proxy
python -m blackbox.benchmarks.faults.inject --target demo_crm --task crm_create_customer
```

### 5.6 Service and dashboard

```bash
python -m uvicorn blackbox.api.main:app --port 8099
# dashboard:      http://127.0.0.1:8099/
# OpenAPI docs:   http://127.0.0.1:8099/docs
```

The React + TypeScript dashboard source lives in `blackbox/dashboard/`
(`npm install && npm run dev`, proxying `/api` to port 8099). Because this
environment has no network access, the *served* dashboard in
`blackbox/api/static/` is a dependency-free build of the same views that runs
today; both consume the same API.

![State graph](artifacts/dashboard-graph.png)

![Overview](artifacts/dashboard-overview.png)

### 5.7 Tests

```bash
python -m pytest tests -q -m "not browser and not e2e"   # fast, no browser
python -m pytest tests -q -m "browser"                   # launches Chromium
python -m pytest tests -q -m "e2e"                       # drives the demo sites
```

---

## 6. Limitations and research directions

### 6.1 Known limitations (measured, not hypothetical)

1. **Coverage bounds success.** With 120 exploration actions the CRM model
   covers 7 states; tasks whose goal state was never reached cannot be planned
   and score as failures (27% success). The learning curve in
   `artifacts/benchmarks/demo_crm-learning-curve.json` shows success rising with
   exploration but plateauing while the create-customer success transition is
   still unlearned.
2. **Value synthesis is generic.** Field values come from semantics and
   attributes (`pattern`, `min`, `max`, `type`), not from site context. A field
   whose acceptable values are domain-specific (a coupon code, a valid SKU) is
   only discovered by trying and observing rejection.
3. **Login walls are out of scope.** Credentials are never typed or stored; a
   target needing authentication expects a human to sign in first, in headed
   mode.
4. **Canvas and cross-origin iframes are partially observable.** The DOM and
   accessibility channels contribute little there; screenshots are captured but
   only used for similarity, not for control discovery.
5. **No visual grounding model.** Icon-only controls with no accessible name are
   detected structurally but their meaning is not inferred without an LLM/VLM
   provider configured.
6. **State matching is heuristic.** Weighted multi-signal similarity with fixed
   thresholds; on sites with rapid live content (tickers, counters) it can split
   one logical screen into several states.
7. **The baseline is deterministic, not an LLM agent.** No LLM was reachable on
   this machine, so the comparison isolates the *learned model* against a
   same-primitives reactive policy. The baseline has an LLM path
   (`--mode baseline` with a provider configured) that was not exercised here.

### 6.2 Research directions

1. **Active learning over the graph**: choose exploration by expected reduction
   in planning uncertainty, not just state novelty.
2. **Value inference from outcomes**: learn what a field accepts from rejection
   messages and successful submissions (constraint synthesis rather than
   pattern synthesis).
3. **Workflow generalisation**: merge near-identical workflows across entity
   types into parameterised templates ("create X" rather than "create customer").
4. **Change detection as a first-class experiment**: schedule cheap re-verification
   of high-confidence transitions and quantify model decay over time.
5. **Vision-grounded element meaning** via VLM proposals fed through the same
   validator and safety policy.
6. **Cost-aware exploration** that prices each action by its runtime and risk, not
   only by information gain.

---

## 7. Repository layout

```
blackbox/
├── agent/
│   ├── observation/    browser · dom · accessibility · screenshot · network_guard · fusion
│   ├── perception/     element_detector · semantic_labeler · state_fingerprint · page_classifier
│   ├── exploration/    explorer · action_generator · action_ranker · novelty · experiment · safety
│   ├── learning/       hypothesis · causal · preconditions · effects · confidence · workflow_miner
│   ├── planning/       task_parser · planner · replanner · verifier
│   ├── execution/      executor · locator · recovery · assertions
│   ├── model/          website · page · element · action · state · transition · workflow ·
│   │                   constraint · graph · evidence · base
│   ├── llm/            provider · prompts · schemas        (optional, never in the control path)
│   ├── storage/        database · repository · targets
│   ├── config.py       settings, demo target registration
│   └── main.py         the agent facade + CLI
├── browser/            manager · context · permissions · sandbox · cdp
├── api/                main · runtime · routes/ · schemas/ · static/ (served dashboard)
├── dashboard/          React + TypeScript dashboard source (Vite)
├── demo/               crm · ecommerce · project_manager · forms · server.py
├── benchmarks/         tasks/ (52 tasks) · baseline · runner · metrics/ · faults/
docs/                   architecture · behavioral model · methodology
tests/                  unit · browser integration · end-to-end
tools/                  model reports, workflow inspector, page debugger, screenshots
docker-compose.yml      optional PostgreSQL + Redis
```

### 7.1 Behavioral model schema (abridged)

```jsonc
{
  "website": {"target_id": "demo_crm", "base_url": "...", "allowed_origins": ["..."], "identity_hash": "..."},
  "model_version": {"version": 1, "schema_version": 1, "browser_version": "Chrome/153.0.8010.12", "prompt_version": "v1"},
  "states": [{
    "state_id": "s_c90cfdf139fd", "url": ".../#/customers", "page_identity": "127.0.0.1:3001/customers",
    "fingerprint": {"url_key": "...", "text_signature": "…", "element_signature": ["BUTTON:Add customer", "..."],
                    "form_signature": ["company:empty", "..."], "selected_controls": {"SELECT:status": "lead"},
                    "dialog_signature": [], "screenshot_hash": "…", "semantic_summary": "…"},
    "visible_elements": [{"element_id": "e_…", "semantic_role": "BUTTON", "accessible_name": "Add customer",
                          "locator_candidates": [{"strategy": "ROLE_NAME", "confidence": 0.95, "evidence": "…"}]}],
    "status": "CURRENT", "confidence": 0.7, "visit_count": 23, "screenshot_ref": "artifacts/screenshots/…png"
  }],
  "transitions": [{
    "transition_id": "t_…", "source_state": "s_…", "target_state": "s_…",
    "action": {"type": "CLICK", "target": {"name": "Save customer", "locators": ["…"]}, "risk": "MEDIUM", "confidence": 0.9},
    "observed_effects": [{"kind": "SUCCESS_MESSAGE", "message": "Customer created."}],
    "preconditions": [], "postconditions": ["message:Customer created."],
    "status": "VERIFIED", "confidence": 0.86, "execution_count": 5, "success_count": 5, "evidence_ids": ["ev_…"]
  }],
  "workflows": [{"workflow_id": "w_…", "goal": "create_customer",
                 "parameters": [{"name": "email", "kind": "EMAIL", "example_value": "blackbox.sample1@example.com"}],
                 "steps": [{"index": 0, "action": {"type": "CLICK", "target": {"name": "Add customer"}}, "parameter_names": []}],
                 "expected_end_state": "create a customer", "confidence": 0.84, "verification_count": 1}],
  "constraints": [{"kind": "PRECONDITION", "subject": "CLICK Export report", "expression": "date_range != null",
                   "message": "Select a date range before exporting.", "status": "SUPPORTED", "confidence": 0.6}],
  "hypotheses": [{"statement": "Save customer may require Name to be filled", "status": "VERIFIED", "confidence": 0.85}],
  "evidence": [{"evidence_id": "ev_…", "kind": "ACTION_RESULT", "transition_id": "t_…",
                "effects": ["…"], "message": "…", "created_at": "…"}]
}
```

Full schema and invariants: [`docs/behavioral-model.md`](docs/behavioral-model.md).
Algorithms in detail: [`docs/architecture.md`](docs/architecture.md).
Measurement method and threats to validity: [`docs/methodology.md`](docs/methodology.md).

---

## 8. Authorization and scope

BlackBox is for websites you are **explicitly authorized** to inspect and
automate. It requires an explicit target registration naming the allowed origins,
enforces them in code, and provides no CAPTCHA/authentication bypass, no
credential capture, no anti-bot evasion and no hidden-API access. The bundled
demo sites exist so the system can be developed and measured without touching
anyone else's property — do not repoint it at third-party sites.
