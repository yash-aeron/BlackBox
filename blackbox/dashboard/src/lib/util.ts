/**
 * Small, dependency-free helpers shared by every view: value coercion for
 * loosely-typed server payloads, formatting, and the client-side one-line
 * event summaries used by the live log.
 */

import { ApiError } from '../api/client';
import type {
  AgentEvent,
  ElementInfo,
  LocatorCandidate,
  LogRecord,
  ObservedEffect,
  Timestamp,
} from '../api/types';

export type Tone = 'ok' | 'warn' | 'err' | 'info' | 'muted';

type Rec = Record<string, unknown>;

/* ------------------------------------------------------------------ basics */

export function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter((part): part is string => Boolean(part)).join(' ');
}

export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

export function toError(value: unknown): Error {
  if (value instanceof Error) return value;
  if (typeof value === 'string' && value) return new Error(value);
  try {
    return new Error(JSON.stringify(value));
  } catch {
    return new Error(String(value));
  }
}

/** `{ label: 'HTTP 404 Not Found', message: 'unknown target' }` for banners. */
export function describeError(error: unknown): { label: string; message: string } {
  if (error instanceof ApiError) {
    return { label: error.label, message: messageWithoutLabel(error) };
  }
  if (error instanceof Error) {
    return { label: 'error', message: error.message };
  }
  return { label: 'error', message: String(error) };
}

function messageWithoutLabel(error: ApiError): string {
  const message = error.message ?? '';
  const urlTag = ` (${error.url})`;
  return message.endsWith(urlTag) ? message.slice(0, -urlTag.length) : message;
}

/** Uppercase token for badge slugs and switch statements. */
export function normalizeToken(value: unknown): string {
  if (value === null || value === undefined) return 'UNKNOWN';
  const text = String(value).trim();
  return text ? text.toUpperCase() : 'UNKNOWN';
}

/* ------------------------------------------------------- value coercion */

export function readRecord(value: unknown): Rec {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Rec) : {};
}

export function readString(value: unknown): string | null {
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  }
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return null;
}

export function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function readBool(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const token = value.trim().toLowerCase();
    if (token === 'true' || token === 'yes' || token === '1') return true;
    if (token === 'false' || token === 'no' || token === '0') return false;
  }
  if (typeof value === 'number') return value !== 0;
  return null;
}

export function readTimestamp(value: unknown): Timestamp {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) return value;
  return null;
}

export function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => describeValue(item)).filter((item) => item.length > 0);
}

/** `count(x)` for stats objects: undefined-safe, never NaN. */
export function count(value: unknown): number {
  const num = readNumber(value);
  return num === null ? 0 : num;
}

export function describeValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(describeValue).filter(Boolean).join(', ');
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function joinList(value: unknown, separator = ', '): string {
  if (Array.isArray(value)) {
    const parts = value.map(describeValue).filter((part) => part.length > 0);
    return parts.join(separator);
  }
  return describeValue(value);
}

export function findLast<T>(
  items: readonly T[],
  predicate: (item: T) => boolean,
): T | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (predicate(item)) return item;
  }
  return null;
}

/* ----------------------------------------------------------- formatting */

export function truncate(text: string | null | undefined, max: number): string {
  const value = text ?? '';
  if (value.length <= max) return value;
  return `${value.slice(0, Math.max(0, max - 1))}…`;
}

function toDate(ts: Timestamp | undefined): Date | null {
  if (ts === null || ts === undefined || ts === '') return null;
  if (typeof ts === 'number') {
    if (!Number.isFinite(ts) || ts <= 0) return null;
    const ms = ts < 1e12 ? ts * 1000 : ts;
    const fromNumber = new Date(ms);
    return Number.isNaN(fromNumber.getTime()) ? null : fromNumber;
  }
  const fromString = new Date(ts);
  return Number.isNaN(fromString.getTime()) ? null : fromString;
}

/** Wall clock in local time, e.g. `14:07:52`. Falls back to the raw string. */
export function formatClock(ts: Timestamp | undefined): string {
  const date = toDate(ts);
  if (date) return date.toTimeString().slice(0, 8);
  return typeof ts === 'string' && ts ? ts : '—';
}

/** `2024-05-02 14:07:52`. */
export function formatDateTime(ts: Timestamp | undefined): string {
  const date = toDate(ts);
  if (date) return `${date.toISOString().slice(0, 10)} ${date.toTimeString().slice(0, 8)}`;
  return typeof ts === 'string' && ts ? ts : '—';
}

export function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return `${minutes}m ${String(rest).padStart(2, '0')}s`;
}

export function formatMs(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return '—';
  if (Math.abs(ms) >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
}

/** Confidence is normally 0..1; a value above 1 is read as a percentage. */
export function confidencePercent(value: number | null | undefined): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const normalized = value > 1 ? value / 100 : value;
  return Math.round(clamp(normalized, 0, 1) * 100);
}

export function formatPercent(value: number | null | undefined): string {
  const percent = confidencePercent(value);
  return percent === null ? '—' : `${percent}%`;
}

export function basename(path: string | null | undefined): string {
  if (!path) return '';
  const withoutQuery = path.split('?')[0].split('#')[0];
  const parts = withoutQuery.split('/');
  return parts[parts.length - 1] || withoutQuery;
}

export function statusTone(value: unknown): Tone {
  switch (normalizeToken(value)) {
    case 'VERIFIED':
    case 'SUCCEEDED':
    case 'COMPLETED':
    case 'APPROVED':
    case 'OK':
    case 'CURRENT':
    case 'ALIVE':
    case 'TRUE':
    case 'PASSED':
    case 'SATISFIED':
      return 'ok';
    case 'PROPOSED':
    case 'PENDING':
    case 'RUNNING':
    case 'QUEUED':
    case 'SUPPORTED':
    case 'PAUSED':
    case 'LOW':
      return 'warn';
    case 'REFUTED':
    case 'FAILED':
    case 'REJECTED':
    case 'BLOCKED':
    case 'ERROR':
    case 'CRASHED':
    case 'CANCELLED':
    case 'STOPPED':
    case 'CRITICAL':
    case 'HIGH':
      return 'err';
    default:
      return 'muted';
  }
}

export function isJobRunning(status: unknown): boolean {
  const token = normalizeToken(status);
  return token === 'RUNNING' || token === 'QUEUED' || token === 'PENDING' || token === 'STARTED';
}

/** `workflow reuse` / `graph route` / `exploration` / `none`. */
export function planSourceInfo(
  source: string | null | undefined,
  usedWorkflow?: string | null,
): { label: string; kind: 'workflow' | 'graph' | 'exploration' | 'none' | 'other' } {
  const token = (source ?? '').trim().toLowerCase();
  if (token.includes('workflow') || (usedWorkflow !== null && usedWorkflow !== undefined && usedWorkflow !== '')) {
    return { label: 'workflow reuse', kind: 'workflow' };
  }
  if (token.includes('graph') || token.includes('route')) return { label: 'graph route', kind: 'graph' };
  if (token.includes('explor')) return { label: 'exploration', kind: 'exploration' };
  if (!token || token === 'none' || token === 'empty') return { label: 'none', kind: 'none' };
  return { label: token, kind: 'other' };
}

/* --------------------------------------------------- domain descriptions */

export function elementRole(element: ElementInfo): string {
  return (
    readString(element.semantic_role) ??
    readString(element.role) ??
    readString(element.tag) ??
    'UNKNOWN'
  );
}

export function elementName(element: ElementInfo): string {
  return (
    readString(element.accessible_name) ??
    readString(element.name) ??
    readString(element.visible_text) ??
    ''
  );
}

/** Current value of a control, or null when it holds nothing observable. */
export function elementValue(element: ElementInfo): string | null {
  const valueState = readRecord(element.value_state);
  const value = readString(valueState.value) ?? readString(element.value);
  if (value) return value;
  const checked = readBool(valueState.checked) ?? readBool(element.checked);
  if (checked === true) return 'checked';
  if (checked === false) return 'unchecked';
  const selected = readString(valueState.selected);
  if (selected) return `selected: ${selected}`;
  return null;
}

export function elementFlag(value: unknown, fallback: boolean): boolean {
  const bool = readBool(value);
  return bool === null ? fallback : bool;
}

export function locatorText(locator: LocatorCandidate | string): string {
  if (typeof locator === 'string') return locator;
  const strategy = readString(locator.strategy) ?? '??';
  const selector = readString(locator.selector) ?? '';
  const confidence = readNumber(locator.confidence);
  const percent = confidence === null ? '' : ` ${formatPercent(confidence)}`;
  return `${strategy}(${selector})${percent}`;
}

export function effectText(effect: string | ObservedEffect): string {
  if (typeof effect === 'string') return effect;
  const kind = readString(effect.kind) ?? 'EFFECT';
  const detail = readString(effect.detail) ?? readString(effect.message);
  const change = [readString(effect.before), readString(effect.after)].every(Boolean)
    ? ` ${describeValue(effect.before)} → ${describeValue(effect.after)}`
    : '';
  return `${kind}${detail ? `: ${detail}` : ''}${change}`;
}

/** `CLICK Save customer` for both `Action` objects and plain strings. */
export function actionLabel(value: unknown): string {
  if (typeof value === 'string') return value.trim() || 'action';
  const action = readRecord(value);
  const type = (
    readString(action.type) ??
    readString(action.action_type) ??
    'ACTION'
  ).toUpperCase();
  const target = readRecord(action.target);
  const name =
    readString(target.name) ??
    readString(action.target) ??
    readString(action.description) ??
    readString(action.name);
  return name ? `${type} ${name}` : type;
}

export function actionRisk(value: unknown): string | null {
  const action = readRecord(value);
  return readString(action.risk);
}

export function actionType(value: unknown): string | null {
  const action = readRecord(value);
  return readString(action.type) ?? readString(action.action_type);
}

/* --------------------------------------------------------------- events */

export interface EventSummary {
  text: string;
  tone: Tone;
}

export function eventKind(event: AgentEvent): string {
  return (readString(event.kind) ?? readString(event.type) ?? 'event').toLowerCase();
}

/** Type-specific fields, preferring `payload` and falling back to a flat event. */
export function eventFields(event: AgentEvent): Rec {
  const payload = readRecord(event.payload);
  const flat: Rec = {};
  for (const [key, value] of Object.entries(event)) {
    if (key === 'payload' || key === 'summary' || key === 'kind' || key === 'target_id' || key === 'ts') {
      continue;
    }
    flat[key] = value;
  }
  return { ...flat, ...payload };
}

function shortId(value: string | null): string {
  if (!value) return '?';
  return value.length > 10 ? value.slice(0, 8) : value;
}

/**
 * One line per event, using the server's own `summary` when it sent one.
 * Mirrors `blackbox/api/schemas/models.py::summarize_event`.
 */
export function summarizeEvent(event: AgentEvent): EventSummary {
  const kind = eventKind(event);
  const data = eventFields(event);
  const serverSummary = readString(event.summary);

  const fallback: EventSummary = (() => {
    switch (kind) {
      case 'new_state': {
        const state = readRecord(data.state);
        const id = shortId(readString(state.state_id) ?? readString(data.state_id) ?? readString(state.id));
        const label = readString(state.label) ?? readString(state.title) ?? readString(state.url_key);
        const elements =
          readNumber(state.elements) ??
          (Array.isArray(state.visible_elements) ? state.visible_elements.length : null) ??
          readNumber(data.elements);
        const detail = [
          label ? truncate(label, 48) : null,
          elements !== null ? `${elements} element${elements === 1 ? '' : 's'}` : null,
        ]
          .filter(Boolean)
          .join(' · ');
        return { text: `new state ${id}${detail ? `: ${detail}` : ''}`, tone: 'info' };
      }
      case 'action_proposed': {
        const risk = normalizeToken(data.risk ?? actionRisk(data.action));
        const score = readNumber(data.score);
        return {
          text: `proposed ${actionLabel(data.action ?? data)} [${risk}${score !== null ? ` · score ${score.toFixed(2)}` : ''}]`,
          tone: 'info',
        };
      }
      case 'action_result': {
        const verified = readBool(data.verified);
        const transition = readRecord(data.transition);
        const status =
          readString(data.status) ??
          readString(transition.status) ??
          (verified === true ? 'OK' : verified === false ? 'FAILED' : null);
        const ms = readNumber(data.duration_ms);
        const tone: Tone =
          verified === true ? 'ok' : verified === false || normalizeToken(status) === 'FAILED' ? 'err' : 'muted';
        return {
          text: `${actionLabel(data.action ?? data)} → ${(status ?? 'done').toLowerCase()}${
            ms !== null ? ` (${Math.round(ms)}ms)` : ''
          }`,
          tone,
        };
      }
      case 'ranking': {
        const top = Array.isArray(data.top) ? data.top : [];
        const names = top
          .slice(0, 3)
          .map((item) => readString(readRecord(item).action) ?? describeValue(item))
          .filter(Boolean)
          .join(' · ');
        const from = readString(data.state_id);
        return {
          text: `ranked ${top.length || count(data.count)} action(s)${from ? ` from ${from}` : ''}${
            names ? `: ${truncate(names, 70)}` : ''
          }`,
          tone: 'muted',
        };
      }
      case 'action_blocked':
      case 'action_denied': {
        const reason = readString(data.reason);
        return {
          text: `${kind.replace('_', ' ')}: ${actionLabel(data.action ?? data)}${
            reason ? ` — ${truncate(reason, 70)}` : ''
          }`,
          tone: 'err',
        };
      }
      case 'approval_requested': {
        const risk = normalizeToken(data.risk ?? actionRisk(data.action));
        const requestId = readString(data.request_id);
        return {
          text: `approval needed: ${actionLabel(data.action ?? data)} [${risk}]${
            requestId ? ` · ${requestId}` : ''
          }`,
          tone: 'warn',
        };
      }
      case 'approval_resolved':
        return {
          text: `approval ${readString(data.request_id) ?? '?'} → ${normalizeToken(data.decision)}`,
          tone: statusTone(data.decision),
        };
      case 'constraint_learned': {
        const constraint = readRecord(data.constraint);
        const expression = readString(constraint.expression) ?? describeValue(constraint);
        return {
          text: `constraint ${readString(constraint.kind) ?? ''} ${truncate(expression, 70)}`.trim(),
          tone: 'info',
        };
      }
      case 'hypothesis': {
        const hypothesis = readRecord(data.hypothesis);
        const status = normalizeToken(data.status ?? hypothesis.status);
        const statement = readString(hypothesis.statement) ?? describeValue(hypothesis);
        return { text: `hypothesis [${status}] ${truncate(statement, 80)}`, tone: 'info' };
      }
      case 'origin_blocked':
      case 'network_blocked':
      case 'origin_blocked_request':
        return {
          text: `${kind.replace(/_/g, ' ')}: ${truncate(
            readString(data.reason) ?? readString(data.url) ?? describeValue(data),
            80,
          )}`,
          tone: 'err',
        };
      case 'exploration_finished': {
        const stop = readString(data.stop_reason);
        const actions = readNumber(data.actions_executed);
        const states = readNumber(data.new_states);
        return {
          text: `exploration finished${stop ? `: ${truncate(stop, 50)}` : ''}${
            actions !== null || states !== null
              ? ` (${actions ?? '?'} actions, ${states ?? '?'} new states)`
              : ''
          }`,
          tone: 'ok',
        };
      }
      case 'job_started':
        return {
          text: `job ${readString(data.job_id) ?? '?'} started (${readString(data.job_kind) ?? 'job'})`,
          tone: 'info',
        };
      case 'job_finished':
        return {
          text: `job ${readString(data.job_id) ?? '?'} ${normalizeToken(data.status)} in ${describeValue(
            data.duration_seconds,
          )}s`,
          tone: statusTone(data.status),
        };
      case 'session_start':
        return {
          text: `session started: browser ${readString(data.browser_version) ?? '?'}, model v${
            readString(data.model_version) ?? '?'
          }, ${describeValue(data.states_loaded)} states loaded`,
          tone: 'info',
        };
      case 'stop_requested':
        return { text: 'stop requested', tone: 'warn' };
      default: {
        const keys = Object.keys(data).slice(0, 4);
        return {
          text: keys.length ? `${kind}: ${keys.join(', ')}` : kind,
          tone: 'muted',
        };
      }
    }
  })();

  if (serverSummary) return { text: serverSummary, tone: fallback.tone };
  return fallback;
}

/** Convert one `/logs` row into the same shape as a streamed event. */
export function logRecordToEvent(record: LogRecord): AgentEvent {
  const payload = readRecord(record.payload);
  const kind = readString(record.kind) ?? readString(payload.type) ?? 'event';
  return {
    ...payload,
    type: kind,
    kind,
    ts: record.created_at ?? readTimestamp(payload.ts),
    target_id: record.target_id ?? null,
    event_id: record.event_id,
    summary: readString(payload.summary),
    payload,
  };
}
