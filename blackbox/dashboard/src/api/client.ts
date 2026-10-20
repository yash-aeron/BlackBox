/**
 * Typed fetch client for the BlackBox API.
 *
 * Every request goes through `request()` so error handling is uniform: a
 * non-2xx response becomes an `ApiError` carrying the HTTP status, the request
 * URL and the response body, which the UI surfaces as `HTTP 404 Not Found`.
 */

import type {
  AgentStatus,
  ApprovalDecision,
  ApprovalDecisionResponse,
  ApprovalInbox,
  BenchmarkTask,
  EvidenceRecord,
  Experiment,
  ExploreRequest,
  GraphResponse,
  HealthResponse,
  Hypothesis,
  JobHandle,
  JobStatus,
  LogRecord,
  MetricsResponse,
  RegisterTargetRequest,
  StateDetail,
  StateSummary,
  TargetSummary,
  TaskRequest,
  TransitionDetail,
  TransitionSummary,
  Constraint,
  WorkflowDetail,
  WorkflowSummary,
} from './types';

/** Single source of the API origin. Empty string means "same origin" (dev proxy). */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '';

export type QueryParams = Record<string, string | number | boolean | null | undefined>;

export class ApiError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly url: string;
  readonly body: string;

  constructor(
    message: string,
    init: { status: number; statusText?: string; url: string; body?: string },
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = init.status;
    this.statusText = init.statusText ?? '';
    this.url = init.url;
    this.body = init.body ?? '';
  }

  /** `HTTP 404 Not Found`, or `network error` when the request never landed. */
  get label(): string {
    if (this.status <= 0) return 'network error';
    return `HTTP ${this.status}${this.statusText ? ` ${this.statusText}` : ''}`;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  params?: QueryParams;
  signal?: AbortSignal;
}

function buildUrl(path: string, params?: QueryParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return `${API_BASE}/api${path}${query ? `?${query}` : ''}`;
}

/** FastAPI puts the human message in `detail`; fall back to `message`/`error`. */
function errorMessage(status: number, statusText: string, body: string): string {
  const trimmed = body.trim();
  if (trimmed) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (parsed && typeof parsed === 'object') {
        const record = parsed as Record<string, unknown>;
        const detail = record.detail ?? record.message ?? record.error;
        if (typeof detail === 'string' && detail.trim()) return detail.trim();
        if (detail !== undefined) return JSON.stringify(detail);
      }
    } catch {
      // not JSON: the raw body is more useful than nothing
    }
    if (trimmed.length <= 400) return trimmed;
  }
  return statusText || `request failed with status ${status}`;
}

function describeCause(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  return String(cause);
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = buildUrl(path, options.params);
  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? 'GET',
      headers: options.body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (cause) {
    throw new ApiError(`could not reach ${url}: ${describeCause(cause)}`, {
      status: 0,
      statusText: 'NETWORK',
      url,
      body: '',
    });
  }

  const text = await response.text();
  if (!response.ok) {
    throw new ApiError(errorMessage(response.status, response.statusText, text), {
      status: response.status,
      statusText: response.statusText,
      url,
      body: text,
    });
  }
  if (!text.trim()) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch (cause) {
    throw new ApiError(`malformed JSON from ${url}: ${describeCause(cause)}`, {
      status: response.status,
      statusText: 'BAD JSON',
      url,
      body: text,
    });
  }
}

function enc(segment: string): string {
  return encodeURIComponent(segment);
}

/** `screenshots/abc.png?t=1` -> `abc.png` so the URL can be rebuilt safely. */
export function screenshotFilename(ref: string): string {
  const withoutQuery = ref.split('?')[0].split('#')[0];
  const parts = withoutQuery.split('/');
  return parts[parts.length - 1] || withoutQuery;
}

/**
 * Every endpoint the dashboard consumes. Grouped by resource so the API-call
 * list per view is easy to audit.
 */
export const api = {
  /* ---------------------------------------------------------------- health */
  health: (signal?: AbortSignal) => request<HealthResponse>('/health', { signal }),

  /* --------------------------------------------------------------- targets */
  listTargets: (signal?: AbortSignal) => request<TargetSummary[]>('/targets', { signal }),
  registerTarget: (body: RegisterTargetRequest) =>
    request<TargetSummary>('/targets', { method: 'POST', body }),
  targetStatus: (targetId: string, signal?: AbortSignal) =>
    request<AgentStatus>(`/targets/${enc(targetId)}/status`, { signal }),

  /* ----------------------------------------------------------------- graph */
  graph: (targetId: string, signal?: AbortSignal) =>
    request<GraphResponse>(`/targets/${enc(targetId)}/graph`, { signal }),

  /* ---------------------------------------------------------------- states */
  states: (targetId: string, limit?: number, signal?: AbortSignal) =>
    request<StateSummary[]>(`/targets/${enc(targetId)}/states`, { params: { limit }, signal }),
  state: (targetId: string, stateId: string, signal?: AbortSignal) =>
    request<StateDetail>(`/targets/${enc(targetId)}/states/${enc(stateId)}`, { signal }),

  /* ----------------------------------------------------------- transitions */
  transitions: (targetId: string, signal?: AbortSignal) =>
    request<TransitionSummary[]>(`/targets/${enc(targetId)}/transitions`, { signal }),
  transition: (targetId: string, transitionId: string, signal?: AbortSignal) =>
    request<TransitionDetail>(`/targets/${enc(targetId)}/transitions/${enc(transitionId)}`, {
      signal,
    }),

  /* ------------------------------------------------------------- workflows */
  workflows: (targetId: string, signal?: AbortSignal) =>
    request<WorkflowSummary[]>(`/targets/${enc(targetId)}/workflows`, { signal }),
  workflow: (targetId: string, workflowId: string, signal?: AbortSignal) =>
    request<WorkflowDetail>(`/targets/${enc(targetId)}/workflows/${enc(workflowId)}`, { signal }),
  mineWorkflows: (targetId: string) =>
    request<Record<string, unknown>>(`/targets/${enc(targetId)}/workflows/mine`, {
      method: 'POST',
    }),

  /* ------------------------------------------------- hypotheses/constraints */
  hypotheses: (targetId: string, signal?: AbortSignal) =>
    request<Hypothesis[]>(`/targets/${enc(targetId)}/hypotheses`, { signal }),
  constraints: (targetId: string, signal?: AbortSignal) =>
    request<Constraint[]>(`/targets/${enc(targetId)}/constraints`, { signal }),

  /* ------------------------------------------------------------ experiments */
  experiments: (targetId: string, signal?: AbortSignal) =>
    request<Experiment[]>(`/targets/${enc(targetId)}/experiments`, { signal }),

  /* --------------------------------------------------------------- evidence */
  evidence: (
    targetId: string,
    params?: { transition_id?: string | null; limit?: number | null },
    signal?: AbortSignal,
  ) =>
    request<EvidenceRecord[]>(`/targets/${enc(targetId)}/evidence`, {
      params: { transition_id: params?.transition_id, limit: params?.limit },
      signal,
    }),
  evidenceRecord: (targetId: string, evidenceId: string, signal?: AbortSignal) =>
    request<EvidenceRecord>(`/targets/${enc(targetId)}/evidence/${enc(evidenceId)}`, { signal }),

  /* ---------------------------------------------------------------- metrics */
  metrics: (targetId: string, signal?: AbortSignal) =>
    request<MetricsResponse>(`/targets/${enc(targetId)}/metrics`, { signal }),
  logs: (targetId: string, params?: { limit?: number; kind?: string }, signal?: AbortSignal) =>
    request<LogRecord[]>(`/targets/${enc(targetId)}/logs`, { params, signal }),

  /* -------------------------------------------------------------- approvals */
  approvals: (targetId: string, signal?: AbortSignal) =>
    request<ApprovalInbox>(`/targets/${enc(targetId)}/approvals`, { signal }),
  decide: (targetId: string, requestId: string, decision: ApprovalDecision) =>
    request<ApprovalDecisionResponse>(
      `/targets/${enc(targetId)}/approvals/${enc(requestId)}`,
      { method: 'POST', body: { decision } },
    ),

  /* ------------------------------------------------------------------- jobs */
  explore: (targetId: string, body: ExploreRequest) =>
    request<JobHandle>(`/targets/${enc(targetId)}/explore`, { method: 'POST', body }),
  jobs: (targetId: string, signal?: AbortSignal) =>
    request<JobStatus[]>(`/targets/${enc(targetId)}/jobs`, { signal }),
  job: (targetId: string, jobId: string, signal?: AbortSignal) =>
    request<JobStatus>(`/targets/${enc(targetId)}/jobs/${enc(jobId)}`, { signal }),

  /* ------------------------------------------------------------------ tasks */
  submitTask: (targetId: string, body: TaskRequest) =>
    request<JobHandle>(`/targets/${enc(targetId)}/tasks`, { method: 'POST', body }),
  availableTasks: (targetId: string, signal?: AbortSignal) =>
    request<BenchmarkTask[]>(`/targets/${enc(targetId)}/tasks/available`, { signal }),

  /* -------------------------------------------------------------- model I/O */
  exportModel: (targetId: string) =>
    request<{ path: string }>(`/targets/${enc(targetId)}/model/export`, { method: 'POST' }),
  importModel: (targetId: string, path: string) =>
    request<Record<string, unknown>>(`/targets/${enc(targetId)}/model/import`, {
      method: 'POST',
      body: { path },
    }),

  /* ------------------------------------------------------------ screenshots */
  screenshotUrl: (targetId: string, ref: string) =>
    `${API_BASE}/api/targets/${enc(targetId)}/screenshots/${enc(screenshotFilename(ref))}`,

  /* --------------------------------------------------------------- SSE URL */
  streamUrl: (targetId: string) => `${API_BASE}/api/targets/${enc(targetId)}/stream`,
};

/** Convenience alias so callers can `import { streamUrl }`. */
export const streamUrl = api.streamUrl;
