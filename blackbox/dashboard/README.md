# BlackBox research dashboard (React + TypeScript)

A dense, technical inspector for the learned behavioral model: the state graph,
the current state, learned workflows, hypotheses and constraints, experiments,
the task runner, live logs, metrics and the approval queue.

## Status in this environment

This source is a complete Vite + React + TypeScript application, but **this
machine has no network access**, so `npm install` (and therefore `vite build` /
`tsc`) cannot run here, and none of it has been executed. It is written against
the API contract below and reviewed for consistency (imports, props, types).

The dashboard that *is* running and verified is the dependency-free equivalent
served by the API at `/` (`blackbox/api/static/`), which consumes the same
endpoints. Screenshots of it are in the top-level README.

## Running it (where npm is available)

```bash
cd blackbox/dashboard
npm install
npm run dev          # http://127.0.0.1:5173, proxies /api -> 127.0.0.1:8099
npm run build        # production bundle in dist/
npm run typecheck    # tsc --noEmit
```

Start the API first:

```bash
python -m uvicorn blackbox.api.main:app --port 8099
```

Set `VITE_API_BASE` if the API is not proxied.

## Views

| View | Components | What it shows |
| --- | --- | --- |
| Overview | `AgentStatusPanel`, `MetricsPanel`, `TargetPicker` | target identity, model counts, browser/sandbox state, policy |
| State Graph | `GraphView`, `GraphNodePanel`, `GraphEdgePanel` | BFS-layered SVG graph; click a node for URL, screenshot and elements; click an edge for action, expected vs observed effects and evidence |
| Current State | `CurrentStatePanel`, `ScreenshotView` | what the agent is looking at now, from the live event stream |
| Workflows | `WorkflowList`, `WorkflowDetail` | learned procedures with their parameters and ordered steps |
| Hypotheses & Constraints | `HypothesisTable`, `ConstraintTable`, `EvidenceList` | what is believed, how strongly, and the evidence ids behind it |
| Experiments | `ExperimentTable` | reproducible experiment records with configuration and metrics |
| Tasks | `TaskRunner`, `PlanView`, `StepResults` | run a goal with a predicate builder, then inspect the plan source, per-step verification and predicate outcomes |
| Logs | `LiveLog` | SSE event stream with pause/filter, plus the persisted log |
| Metrics | `MetricsPanel` | counts, observation timing, sandbox blocks, network-guard counters |
| Approvals | `ApprovalQueue` | pending risky actions with approve/reject |

## API surface used

`/health`, `/targets`, `/targets/{id}/status|graph|states|transitions|workflows|hypotheses|constraints|experiments|evidence|metrics|logs|stream|approvals|screenshots/{file}`, `POST /targets/{id}/explore|tasks|approvals/{request_id}|workflows/mine|model/export|model/import`, `GET /targets/{id}/jobs/{job_id}`.

Types for every response live in `src/api/types.ts`; the fetch client is
`src/api/client.ts`; the SSE hook is `src/hooks/useEventStream.ts`.

## Design intent

The important visual artifact is the learned behavioral graph, so the graph is
the centrepiece: verified edges in green, proposed in amber, refuted in red,
stale in grey, stroke width by confidence, node fill by state status, pan/zoom,
"fit to view", and filters. Everything else is deliberately instrument-like —
monospace identifiers, 1px borders, restrained semantic colour, no decorative
gradients or marketing layout.
