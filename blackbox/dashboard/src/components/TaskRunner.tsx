import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactElement } from 'react';
import { api } from '../api/client';
import type { BenchmarkTask, JobStatus, PredicateSpec, TaskResult } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import {
  describeValue,
  formatDateTime,
  formatDuration,
  formatMs,
  isJobRunning,
  joinList,
  toError,
  truncate,
} from '../lib/util';
import { EmptyState, ErrorState, Loading, Panel, ResourceView } from './Panel';
import { PlanView } from './PlanView';
import { StepResults } from './StepResults';
import { StatusBadge } from './StatusBadge';

export interface TaskRunnerProps {
  targetId: string;
}

interface PredicateDraft {
  id: number;
  kind: string;
  value: string;
  target: string;
}

const POLL_INTERVAL_MS = 1000;

/**
 * Run a free-text goal against the learned model, poll the job, then show the
 * plan it used and what actually happened step by step.
 */
export function TaskRunner({ targetId }: TaskRunnerProps): ReactElement {
  const [text, setText] = useState('');
  const [predicates, setPredicates] = useState<PredicateDraft[]>([]);
  const [modelOnly, setModelOnly] = useState(false);
  const [useWorkflows, setUseWorkflows] = useState(true);
  const [useTransitions, setUseTransitions] = useState(true);
  const [maxExplorationActions, setMaxExplorationActions] = useState('20');
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [jobError, setJobError] = useState<unknown>(null);
  const [finishedResult, setFinishedResult] = useState<TaskResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const nextIdRef = useRef(1);
  const busyRef = useRef(false);

  const presets = useApiData<BenchmarkTask[]>(() => api.availableTasks(targetId), [targetId]);

  const running = job !== null && isJobRunning(job.status);

  // Poll the submitted job every second until it reaches a terminal status.
  useEffect(() => {
    if (!jobId) return undefined;
    let cancelled = false;
    let timer: number | null = null;

    const tick = (): void => {
      if (busyRef.current) return;
      busyRef.current = true;
      api.job(targetId, jobId).then(
        (next) => {
          busyRef.current = false;
          if (cancelled) return;
          setJob(next);
          setJobError(null);
          if (!isJobRunning(next.status) && timer !== null) {
            window.clearInterval(timer);
            timer = null;
          }
        },
        (cause: unknown) => {
          busyRef.current = false;
          if (cancelled) return;
          setJobError(toError(cause));
        },
      );
    };

    tick();
    timer = window.setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [targetId, jobId]);

  // Keep the last outcome on screen while a new job runs.
  useEffect(() => {
    if (job && !isJobRunning(job.status) && job.result) setFinishedResult(job.result);
  }, [job]);

  const addPredicate = (): void => {
    nextIdRef.current += 1;
    setPredicates((previous) => [
      ...previous,
      { id: nextIdRef.current, kind: '', value: '', target: '' },
    ]);
  };

  const updatePredicate = (id: number, patch: Partial<PredicateDraft>): void => {
    setPredicates((previous) =>
      previous.map((predicate) => (predicate.id === id ? { ...predicate, ...patch } : predicate)),
    );
  };

  const removePredicate = (id: number): void => {
    setPredicates((previous) => previous.filter((predicate) => predicate.id !== id));
  };

  const applyPreset = (task: BenchmarkTask): void => {
    setText(task.text);
    nextIdRef.current += 1;
    const drafts: PredicateDraft[] = (task.predicates ?? []).map((predicate, index) => ({
      id: nextIdRef.current * 100 + index,
      kind: predicate.kind,
      value: describeValue(predicate.value),
      target: describeValue(predicate.target),
    }));
    setPredicates(drafts);
    setNotice(
      `loaded benchmark task ${task.task_id} (${task.difficulty})${
        task.expect_blocked ? ' · expected to be blocked' : ''
      }`,
    );
  };

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const goal = text.trim();
    if (!goal) {
      setSubmitError(new Error('describe the goal first'));
      return;
    }
    const spec: PredicateSpec[] = predicates
      .filter((predicate) => predicate.kind.trim().length > 0)
      .map((predicate) => ({
        kind: predicate.kind.trim(),
        value: predicate.value,
        target: predicate.target,
      }));
    const parsedMax = Number(maxExplorationActions);
    setSubmitting(true);
    setSubmitError(null);
    setNotice(null);
    setJob(null);
    setJobId(null);
    api
      .submitTask(targetId, {
        text: goal,
        predicates: spec,
        allow_exploration: !modelOnly,
        use_workflows: useWorkflows,
        use_transitions: useTransitions,
        max_exploration_actions: Number.isFinite(parsedMax) && parsedMax >= 0 ? parsedMax : 20,
      })
      .then(
        (handle) => {
          setSubmitting(false);
          setJobId(handle.job_id);
          setNotice(`queued job ${handle.job_id}`);
        },
        (cause: unknown) => {
          setSubmitting(false);
          setSubmitError(toError(cause));
        },
      );
  };

  const reset = (): void => {
    setJob(null);
    setJobId(null);
    setJobError(null);
    setFinishedResult(null);
    setNotice(null);
    setSubmitError(null);
  };

  const result: TaskResult | null =
    (job && !isJobRunning(job.status) ? job.result : null) ?? finishedResult;

  const metrics: Array<{ label: string; value: string | number }> = result
    ? [
        { label: 'actions executed', value: result.actions_executed },
        { label: 'steps verified', value: `${result.steps_verified}/${result.steps_total}` },
        { label: 'planning latency', value: formatMs(result.planning_latency_ms) },
        { label: 'recoveries', value: `${result.recoveries} recovery(+${result.replans} replan)` },
        {
          label: 'model reuse',
          value: result.used_workflow
            ? `workflow ${truncate(result.used_workflow, 14)}`
            : result.used_exploration
              ? 'exploration'
              : 'graph route',
        },
        {
          label: 'prediction hits',
          value: `${result.transition_predictions_hit} hit / ${result.transition_predictions_missed} missed`,
        },
        { label: 'unnecessary actions', value: result.unnecessary_actions },
        { label: 'duration', value: formatDuration(result.duration_seconds) },
        { label: 'current state matched', value: result.current_state_matched ? 'yes' : 'no' },
        {
          label: 'model states',
          value: `${result.model_states_before ?? '?'} → ${result.model_states_after ?? '?'}`,
        },
      ]
    : [];

  return (
    <div className="stack">
      <Panel
        title="Run a task"
        subtitle="a natural-language goal executed against the learned model"
        actions={
          <button type="button" className="btn btn-small" onClick={reset} disabled={submitting}>
            clear result
          </button>
        }
      >
        <form className="task-form" onSubmit={submit}>
          <label className="field" htmlFor="task-text">
            <span className="field-label">goal</span>
            <textarea
              id="task-text"
              className="input"
              rows={2}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Create a customer named Yash with email yash@example.com"
            />
          </label>

          <fieldset className="field field-predicates">
            <legend className="field-label">predicates — machine-checkable success conditions</legend>
            {predicates.length === 0 ? (
              <p className="muted">no predicates: the task will be judged only on step verification</p>
            ) : (
              <ul className="pred-list">
                {predicates.map((predicate) => (
                  <li key={predicate.id} className="pred-row">
                    <label className="field-inline">
                      <span className="field-label">kind</span>
                      <input
                        className="input mono"
                        value={predicate.kind}
                        placeholder="TEXT_VISIBLE"
                        onChange={(event) => updatePredicate(predicate.id, { kind: event.target.value })}
                      />
                    </label>
                    <label className="field-inline">
                      <span className="field-label">value</span>
                      <input
                        className="input mono"
                        value={predicate.value}
                        onChange={(event) => updatePredicate(predicate.id, { value: event.target.value })}
                      />
                    </label>
                    <label className="field-inline">
                      <span className="field-label">target</span>
                      <input
                        className="input mono"
                        value={predicate.target}
                        onChange={(event) => updatePredicate(predicate.id, { target: event.target.value })}
                      />
                    </label>
                    <button
                      type="button"
                      className="btn btn-small btn-danger"
                      onClick={() => removePredicate(predicate.id)}
                      aria-label="remove predicate"
                    >
                      remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <button type="button" className="btn btn-small" onClick={addPredicate}>
              add predicate
            </button>
          </fieldset>

          <div className="task-options">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={modelOnly}
                onChange={(event) => setModelOnly(event.target.checked)}
              />
              <span>use learned model only (no exploration)</span>
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={useWorkflows}
                onChange={(event) => setUseWorkflows(event.target.checked)}
              />
              <span>prefer mined workflows</span>
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={useTransitions}
                onChange={(event) => setUseTransitions(event.target.checked)}
              />
              <span>use learned transitions</span>
            </label>
            <label className="field-inline" htmlFor="task-max-explore">
              <span className="field-label">max exploration actions</span>
              <input
                id="task-max-explore"
                className="input mono"
                type="number"
                min={0}
                value={maxExplorationActions}
                disabled={modelOnly}
                onChange={(event) => setMaxExplorationActions(event.target.value)}
              />
            </label>
          </div>

          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting || running}>
              {running ? 'running…' : submitting ? 'submitting…' : 'run task'}
            </button>
            {notice ? <span className="notice-text">{notice}</span> : null}
          </div>
          {submitError ? <ErrorState error={submitError} /> : null}
        </form>
      </Panel>

      <Panel title="Benchmark tasks" subtitle="one-click presets from /tasks/available">
        <ResourceView
          resource={presets}
          isEmpty={(rows) => rows.length === 0}
          emptyMessage="no benchmark tasks registered for this target"
          emptyHint="the benchmark suite ships with the repo; register a target that matches one of its tasks."
        >
          {(rows) => (
            <ul className="rows rows-tight">
              {rows.map((task) => (
                <li key={task.task_id}>
                  <button type="button" className="row-btn" onClick={() => applyPreset(task)}>
                    <span className="row-title">{task.text}</span>
                    <span className="row-meta mono">
                      {task.task_id} · {task.difficulty} · {task.target}
                      {task.tags && task.tags.length > 0 ? ` · ${joinList(task.tags)}` : ''}
                      {task.expect_blocked ? ' · expects blocked' : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </ResourceView>
      </Panel>

      <Panel
        title="Job"
        subtitle={jobId ? jobId : 'no job submitted'}
        actions={
          job ? (
            <>
              <StatusBadge value={job.status} />
              <span className="muted mono">
                {formatDateTime(job.started_at)} → {job.finished_at ? formatDateTime(job.finished_at) : '…'}
              </span>
            </>
          ) : null
        }
      >
        {!jobId ? (
          <EmptyState
            message="submit a task to see the plan it used and the per-step results"
            hint="the plan shows whether a mined workflow was reused, a graph route was found, or exploration was needed."
          />
        ) : null}

        {jobError ? <ErrorState error={jobError} /> : null}
        {jobId && !job && !jobError ? <Loading label="waiting for the first job poll…" /> : null}

        {job && isJobRunning(job.status) ? (
          <p className="state state-loading" role="status" aria-live="polite">
            job {job.job_id} is {job.status} — polling every {POLL_INTERVAL_MS / 1000}s
          </p>
        ) : null}

        {job && !isJobRunning(job.status) && job.status === 'failed' ? (
          <ErrorState error={new Error(job.error ?? 'the job failed without an error message')} />
        ) : null}

        {result ? (
          <>
            <p className="badge-row">
              <StatusBadge value={result.success ? 'SUCCESS' : 'FAILED'} />
              <StatusBadge value={result.plan_source} kind="kind" />
              {result.used_workflow ? (
                <StatusBadge value="reused workflow" kind="kind" />
              ) : (
                <StatusBadge value={result.used_exploration ? 'explored' : 'model only'} kind="kind" />
              )}
              <span className="muted mono">{result.task?.goal ?? ''}</span>
            </p>

            <div className="metric-grid">
              {metrics.map((metric) => (
                <div className="metric" key={metric.label}>
                  <span className="metric-value mono">{metric.value}</span>
                  <span className="metric-label">{metric.label}</span>
                </div>
              ))}
            </div>

            <PlanView
              plan={result.plan}
              planSource={result.plan_source}
              usedWorkflow={result.used_workflow}
              notes={result.notes}
            />

            <h3 className="side-sub">step results ({result.step_results?.length ?? 0})</h3>
            <StepResults stepResults={result.step_results ?? []} verification={result.verification} />
          </>
        ) : null}
      </Panel>
    </div>
  );
}
