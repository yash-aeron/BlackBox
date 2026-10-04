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

## About BlackBox

**BlackBox** is an autonomous web agent and behavioral learning engine that constructs persistent, verifiable state-transition models of unfamiliar web applications through black-box interaction alone.

### Why BlackBox?
Traditional web agents rely on a continuous "screenshot/HTML &rarr; LLM prompt &rarr; click" loop. This pattern is:
1. **Stateless and forgetful**: The agent starts from zero context on every session, relearning the interface at full token cost.
2. **Prone to thrashing**: Without causal memory, agents repeatedly click inert elements or trigger validation failures without understanding why.
3. **Slow and expensive**: Sending entire page contexts to an LLM for routine clicks introduces multi-second latency and high API costs.
4. **Vulnerable**: Relying on unconstrained LLM interpretation creates prompt-injection and runaway action risks.

### How BlackBox Solves This
BlackBox separates **knowledge acquisition** from **task planning**:
* **Autonomous Active Exploration**: The agent navigates applications using deterministic information-gain heuristics, testing hypotheses about required fields, button states, and multi-step workflows.
* **Persistent Behavioral Graph**: Every observed screen is fingerprinted across multiple independent channels (DOM structure, accessibility tree, form state, and visual hashes). Every click or keystroke that produces an observable effect becomes a verified transition edge.
* **Outcome-Driven Constraint Learning**: When a submission fails, the agent inspects the validation message, extracts precise precondition rules (e.g., `min_length: 8`, `prefix: "SAVE"`), and synthesizes compliant inputs.
* **Mined Parameterized Workflows**: When a sequence leads to a confirmation message or database change, BlackBox extracts it into a parameterized procedure (e.g. `create_customer(name, email, revenue)`), lifting literal values into typed parameters.
* **Sub-Millisecond Deterministic Execution**: Tasks are resolved via graph search and workflow reuse, achieving **90%+ fewer wasted actions** and **50&times; faster planning latency** without needing an LLM in the loop.
* **Zero Trust & Safety by Design**: Enforces strict origin allowlists, blocks arbitrary network extraction, gates destructive actions (MEDIUM/HIGH/CRITICAL) behind approval policies, and never stores passwords or secrets.

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
