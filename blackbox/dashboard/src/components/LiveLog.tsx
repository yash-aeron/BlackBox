import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { AgentEvent } from '../api/types';
import { EVENT_STREAM_LIMIT } from '../hooks/useEventStream';
import {
  classNames,
  eventKind,
  formatClock,
  logRecordToEvent,
  readString,
  summarizeEvent,
  toError,
} from '../lib/util';
import { ErrorState, Panel } from './Panel';

/** Rows actually mounted: the tail of the buffer ("virtualised-ish"). */
const RENDER_LIMIT = 400;
const HISTORY_LIMIT = 500;

export interface LiveLogProps {
  targetId: string;
  events: AgentEvent[];
  connected: boolean;
  paused: boolean;
  onPausedChange: (paused: boolean) => void;
  /** Events received while paused and still buffered. */
  bufferedCount: number;
  onClear: () => void;
}

const KNOWN_KINDS = [
  'new_state',
  'action_proposed',
  'action_result',
  'ranking',
  'action_blocked',
  'action_denied',
  'approval_requested',
  'approval_resolved',
  'constraint_learned',
  'hypothesis',
  'origin_blocked',
  'network_blocked',
  'exploration_finished',
  'job_started',
  'job_finished',
  'session_start',
  'stop_requested',
];

/** The live SSE feed: one line per event, pausable without dropping anything. */
export function LiveLog({
  targetId,
  events,
  connected,
  paused,
  onPausedChange,
  bufferedCount,
  onClear,
}: LiveLogProps): ReactElement {
  const [kindFilter, setKindFilter] = useState('ALL');
  const [backfill, setBackfill] = useState<AgentEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<unknown>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [stick, setStick] = useState(true);

  const listRef = useRef<HTMLDivElement | null>(null);

  const rows = useMemo(() => {
    const merged = [...backfill, ...events].slice(-EVENT_STREAM_LIMIT);
    return kindFilter === 'ALL' ? merged : merged.filter((event) => eventKind(event) === kindFilter);
  }, [backfill, events, kindFilter]);

  const visible = rows.length > RENDER_LIMIT ? rows.slice(rows.length - RENDER_LIMIT) : rows;

  const kinds = useMemo(() => {
    const seen = new Set<string>(KNOWN_KINDS);
    for (const event of events) seen.add(eventKind(event));
    return ['ALL', ...[...seen].sort()];
  }, [events]);

  useEffect(() => {
    const element = listRef.current;
    if (!element || !stick || paused) return;
    element.scrollTop = element.scrollHeight;
  }, [visible.length, stick, paused]);

  const handleScroll = (): void => {
    const element = listRef.current;
    if (!element) return;
    setStick(element.scrollHeight - element.scrollTop - element.clientHeight < 48);
  };

  const loadHistory = (): void => {
    setHistoryLoading(true);
    setHistoryError(null);
    api.logs(targetId, { limit: HISTORY_LIMIT, kind: kindFilter === 'ALL' ? undefined : kindFilter }).then(
      (records) => {
        setHistoryLoading(false);
        setBackfill(records.map(logRecordToEvent));
      },
      (cause: unknown) => {
        setHistoryLoading(false);
        setHistoryError(toError(cause));
      },
    );
  };

  return (
    <Panel
      title="Live log"
      subtitle={
        <>
          <span className={classNames('conn', connected ? 'conn-on' : 'conn-off')} role="status">
            {connected ? 'stream connected' : 'stream offline'}
          </span>
          <span className="mono muted">
            {events.length} streamed{paused && bufferedCount > 0 ? ` · ${bufferedCount} buffered` : ''}
            {rows.length !== events.length + backfill.length ? ` · ${rows.length} shown` : ''}
          </span>
        </>
      }
      actions={
        <>
          <label className="field-inline" htmlFor="log-kind">
            <span className="field-label">type</span>
            <select
              id="log-kind"
              className="input mono"
              value={kindFilter}
              onChange={(event) => setKindFilter(event.target.value)}
            >
              {kinds.map((kind) => (
                <option key={kind} value={kind}>
                  {kind.toLowerCase()}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={classNames('btn', 'btn-small', paused && 'btn-primary')}
            aria-pressed={paused}
            onClick={() => onPausedChange(!paused)}
          >
            {paused ? `resume${bufferedCount > 0 ? ` (${bufferedCount})` : ''}` : 'pause'}
          </button>
          <button type="button" className="btn btn-small" onClick={loadHistory} disabled={historyLoading}>
            {historyLoading ? 'loading…' : 'load history'}
          </button>
          <button type="button" className="btn btn-small" onClick={onClear}>
            clear
          </button>
        </>
      }
      className="panel-log"
    >
      {historyError ? <ErrorState error={historyError} onRetry={loadHistory} /> : null}
      {paused ? (
        <p className="muted" role="status">
          paused — incoming events are buffered and appended when you resume.
        </p>
      ) : null}
      {rows.length === 0 ? (
        <p className="state state-empty">
          no events yet — run an exploration or a task while this view is open.
        </p>
      ) : (
        <>
          {rows.length > RENDER_LIMIT ? (
            <p className="muted mono">
              showing the most recent {RENDER_LIMIT} of {rows.length} matching events
            </p>
          ) : null}
          <div className="log-list" ref={listRef} onScroll={handleScroll} role="log" aria-live="off">
            {visible.map((event, index) => {
              const summary = summarizeEvent(event);
              const kind = eventKind(event);
              const key = readString(event.event_id) ?? `${String(event.ts ?? '')}-${kind}-${index}`;
              const isOpen = expanded === key;
              return (
                <div className={classNames('log-row', `log-${summary.tone}`)} key={key}>
                  <button
                    type="button"
                    className="log-line"
                    aria-expanded={isOpen}
                    onClick={() => setExpanded(isOpen ? null : key)}
                  >
                    <span className="log-ts mono">{formatClock(event.ts)}</span>
                    <span className="log-kind mono" title={kind}>
                      {kind}
                    </span>
                    <span className="log-text">{summary.text}</span>
                  </button>
                  {isOpen ? <pre className="log-detail mono">{JSON.stringify(event, null, 2)}</pre> : null}
                </div>
              );
            })}
          </div>
        </>
      )}
      <p className="muted mono">
        tone legend: <span className="ok-text">ok</span> · <span className="warn-text">attention</span> ·{' '}
        <span className="err-text">blocked/failed</span> · capped at {EVENT_STREAM_LIMIT} events
      </p>
    </Panel>
  );
}
