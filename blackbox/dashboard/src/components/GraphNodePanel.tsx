import type { ReactElement } from 'react';
import { api } from '../api/client';
import type { ElementInfo, GraphNode, GraphResponse, StateDetail } from '../api/types';
import { useApiData } from '../hooks/useApiData';
import {
  classNames,
  count,
  elementFlag,
  elementName,
  elementRole,
  elementValue,
  formatDateTime,
  truncate,
} from '../lib/util';
import { KeyValues, ResourceView } from './Panel';
import { ConfidenceBar } from './ConfidenceBar';
import { ScreenshotView } from './ScreenshotView';
import { StatusBadge } from './StatusBadge';
import type { PathResult } from './GraphView';

export interface GraphNodePanelProps {
  targetId: string;
  node: GraphNode;
  graph: GraphResponse;
  pathInfo: PathResult | null;
  onFindPath: () => void;
  onClearPath: () => void;
  onClose: () => void;
  onSelectEdge: (edgeId: string) => void;
}

const ELEMENT_LIMIT = 80;

/** Side panel for one state: what it looks like, how it is reached, how it leaves. */
export function GraphNodePanel({
  targetId,
  node,
  graph,
  pathInfo,
  onFindPath,
  onClearPath,
  onClose,
  onSelectEdge,
}: GraphNodePanelProps): ReactElement {
  const state = useApiData<StateDetail>(
    () => api.state(targetId, node.id),
    [targetId, node.id],
  );

  const detail = state.data;
  const elements: ElementInfo[] = detail?.visible_elements ?? [];
  const dialogs = detail?.dialogs ?? [];
  const forms = detail?.forms ?? [];
  const fingerprint = detail?.fingerprint ?? null;
  const screenshotRef = detail?.screenshot_ref ?? node.screenshot_ref ?? null;
  const summary = detail?.semantic_summary ?? node.summary ?? null;
  const visits = count(detail?.visit_count ?? node.visit_count);

  const incoming = graph.edges.filter((edge) => edge.target === node.id);
  const outgoing = graph.edges.filter((edge) => edge.source === node.id);
  const isRoot = graph.root_state_id === node.id;

  return (
    <div className="side-panel">
      <header className="side-head">
        <span className="side-kind">state</span>
        <span className="mono side-id" title={node.id}>
          {node.id}
        </span>
        <button type="button" className="btn btn-small" onClick={onClose} aria-label="close state panel">
          close
        </button>
      </header>

      <div className="side-body">
        <p className="badge-row">
          <StatusBadge value={node.status} />
          <StatusBadge value={`${visits} visit${visits === 1 ? '' : 's'}`} kind="kind" />
          {isRoot ? <StatusBadge value="root" kind="kind" /> : null}
          <ConfidenceBar value={node.confidence} />
        </p>

        {node.url ? (
          <p className="side-url">
            <a href={node.url} target="_blank" rel="noreferrer noopener" className="mono link">
              {node.url}
            </a>
          </p>
        ) : null}

        <ScreenshotView targetId={targetId} screenshotRef={screenshotRef} className="screenshot-side" />

        {summary ? <p className="summary">{summary}</p> : null}

        <KeyValues
          entries={[
            { label: 'url key', value: node.url_key ?? '—', mono: true },
            { label: 'page', value: node.page_id ?? '—', mono: true },
            { label: 'elements', value: count(detail?.elements ?? node.elements) },
            { label: 'first seen', value: formatDateTime(detail?.first_seen) },
            { label: 'last seen', value: formatDateTime(detail?.last_seen) },
            { label: 'confidence', value: <ConfidenceBar value={node.confidence} label="state" /> },
          ]}
        />

        <div className="side-actions">
          <button
            type="button"
            className="btn btn-small btn-primary"
            onClick={onFindPath}
            disabled={isRoot}
            title={isRoot ? 'this is the root state' : 'highlight the shortest path from the root'}
          >
            find path from root
          </button>
          {pathInfo ? (
            <button type="button" className="btn btn-small" onClick={onClearPath}>
              clear path
            </button>
          ) : null}
        </div>
        {pathInfo ? (
          <p className={classNames('path-note', pathInfo.found ? 'ok-text' : 'warn-text')}>
            {pathInfo.found
              ? `path from root: ${pathInfo.nodeIds.length} states, ${pathInfo.edgeIds.length} transitions`
              : 'no path from the root in the loaded graph'}
          </p>
        ) : null}

        <ResourceView
          resource={state}
          loadingLabel="loading state detail…"
          emptyMessage="state detail unavailable"
          emptyHint="the API returned no body for this state"
        >
          {() => (
            <>
              <h3 className="side-sub">fingerprint</h3>
              <KeyValues
                entries={[
                  { label: 'title', value: fingerprint?.title || '—' },
                  { label: 'url key', value: fingerprint?.url_key ?? '—', mono: true },
                  { label: 'text sig', value: truncate(fingerprint?.text_signature ?? '—', 60), mono: true },
                  {
                    label: 'element sig',
                    value: `${fingerprint?.element_signature?.length ?? 0} entries`,
                  },
                  { label: 'dialog sig', value: `${fingerprint?.dialog_signature?.length ?? 0} entries` },
                  { label: 'screenshot hash', value: truncate(fingerprint?.screenshot_hash ?? '—', 24), mono: true },
                ]}
              />

              <h3 className="side-sub">visible elements ({elements.length})</h3>
              {elements.length === 0 ? (
                <p className="state state-empty">no elements recorded for this state</p>
              ) : (
                <>
                  <table className="tbl tbl-elements">
                    <thead>
                      <tr>
                        <th scope="col">role</th>
                        <th scope="col">accessible name</th>
                        <th scope="col">on</th>
                        <th scope="col">vis</th>
                        <th scope="col">value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {elements.slice(0, ELEMENT_LIMIT).map((element, index) => (
                        <tr key={element.element_id ?? `element-${index}`}>
                          <td className="mono">{elementRole(element)}</td>
                          <td title={elementName(element)}>{truncate(elementName(element) || '—', 40)}</td>
                          <td>{elementFlag(element.enabled, true) ? 'yes' : 'no'}</td>
                          <td>{elementFlag(element.visible, true) ? 'yes' : 'no'}</td>
                          <td className="mono">
                            {elementValue(element) === null ? <span className="muted">—</span> : elementValue(element)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {elements.length > ELEMENT_LIMIT ? (
                    <p className="muted">
                      showing {ELEMENT_LIMIT} of {elements.length} elements
                    </p>
                  ) : null}
                </>
              )}

              {dialogs.length > 0 ? (
                <>
                  <h3 className="side-sub">dialogs ({dialogs.length})</h3>
                  <ul className="chip-list">
                    {dialogs.map((dialog, index) => (
                      <li key={`dialog-${index}`} className="chip">
                        {dialog.title || dialog.kind || dialog.role || 'dialog'}
                        {dialog.actions?.length ? ` · ${dialog.actions.join(', ')}` : ''}
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}

              {forms.length > 0 ? (
                <>
                  <h3 className="side-sub">forms ({forms.length})</h3>
                  <ul className="chip-list">
                    {forms.map((form, index) => (
                      <li key={form.form_id ?? `form-${index}`} className="chip">
                        {form.name || form.form_id || 'form'} · {form.fields?.length ?? 0} fields
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
            </>
          )}
        </ResourceView>

        <h3 className="side-sub">incoming transitions ({incoming.length})</h3>
        <TransitionList
          edges={incoming}
          emptyMessage="nothing leads here yet"
          direction="in"
          onSelectEdge={onSelectEdge}
        />

        <h3 className="side-sub">outgoing transitions ({outgoing.length})</h3>
        <TransitionList
          edges={outgoing}
          emptyMessage="no action has been learned from this state"
          direction="out"
          onSelectEdge={onSelectEdge}
        />
      </div>
    </div>
  );
}

function TransitionList({
  edges,
  emptyMessage,
  direction,
  onSelectEdge,
}: {
  edges: GraphResponse['edges'];
  emptyMessage: string;
  direction: 'in' | 'out';
  onSelectEdge: (edgeId: string) => void;
}): ReactElement {
  if (edges.length === 0) return <p className="state state-empty">{emptyMessage}</p>;
  return (
    <ul className="edge-list">
      {edges.map((edge) => (
        <li key={edge.id}>
          <button type="button" className="edge-row" onClick={() => onSelectEdge(edge.id)}>
            <StatusBadge value={edge.status} />
            <span className="edge-action mono" title={edge.action}>
              {truncate(edge.action, 34)}
            </span>
            <span className="muted mono">
              {direction === 'in' ? `from ${truncate(edge.source, 12)}` : `to ${truncate(edge.target, 12)}`}
            </span>
            <ConfidenceBar value={edge.confidence} label="" showValue={false} />
          </button>
        </li>
      ))}
    </ul>
  );
}
