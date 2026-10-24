# BlackBox — architecture

> Learn how an unknown website behaves by interacting with it, and keep what you
> learned. The API is the boundary the system never crosses.

## The idea in one line

```
OBSERVATION → ACTION → RESULT → STATE CHANGE → HYPOTHESIS → EXPERIMENT
→ VERIFIED BEHAVIOR → BEHAVIORAL MODEL → REUSABLE WORKFLOW
```

BlackBox is not a scraper and not a one-shot browser agent. Its product is a
**persistent, evidence-backed behavioral model** of a website, and the test of
that model is whether a later task runs better because of it.

## Layers

```
                      ┌──────────────────────────────────────────────┐
   research dashboard │  React + TS (blackbox/dashboard)             │
   (human)            │  served fallback UI (blackbox/api/static)    │
                      └───────────────┬──────────────────────────────┘
                                      │ HTTP + SSE
                      ┌───────────────▼──────────────────────────────┐
   service            │  FastAPI (blackbox/api)                      │
                      │  targets · explore · tasks · graph · evidence│
                      └───────────────┬──────────────────────────────┘
                                      │ in-process
   ┌──────────────────────────────────▼───────────────────────────────────┐
   │  Agent (blackbox/agent/main.py)                                      │
   │                                                                      │
   │  exploration/   generator · ranker (info gain) · safety · novelty    │
   │                 experiment budgets · the OBSERVE→…→UPDATE loop       │
   │  learning/      hypotheses · causal attribution · preconditions ·    │
   │                 effects/postconditions · confidence · workflow miner │
   │  planning/      task parser · graph planner · replanner · verifier   │
   │  execution/     locator resolution · executor · assertions · recovery│
   │  perception/    element detector · semantic labeler · state          │
   │                 fingerprinting + diff engine · page classifier       │
   │  observation/   DOM · accessibility · screenshot · fusion ·          │
   │                 NetworkGuard                                         │
   │  model/         element · action · state · transition · workflow ·   │
   │                 constraint · evidence · page · graph · website       │
   │  storage/       SQLite/PostgreSQL repository · target registry       │
   │  llm/           provider abstraction (optional, never in control)    │
   └──────────────────────────────────┬───────────────────────────────────┘
                                      │ Chrome DevTools Protocol
                      ┌───────────────▼──────────────────────────────┐
   browser boundary   │  browser/  manager · context · permissions · │
                      │  sandbox (allowed_origins)                   │
                      └──────────────────────────────────────────────┘
```

## Why the browser boundary is code, not a promise

`NetworkGuard` and `Sandbox` are the enforcement points:

| Rule | Enforced by | Evidence produced |
| --- | --- | --- |
| Only registered origins are visited | `Sandbox.check()` before every navigation, and `assert_current_url()` after page-initiated navigation | `blocked_log` entries with reason |
| The agent never calls a backend API directly | `NetworkGuard.check_expression()` refuses `fetch`/`XMLHttpRequest`/`WebSocket`/`sendBeacon` inside agent-issued JavaScript | `blocked_agent_requests` counter + logged snippet |
| API traffic is never captured or replayed | `NetworkGuard.check_command()` refuses CDP commands that expose bodies; observed requests are counted **by resource type only** - no URLs, headers or bodies are retained | `observed_by_type`, `external_origins_seen` (origins only) |
| Page text cannot control the agent | Page content is only ever fed to the deterministic pipeline or to a *validated structured-output* LLM call; there is no path from page text to a command | the injection tests in `tests/test_safety_and_guard.py` |

The prohibition is structural: there is no code path in the agent that issues an
HTTP request to a target, so "don't use the API" cannot be violated by accident.

## The observation model

Observation is multi-channel on purpose, because each channel fails differently:

| Channel | File | What it contributes | When it is weak |
| --- | --- | --- | --- |
| Rendered DOM | `observation/dom.py` | visible text, interactive elements, geometry, forms, tables, dialogs, alerts, pagination, accessible names | canvas UIs, icon-only controls |
| Accessibility tree | `observation/accessibility.py` | roles, names, and states (disabled/required/invalid/expanded) the browser itself asserts | unlabelled custom widgets |
| Screenshot | `observation/screenshot.py` | perceptual hash for similarity; the human-visible artifact | text-only differences, canvas content without DOM |
| Browser state | `observation/browser.py` | URL, title, cookies (names only), scroll, native dialogs, downloads, console errors | nothing about application logic |

`fusion.py` folds them into one `Observation` and records `channel_health`, so a
degraded run is visible rather than silent. A state is then described by a
**fingerprint** over all of them (`perception/state_fingerprint.py`), which is
what makes two states on the same URL distinguishable:

```
/customers                       /customers
  filter = none                    filter = active
  text shingles A                  text shingles B
  element set A                    element set B
  → state s_55eee504b8d7           → state s_e2d3bafeb954
```

Similarity is a weighted score in `[0,1]` with configurable thresholds
(`same_state`, `same_page`), and volatile differences (timestamps, generated ids,
opaque tokens) are masked before comparison so they cannot split a state.

## The learning loop

`exploration/explorer.py` implements one iteration as:

1. **Observe** the settled page (waiting out loading indicators, so a mid-render
   frame is never mistaken for a state).
2. **Build a state** and match it against known states; a new state is written to
   the graph, and disabled controls in it become *requirement hypotheses*.
3. **Generate candidates** - every visible actionable control, plus data-entry
   actions with synthesized values, plus the experiments that would test an open
   hypothesis.
4. **Filter for safety** - `SafetyClassifier` assigns LOW/MEDIUM/HIGH/CRITICAL
   from the control's role and meaning; the `SafetyPolicy` decides whether that
   risk may proceed unattended.
5. **Rank by information gain** - novelty, hypothesis-testing value, capability
   of the control, minus risk, repetition and known-no-effect penalties.
6. **Execute** with real input events, bounded retries, locator regeneration and
   recovery.
7. **Compare states** (`diff_states`) and **attribute effects** to the action.
8. **Update** hypotheses, constraints (preconditions), transitions (with
   evidence) and the graph.

The loop stops on budget exhaustion, on a lack of progress, on a detected
repetition loop, or when it would have to leave the authorized origins.

## Knowledge and its evidence

| Artifact | Meaning | Promotion rule |
| --- | --- | --- |
| `Transition` | in state A, action X led to state B | VERIFIED only after the predicted effect was observed; REFUTED after repeated failure |
| `Hypothesis` | a rule under test | PROPOSED → SUPPORTED/VERIFIED by experiments, → REFUTED by contradiction, → STALE when the site changes |
| `Constraint` | a precondition, validation rule or postcondition | created from an observed blocking message or a disabled control, strengthened by each confirming experiment |
| `Workflow` | a goal-shaped, parameterized procedure | mined from a *success* effect plus the data entry that preceded it; `verification_count` only increases when the whole workflow is executed again |
| `Evidence` | the audit record behind all of the above | every action result, message, blocked attempt and human decision |

Nothing in the model can be asserted without an evidence id, and confidence is a
function of independent success (with decay over time), not of plausibility.

## Execution and planning

`planning/planner.py` prefers, in order: a learned **workflow** whose goal matches
the task, or a **graph route** to a state satisfying the task's declared success
conditions - whichever is shorter. When neither exists, the plan says so and
names the region to explore; `Replanner` then retries, routes around the failure,
or runs *targeted* exploration (never a full re-exploration).

Every step is verified: predicted effects are compared against the observed
state difference, the transition's success/failure counters are updated, and a
step that silently did nothing is a failure rather than a success.

## Persistence and versioning

`storage/repository.py` writes the model to portable SQL (SQLite by default,
PostgreSQL by DSN). Model versions are *continued*, not recreated, so a restart
reuses what was learned; `import_model`/`export_model` move a model as JSON; and
website changes mark knowledge `STALE` instead of deleting it, so the history of
a claim survives.

## Where the optional LLM sits

`llm/provider.py` exposes eight structured operations. Every response is a
validated pydantic object from `llm/schemas.py`, every prompt states that page
text is data and that the model cannot control the browser, and every proposal
still passes through the safety policy and the executor. With no provider
configured - the default, and how the measurements in the README were produced -
`NullProvider` returns `None` and the deterministic path runs alone.
