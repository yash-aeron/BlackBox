# The BlackBox behavioral model

## Invariants

These hold across the whole system and are asserted by the test suite.

1. **Nothing is asserted without evidence.** Every `Transition`, `Constraint`,
   `Hypothesis` and `Workflow` carries evidence ids, and every evidence record
   names the observation or action result that produced it.
2. **A transition points at an observed state.** `target_state` is always a state
   that exists in the graph because the browser was seen in it.
3. **VERIFIED means observed, not predicted.** A transition is VERIFIED only after
   its predicted effect was actually seen; it is REFUTED after repeated failure.
   A VERIFIED transition can never have `execution_count > 0` and
   `success_count == 0`.
4. **A state is not a URL.** State identity is the weighted multi-signal
   fingerprint; the URL is one signal among nine.
5. **Knowledge is never deleted, only superseded.** Website change marks states,
   transitions and hypotheses `STALE`; the history remains queryable.
6. **The browser is the only interaction channel.** No model object can be
   written from an HTTP response body, because none is ever captured.

## Entities

### Element

```jsonc
{
  "element_id": "e_90003d394277",          // content-addressed: role + name + name-attr
  "semantic_role": "TEXT_FIELD",           // BUTTON, LINK, TEXT_FIELD, SEARCH, SELECT, CHECKBOX,
                                           // RADIO, TAB, MENU_ITEM, DIALOG, UPLOAD, DOWNLOAD, ...
  "accessible_name": "Name *",             // what a screen reader would announce
  "visible_text": "",                      // rendered text inside the node
  "tag": "input",
  "attributes": {"id": "customer-name", "name": "name", "type": "text", "__css_path": "..."},
  "bounding_box": {"x": 420.0, "y": 310.5, "width": 260.0, "height": 32.0},
  "enabled": true, "visible": true, "focused": false, "editable": true,
  "value_state": {"value": "", "checked": null, "selected": null},
  "locator_candidates": [                   // strongest first; coordinates always last
    {"strategy": "ROLE_NAME",   "confidence": 0.95, "evidence": "accessible name from <label>"},
    {"strategy": "LABEL",       "confidence": 0.89, "evidence": "associated <label> element"},
    {"strategy": "ELEMENT_ID",  "confidence": 0.88, "evidence": "element id"},
    {"strategy": "NAME_ATTR",   "confidence": 0.82, "evidence": "form control name attribute"},
    {"strategy": "CSS_PATH",    "confidence": 0.55, "evidence": "structural path"},
    {"strategy": "COORDINATES", "confidence": 0.25, "evidence": "last resort: geometry only"}
  ],
  "confidence": 0.95, "source": "dom+ax"
}
```

### State

```jsonc
{
  "state_id": "s_c90cfdf139fd",            // content-addressed from the fingerprint
  "url": "http://127.0.0.1:3001/#/customers",
  "page_identity": "127.0.0.1:3001/customers",
  "page_id": "p_…",
  "fingerprint": {
    "url_key": "127.0.0.1:3001/customers", // ids masked, params sorted
    "title": "northwind crm",
    "text_signature": "…",                 // hash of scrubbed text shingles
    "text_shingles": ["…"],                // bounded, hashed 3-grams
    "element_signature": ["BUTTON:add customer", "LINK:customers", "…"],
    "accessibility_signature": ["button:add customer", "link:customers"],
    "form_signature": ["company:empty", "email *:filled"],
    "selected_controls": {"SELECT:status": "lead"},
    "dialog_signature": [], "pagination_signature": "Page 1 of 3|true|false",
    "screenshot_hash": "a4f19c0b…",        // 64-bit difference hash
    "semantic_summary": "Northwind CRM | table: 5 rows, columns NAME, EMAIL, …"
  },
  "visible_elements": [ /* Element[] */ ],
  "forms": [ /* FormSummary[] */ ],
  "dialogs": [], "pagination": {...}, "session": {"cookie_names": ["sid"]},
  "status": "CURRENT", "confidence": 0.7, "visit_count": 23,
  "observation_id": "o_…", "screenshot_ref": "artifacts/screenshots/obs-….png",
  "first_seen": "…", "last_seen": "…"
}
```

**Similarity** is a weighted sum over nine signals (default weights: elements
0.24, text 0.22, url 0.20, accessibility 0.08, title 0.06, dialogs 0.06, forms
0.05, controls 0.05, screenshot 0.04) with thresholds `same_state = 0.90` and
`same_page = 0.68`. Both are configurable per run, which is how the ablation and
sensitivity experiments are expressed.

### Action

```jsonc
{
  "action_id": "a_1c0f…",
  "type": "TYPE",                          // CLICK, TYPE, CLEAR, SELECT, CHECK, UNCHECK, SUBMIT,
                                           // PRESS_KEY, HOTKEY, SCROLL, OPEN_MENU, CLOSE_DIALOG,
                                           // NAVIGATE_BACK, NAVIGATE, UPLOAD, DOWNLOAD, WAIT_FOR_STATE
  "target": {"element_id": "e_…", "role": "TEXT_FIELD", "name": "Name *", "locators": [ /* … */ ]},
  "parameters": {"value": "Yash"},
  "expected_effect": ["VALUE_CHANGED", "FORM_CHANGED"],
  "risk": "LOW",
  "confidence": 0.95,
  "origin": "generator",                   // generator | hypothesis | planner | llm
  "rationale": "fill Name * with a synthesized value"
}
```

### Transition

```jsonc
{
  "transition_id": "t_…", "source_state": "s_…", "target_state": "s_…",
  "action": { /* Action */ },
  "observed_effects": [{"kind": "SUCCESS_MESSAGE", "message": "Customer created.", "detail": "…"}],
  "preconditions": [], "postconditions": ["message:Customer created."],
  "status": "VERIFIED",                    // PROPOSED | VERIFIED | REFUTED | STALE
  "confidence": 0.86,
  "execution_count": 5, "success_count": 5, "failure_count": 0,
  "evidence_ids": ["ev_…"], "created_at": "…", "updated_at": "…"
}
```

Effect kinds: `NAVIGATION`, `URL_CHANGE`, `DIALOG_OPENED`, `DIALOG_CLOSED`,
`ELEMENTS_ADDED`, `ELEMENTS_REMOVED`, `ELEMENTS_CHANGED`, `TEXT_CHANGED`,
`FORM_CHANGED`, `VALUE_CHANGED`, `VALIDATION_MESSAGE`, `SUCCESS_MESSAGE`,
`LOADING_STARTED`, `LOADING_FINISHED`, `DATA_CHANGED`, `PRECONDITION_BLOCKED`,
`NO_EFFECT`, `ERROR`.

### Workflow

```jsonc
{
  "workflow_id": "w_b5485c1c71e6", "name": "Create Customer", "goal": "create_customer",
  "parameters": [
    {"name": "email", "label": "Email *", "kind": "EMAIL", "required": true,
     "example_value": "blackbox.sample1@example.com", "target_element_id": "e_…"}
  ],
  "steps": [
    {"index": 0, "description": "CLICK Add customer", "parameter_names": [],
     "transition_id": "t_…", "expected_effect": ["DIALOG_OPENED"]},
    {"index": 1, "description": "TYPE Name * = 'BlackBox Sample' (parameters: name)",
     "parameter_names": ["name"], "transition_id": "t_…"}
  ],
  "expected_end_state": "create a customer",
  "start_state_id": "s_db233137f595",
  "path_state_ids": ["s_db233137f595", "s_0c3313f77f4f", "s_db233137f595"],
  "confidence": 0.84, "verification_count": 1, "evidence_ids": ["…"]
}
```

### Hypothesis, Constraint, Evidence

```jsonc
// Hypothesis
{"hypothesis_id": "h_…", "statement": "Save customer may require Name to be filled",
 "kind": "REQUIREMENT", "status": "VERIFIED", "confidence": 0.85,
 "prediction": {"field_id": "e_…", "field_label": "Name *", "enables": "e_…"},
 "supporting_experiments": ["field-fill"], "contradicting_experiments": [],
 "evidence": ["ev_…"], "created_at": "…", "updated_at": "…"}

// Constraint
{"constraint_id": "c_…", "kind": "PRECONDITION", "scope": "ACTION",
 "subject": "click save customer", "expression": "name != empty",
 "condition": {"field": "Name *", "op": "!=", "value": "", "field_ids": ["e_…"], "state_id": "…"},
 "message": "Name is required.", "status": "SUPPORTED", "confidence": 0.6,
 "supporting_evidence": ["ev_…"], "contradicting_evidence": []}

// Evidence
{"evidence_id": "ev_…", "kind": "ACTION_RESULT", "experiment_id": "exp_…",
 "action_id": "a_…", "source_state": "s_…", "target_state": "s_…", "transition_id": "t_…",
 "effects": [ /* ObservedEffect[] */ ], "observations": ["o_…", "o_…"],
 "screenshot_refs": ["…"], "message": "SUCCESS_MESSAGE: Customer created.",
 "notes": [], "created_at": "…"}
```

Evidence kinds: `STATE_OBSERVATION`, `ACTION_RESULT`, `VALIDATION_MESSAGE`,
`SUCCESS_MESSAGE`, `FAILURE`, `BLOCKED_ACTION`, `NETWORK_GUARD`,
`HUMAN_DECISION`, `FAULT_INJECTION`.

## Confidence

`ConfidenceModel` maps independent success and failure to a value in
`[0.02, 0.99]`: base 0.35, +0.18 per success (capped at 4), −0.25 per failure,
+0.15 when verified, then decayed toward 0.5 with a 21-day half-life. A
transition with no successes is capped at 0.25. Thresholds: `0.5` to act on a
claim, `0.75` to trust it without re-verification.

## Graph queries

| Query | Purpose |
| --- | --- |
| `find_state(...)` / `find_states(predicate)` | locate states by url/title/text/predicate |
| `find_transition(source, target, action_signature)` | locate an edge |
| `find_workflow(goal)` | match a goal to a learned procedure (exact, then token overlap) |
| `find_path(start, goal, cost)` | Dijkstra over verified-ish edges, cost = length + risk + (1−confidence) + unverified penalty |
| `find_prerequisites(subject)` | preconditions affecting an action |
| `find_known_failure(action_signature)` | evidence where an action was blocked or validated against |
| `find_related_evidence(transition|hypothesis|constraint)` | the audit trail for a claim |
| `to_graph_json()` | nodes/edges/stats for the dashboard |

## Storage

Portable SQL: `targets`, `models`, `states`, `transitions`, `workflows`,
`constraints`, `hypotheses`, `evidence`, `experiments`, `observations`, `pages`,
`events`, `runs`. Model objects are stored as JSON documents keyed by their
content-addressed ids, with indexed relational columns for the fields that are
queried. SQLite is the default; PostgreSQL works through the same code path with
a DSN change. `export_model` / `import_model` move the whole model as JSON, and
imports create a new model version rather than overwriting the previous one.
