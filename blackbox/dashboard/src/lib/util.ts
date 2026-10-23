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
