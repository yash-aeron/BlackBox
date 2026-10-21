import { useMemo } from 'react';
import type { ReactElement } from 'react';
import type { AgentEvent, GraphResponse } from '../api/types';
import {
  actionLabel,
  count,
  eventFields,
  eventKind,
  findLast,
  formatClock,
  readBool,
  readNumber,
  readRecord,
  readString,
  truncate,
} from '../lib/util';
import { ScreenshotView } from './ScreenshotView';
import { StatusBadge } from './StatusBadge';

export interface CurrentStatePanelProps {
  targetId: string;
  graph: GraphResponse | null;
  events: AgentEvent[];
}

/**
 * "What is the agent looking at right now" strip, derived entirely from the
 * live stream plus the loaded graph (no extra requests).
 */
export function CurrentStatePanel({ targetId, graph, events }: CurrentStatePanelProps): ReactElement {
  const stateEvent = useMemo(
    () => findLast(events, (event) => eventKind(event) === 'new_state'),
    [events],
  );
  const actionEvent = useMemo(
    () =>
      findLast(events, (event) => {
        const kind = eventKind(event);
        return kind === 'action_proposed' || kind === 'action_result';
      }),
    [events],
  );

  const stateData = stateEvent ? eventFields(stateEvent) : {};
  const state = readRecord(stateData.state);
  const stateId =
    readString(state.state_id) ?? readString(stateData.state_id) ?? readString(state.id) ?? null;
  const label =
    readString(state.label) ?? readString(state.title) ?? readString(state.url_key) ?? null;
  const url = readString(state.url) ?? readString(stateData.url) ?? null;
  const elements =
    readNumber(state.elements) ??
    (Array.isArray(state.visible_elements) ? state.visible_elements.length : null) ??
    readNumber(stateData.elements);

  const node =
    (stateId ? graph?.nodes.find((candidate) => candidate.id === stateId) : undefined) ??
    (url ? graph?.nodes.find((candidate) => candidate.url === url) : undefined) ??
    null;

  const actionData = actionEvent ? eventFields(actionEvent) : {};
  const actionText = actionEvent ? actionLabel(actionData.action ?? actionData) : null;
  const verified = actionEvent ? readBool(actionData.verified) : null;
  const actionStatus =
    readString(actionData.status) ??
    (verified === true ? 'VERIFIED' : verified === false ? 'FAILED' : null);
  const actionKind = actionEvent ? eventKind(actionEvent) : null;

  const latestTs = actionEvent?.ts ?? stateEvent?.ts ?? null;

  if (!stateEvent && !actionEvent) {
    return (
      <section className="current-state current-state-empty" aria-label="current state">
        <p className="state state-empty">
          no live events yet — run an exploration or a task to see what the agent is looking at.
        </p>
      </section>
    );
  }

  return (
    <section className="current-state" aria-label="current state">
      <div className="cs-thumb">
        <ScreenshotView
          targetId={targetId}
          screenshotRef={node?.screenshot_ref ?? null}
          className="screenshot-thumb"
          alt={label ? `latest state screenshot: ${label}` : 'latest state screenshot'}
        />
      </div>
      <div className="cs-body">
        <p className="cs-title">
          <span className="side-kind">looking at</span>{' '}
          <strong>{truncate(label ?? stateId ?? 'unknown state', 60)}</strong>{' '}
          {node ? <StatusBadge value={node.status} /> : null}{' '}
          {stateId ? <span className="muted mono">{stateId}</span> : null}
        </p>
        {url ? (
          <p className="cs-url mono muted" title={url}>
            {truncate(url, 96)}
          </p>
        ) : null}
        <p className="cs-action">
          {actionText ? (
            <>
              <span className="muted">last {actionKind === 'action_result' ? 'result' : 'proposal'}:</span>{' '}
              <span className="mono">{truncate(actionText, 72)}</span>{' '}
              {actionStatus ? <StatusBadge value={actionStatus} /> : null}
            </>
          ) : (
            <span className="muted">no action proposed yet</span>
          )}
        </p>
        <p className="cs-meta muted mono">
          {elements === null ? 'elements unknown' : `${elements} elements`}
          {node ? ` · ${count(node.visit_count)} visits` : ''}
          {latestTs !== null && latestTs !== undefined ? ` · updated ${formatClock(latestTs)}` : ''}
        </p>
      </div>
    </section>
  );
}
