import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { AgentEvent } from '../api/types';
import { readString } from '../lib/util';

/** Hard cap on buffered events: the oldest are dropped first. */
export const EVENT_STREAM_LIMIT = 2000;

/**
 * Named SSE events the backend emits. The default (unnamed) `message` channel is
 * handled as well, so a server that sends `data: {...}` without an `event:`
 * line still works.
 */
export const AGENT_EVENT_TYPES: readonly string[] = [
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

const MAX_RETRY_DELAY_MS = 15000;

function parseEvent(raw: string, fallbackType?: string): AgentEvent | null {
  if (!raw || !raw.trim()) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { type: fallbackType ?? 'event', ts: Date.now(), payload: { value: parsed } };
  }
  const record = parsed as Record<string, unknown>;
  const type = readString(record.type) ?? readString(record.kind) ?? fallbackType ?? 'event';
  const rawTs = record.ts;
  const ts =
    typeof rawTs === 'number' || typeof rawTs === 'string'
      ? rawTs
      : typeof record.created_at === 'number' || typeof record.created_at === 'string'
        ? record.created_at
        : Date.now();
  return { ...record, type, ts };
}

function appendCapped(
  events: AgentEvent[],
  incoming: AgentEvent[],
  limit: number,
): AgentEvent[] {
  if (incoming.length === 0) return events;
  const merged = events.concat(incoming);
  return merged.length > limit ? merged.slice(merged.length - limit) : merged;
}

export interface EventStream {
  events: AgentEvent[];
  connected: boolean;
  paused: boolean;
  setPaused: (paused: boolean) => void;
  /** Discard everything buffered and received so far. */
  clear: () => void;
  /** Events received while paused and not yet flushed into the list. */
  bufferedCount: number;
  /** Set when EventSource is unavailable in this browser. */
  error: string | null;
}

/**
 * Server-sent events for one target, with manual reconnect (exponential backoff
 * capped at 15s) and a pause mode that *buffers* rather than drops.
 */
export function useEventStream(
  targetId: string | null,
  limit: number = EVENT_STREAM_LIMIT,
): EventStream {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [paused, setPausedState] = useState(false);
  const [bufferedCount, setBufferedCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const pausedRef = useRef(false);
  const bufferRef = useRef<AgentEvent[]>([]);
  const sourceRef = useRef<EventSource | null>(null);
  const retryRef = useRef<number | null>(null);
  const attemptRef = useRef(0);

  const push = useCallback(
    (event: AgentEvent) => {
      if (pausedRef.current) {
        const buffer = bufferRef.current;
        buffer.push(event);
        if (buffer.length > limit) buffer.splice(0, buffer.length - limit);
        setBufferedCount(buffer.length);
        return;
      }
      setEvents((previous) => appendCapped(previous, [event], limit));
    },
    [limit],
  );

  const setPaused = useCallback(
    (next: boolean) => {
      pausedRef.current = next;
      setPausedState(next);
      if (next) return;
      const buffered = bufferRef.current;
      bufferRef.current = [];
      setBufferedCount(0);
      if (buffered.length > 0) {
        setEvents((previous) => appendCapped(previous, buffered, limit));
      }
    },
    [limit],
  );

  const clear = useCallback(() => {
    bufferRef.current = [];
    setBufferedCount(0);
    setEvents([]);
  }, []);

  useEffect(() => {
    if (!targetId) {
      bufferRef.current = [];
      setBufferedCount(0);
      setEvents([]);
      setConnected(false);
      return;
    }
    if (typeof EventSource === 'undefined') {
      setError('this browser has no EventSource: the live stream is unavailable');
      setConnected(false);
      return;
    }

    let disposed = false;
    attemptRef.current = 0;
    bufferRef.current = [];
    setBufferedCount(0);
    setEvents([]);
    setError(null);

    const open = () => {
      if (disposed) return;
      const url = api.streamUrl(targetId);
      let source: EventSource;
      try {
        source = new EventSource(url);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
        return;
      }
      sourceRef.current = source;

      source.onopen = () => {
        if (disposed) return;
        attemptRef.current = 0;
        setConnected(true);
        setError(null);
      };

      source.onmessage = (message: MessageEvent<string>) => {
        const parsed = parseEvent(message.data);
        if (parsed) push(parsed);
      };

      for (const type of AGENT_EVENT_TYPES) {
        source.addEventListener(type, (event: Event) => {
          const data = (event as MessageEvent<string>).data;
          const parsed = parseEvent(data, type);
          if (parsed) push(parsed);
        });
      }

      source.onerror = () => {
        if (disposed) return;
        setConnected(false);
        // EventSource retries by itself, but a server that closed the stream
        // needs a fresh connection: close and reconnect with backoff.
        source.close();
        if (sourceRef.current === source) sourceRef.current = null;
        attemptRef.current += 1;
        const delay = Math.min(1000 * 2 ** (attemptRef.current - 1), MAX_RETRY_DELAY_MS);
        retryRef.current = window.setTimeout(open, delay);
      };
    };

    open();

    return () => {
      disposed = true;
      if (retryRef.current !== null) {
        window.clearTimeout(retryRef.current);
        retryRef.current = null;
      }
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
      setConnected(false);
    };
  }, [targetId, push]);

  return { events, connected, paused, setPaused, clear, bufferedCount, error };
}
