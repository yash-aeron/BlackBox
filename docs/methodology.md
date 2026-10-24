# Methodology

How the measurements in the README were produced, and what could make them wrong.

## Research question

> Can an AI learn the behavioral structure of an unfamiliar website through
> black-box interaction, and does that learned model make future task execution
> more reliable and efficient than repeatedly reasoning from the current page?

The independent variable is the **presence of a learned behavioral model**. The
dependent variables are task success, actions per task, wasted actions, planning
latency, recoveries and model reuse.

## Test subjects

Four local applications written for this project (`blackbox/demo/`): a CRM, a
storefront, a project/task manager and a four-step wizard. They were built by a
separate process from the agent and, before measurement, audited for structure
(labels, accessible names, duplicate ids) and behaviour (unit-style checks of
every interaction). They are deliberately ordinary: hash routing, delayed
renders, disabled-until-valid submits, modal dialogs, conditional fields,
dependencies and validation messages.

**The agent contains no selectors, identifiers, labels or URLs from these
applications.** That claim is mechanically checkable: the only target-specific
strings in the whole agent are the demo `base_url`s in `agent/config.py`.

## Procedure

1. **Registration.** Each target is registered with an explicit origin allow-list
   and mode. Nothing is explored that was not registered.
2. **Exploration.** `python -m blackbox.agent.main explore <target> --max-actions N`.
   Fixed seed, fixed budgets, deterministic value synthesis; the only randomness
   in the system is the seeded RNG, which the deterministic path does not use.
3. **Model freeze.** The learned model is persisted (SQLite) and exported as JSON
   (`artifacts/models/`).
4. **Task execution.** Each task starts from a freshly loaded page so runs are
   independent, then plans against the frozen model. `--mode learned` forbids
   exploration entirely; `--mode learned_exploratory` allows bounded targeted
   exploration.
5. **Scoring.** A task counts as successful only if every declared predicate holds
   on a *settled* final observation taken after the last action (loading
   indicators are waited out). Predicates were validated against the running demos.
6. **Baseline.** `blackbox/benchmarks/baseline.py` reuses the identical element
   detector, action generator, safety classifier, locator resolver and executor,
   but keeps no model: it observes the page, scores candidate actions by keyword
   overlap with the task, acts, and repeats. This isolates the learned model as
   the only difference. When an LLM provider is configured, the baseline asks it
   to choose among the same pre-validated candidates.

## What was measured

| Metric | Definition |
| --- | --- |
| success rate | tasks whose predicates all held, excluding tasks the safety policy blocked |
| actions / task | browser actions executed, including any exploration performed for that task |
| wasted actions | steps whose observed effect was `NO_EFFECT` |
| planning latency | wall-clock time to produce a plan (model query only, no browser calls) |
| recoveries / replans | retries, alternate routes and targeted explorations triggered by failures |
| model reuse rate | tasks executed from a workflow or graph route with zero exploration |
| workflow reuse | tasks whose plan came from a learned workflow |
| state recognition accuracy | fraction of executions where the live page matched a known state |
| transition prediction accuracy | fraction of planned steps whose observed target state equalled the predicted one |
| step verification rate | fraction of steps whose predicted effect was observed |

## Threats to validity

1. **One implementer.** The demos, the agent and the benchmark were written in
   the same project. The demo audit and the fact that the agent contains no
   demo-specific strings mitigate, but do not remove, this.
2. **Coverage, not correctness, drives the low absolute success rate.** A learned
   model cannot plan through states it never reached. The measured plateau in the
   learning curve is a property of the exploration policy.
3. **The baseline is deterministic.** No LLM was reachable on the measurement
   machine, so the comparison is "learned model vs the same primitives without
   memory", not "BlackBox vs a frontier model". The baseline's LLM path exists
   and is exercised only when a provider is configured.
4. **Small task suites.** 52 tasks across four sites, 15 in the headline CRM
   comparison. Differences of one or two tasks move the success rate by ~7 points;
   the action-count and latency differences are far larger than that noise.
5. **Single machine, single browser version.** Chrome 153.0.8010.12, headless,
   1440×900, Python 3.12.10. Timing numbers are machine-specific.
6. **Threshold sensitivity.** Fingerprint thresholds (`same_state = 0.90`,
   `same_page = 0.68`) were chosen from inspection of the demo behaviour, not
   tuned against the task suite; they are exposed as configuration so this can be
   revisited.
7. **Safety policy affects scores.** Unattended runs refuse HIGH/CRITICAL actions,
   so the two deletion tasks can never succeed by design. They are reported as
   policy-blocked rather than as capability failures.

## Reproducing

```bash
python -m blackbox.demo.server &                     # 3001-3004
python -m blackbox.agent.main explore demo_crm --max-actions 140
python -m blackbox.benchmarks.runner --target demo_crm --mode compare
python -m blackbox.benchmarks.faults.inject --target demo_crm --task crm_create_customer
python -m pytest tests -q
```

Raw artefacts: `artifacts/benchmarks/*.json` (per-task reports with plans, step
verifications and predicate evidence), `artifacts/models/*.json` (exported models),
`artifacts/screenshots/` (the visual channel), `artifacts/blackbox.db` (the model
and its evidence).

## Reproducibility guarantees

* Every experiment records: target, configuration, seed, browser version, model
  version, prompt version, budget, timestamps and the learned model version.
* Element, state, transition, workflow and task ids are content-addressed, so the
  same observation produces the same ids in any run.
* Values typed into fields are synthesized deterministically per variant, so a
  repeated run performs the same actions in the same order.
* Nothing in the results is hand-edited; each table in the README names the
  command that produced it.
