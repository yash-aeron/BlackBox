import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactElement } from 'react';
import type { GraphEdge, GraphNode, GraphResponse } from '../api/types';
import { clamp, classNames, count, normalizeToken, truncate } from '../lib/util';
import { EmptyState, ErrorState, Loading, Panel } from './Panel';
import { GraphEdgePanel } from './GraphEdgePanel';
import { GraphNodePanel } from './GraphNodePanel';

/* --------------------------------------------------------------- geometry */

export const NODE_W = 190;
export const NODE_H = 48;
export const MIN_ZOOM = 0.3;
export const MAX_ZOOM = 3;

const COL_GAP = 116;
const ROW_GAP = 22;
const PAD = 32;
const FIT_MARGIN = 24;

const UNKNOWN_COLOR = '#6e7681';
export const PATH_COLOR = '#58a6ff';

/** Edge colour by transition status (semantic colour only). */
export const EDGE_STATUS_COLORS: Record<string, string> = {
  VERIFIED: '#3fb950',
  PROPOSED: '#d29922',
  REFUTED: '#f85149',
  STALE: UNKNOWN_COLOR,
  UNKNOWN: UNKNOWN_COLOR,
};

export function edgeColor(status: unknown): string {
  return EDGE_STATUS_COLORS[normalizeToken(status)] ?? UNKNOWN_COLOR;
}

/** Node fill by state status. */
export const STATE_FILL: Record<string, string> = {
  CURRENT: '#123449',
  STALE: '#161b22',
  UNKNOWN: '#11161c',
};

export function nodeFill(status: unknown): string {
  return STATE_FILL[normalizeToken(status)] ?? STATE_FILL.UNKNOWN;
}

export interface LaidOutNode {
  node: GraphNode;
  x: number;
  y: number;
  depth: number;
}

export interface LaidOutEdge {
  edge: GraphEdge;
  path: string;
  color: string;
  width: number;
  dashed: boolean;
  markerId: string;
}

export interface GraphLayout {
  nodes: LaidOutNode[];
  edges: LaidOutEdge[];
  nodeById: Map<string, LaidOutNode>;
  width: number;
  height: number;
  rootId: string | null;
}

function edgePath(source: LaidOutNode, target: LaidOutNode): string {
  const x1 = source.x + NODE_W;
  const y1 = source.y + NODE_H / 2;
  if (source.node.id === target.node.id) {
    const x = source.x + NODE_W * 0.5;
    const y = source.y;
    return `M ${x} ${y} C ${x + 70} ${y - 62}, ${x - 70} ${y - 62}, ${x - 44} ${y - 1}`;
  }
  const x2 = target.x;
  const y2 = target.y + NODE_H / 2;
  if (x2 >= x1) {
    const dx = Math.max(40, (x2 - x1) * 0.5);
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  }
  // Backwards edge: bow it below both endpoints so it never hides a forward one.
  const bend = Math.max(70, Math.abs(y2 - y1) * 0.5 + 80);
  return `M ${x1} ${y1} C ${x1 + 90} ${y1 + bend}, ${x2 - 90} ${y2 + bend}, ${x2} ${y2}`;
}

/**
 * Deterministic BFS-layered layout: left→right by BFS depth from the root,
 * vertically distributed inside each layer and ordered by node id. The same
 * graph always produces the same coordinates, so a refresh never moves the
 * picture.
 */
export function computeLayout(graph: GraphResponse): GraphLayout {
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  const byId = new Map<string, GraphNode>();
  for (const node of nodes) byId.set(node.id, node);

  const outgoing = new Map<string, string[]>();
  for (const edge of edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue;
    const list = outgoing.get(edge.source);
    if (list) list.push(edge.target);
    else outgoing.set(edge.source, [edge.target]);
  }
  for (const list of outgoing.values()) list.sort();

  const sortedIds = [...byId.keys()].sort();
  const rootId =
    graph.root_state_id && byId.has(graph.root_state_id) ? graph.root_state_id : sortedIds[0] ?? null;

  const depth = new Map<string, number>();
  if (rootId) {
    depth.set(rootId, 0);
    const queue: string[] = [rootId];
    while (queue.length > 0) {
      const current = queue.shift() as string;
      const currentDepth = depth.get(current) ?? 0;
      for (const next of outgoing.get(current) ?? []) {
        if (depth.has(next)) continue;
        depth.set(next, currentDepth + 1);
        queue.push(next);
      }
    }
  }

  // States unreachable from the root get one trailing layer, ordered by id.
  const unreachable = sortedIds.filter((id) => !depth.has(id));
  let deepest = 0;
  for (const value of depth.values()) deepest = Math.max(deepest, value);
  if (unreachable.length > 0) {
    const layer = depth.size > 0 ? deepest + 1 : 0;
    for (const id of unreachable) {
      depth.set(id, layer);
      deepest = Math.max(deepest, layer);
    }
  }

  const layers = new Map<number, string[]>();
  for (const id of sortedIds) {
    const layer = depth.get(id) ?? 0;
    const list = layers.get(layer);
    if (list) list.push(id);
    else layers.set(layer, [id]);
  }
  for (const list of layers.values()) list.sort();

  const tallest = Math.max(1, ...[...layers.values()].map((list) => list.length));
  const contentHeight = tallest * NODE_H + (tallest - 1) * ROW_GAP;
  const depthCount = deepest + 1;

  const laidOut: LaidOutNode[] = [];
  const nodeById = new Map<string, LaidOutNode>();
  for (const layer of [...layers.keys()].sort((a, b) => a - b)) {
    const ids = layers.get(layer) ?? [];
    const columnHeight = ids.length * NODE_H + Math.max(0, ids.length - 1) * ROW_GAP;
    const offset = PAD + (contentHeight - columnHeight) / 2;
    ids.forEach((id, index) => {
      const node = byId.get(id);
      if (!node) return;
      const entry: LaidOutNode = {
        node,
        depth: layer,
        x: PAD + layer * (NODE_W + COL_GAP),
        y: offset + index * (NODE_H + ROW_GAP),
      };
      laidOut.push(entry);
      nodeById.set(id, entry);
    });
  }

  const laidOutEdges: LaidOutEdge[] = [];
  for (const edge of edges) {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    if (!source || !target) continue;
    const confidence = typeof edge.confidence === 'number' ? edge.confidence : 0.5;
    laidOutEdges.push({
      edge,
      path: edgePath(source, target),
      color: edgeColor(edge.status),
      width: clamp(1 + confidence * 2.4, 1, 3.4),
      dashed: count(edge.success_count) === 0,
      markerId: `bb-arrow-${normalizeToken(edge.status).toLowerCase()}`,
    });
  }

  return {
    nodes: laidOut,
    edges: laidOutEdges,
    nodeById,
    width: PAD * 2 + depthCount * NODE_W + Math.max(0, depthCount - 1) * COL_GAP,
    height: PAD * 2 + contentHeight,
    rootId,
  };
}

export interface PathResult {
  found: boolean;
  nodeIds: string[];
  edgeIds: string[];
}

/** Breadth-first shortest path from the root over the already-loaded graph. */
export function findPathFromRoot(graph: GraphResponse, targetStateId: string): PathResult {
  const root = graph.root_state_id;
  const empty: PathResult = { found: false, nodeIds: [], edgeIds: [] };
  if (!root) return empty;
  if (root === targetStateId) return { found: true, nodeIds: [root], edgeIds: [] };

  const adjacency = new Map<string, Array<{ next: string; edgeId: string; rank: number }>>();
  for (const edge of graph.edges) {
    const verified = edge.verified === true || normalizeToken(edge.status) === 'VERIFIED';
    const list = adjacency.get(edge.source);
    const step = { next: edge.target, edgeId: edge.id, rank: verified ? 0 : 1 };
    if (list) list.push(step);
    else adjacency.set(edge.source, [step]);
  }
  for (const list of adjacency.values()) {
    list.sort((a, b) => a.rank - b.rank || a.next.localeCompare(b.next) || a.edgeId.localeCompare(b.edgeId));
  }

  const previous = new Map<string, { from: string; edgeId: string }>();
  const seen = new Set<string>([root]);
  const queue: string[] = [root];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    if (current === targetStateId) break;
    for (const step of adjacency.get(current) ?? []) {
      if (seen.has(step.next)) continue;
      seen.add(step.next);
      previous.set(step.next, { from: current, edgeId: step.edgeId });
      queue.push(step.next);
    }
  }
  if (!previous.has(targetStateId)) return empty;

  const nodeIds: string[] = [targetStateId];
  const edgeIds: string[] = [];
  let cursor = targetStateId;
  while (cursor !== root) {
    const step = previous.get(cursor);
    if (!step) return empty;
    edgeIds.push(step.edgeId);
    nodeIds.push(step.from);
    cursor = step.from;
  }
  nodeIds.reverse();
  edgeIds.reverse();
  return { found: true, nodeIds, edgeIds };
}

/* -------------------------------------------------------------- component */

export interface GraphViewProps {
  targetId: string;
  graph: GraphResponse | null;
  loading: boolean;
  error: unknown;
  onReload: () => void;
}

interface DragState {
  pointerId: number;
  startX: number;
  startY: number;
  originX: number;
  originY: number;
  moved: boolean;
}

const RISK_OPTIONS = ['ALL', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;
const STATUS_OPTIONS = ['ALL', 'VERIFIED', 'PROPOSED', 'REFUTED', 'STALE'] as const;

export function GraphView({ targetId, graph, loading, error, onReload }: GraphViewProps): ReactElement {
  const [query, setQuery] = useState('');
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [riskFilter, setRiskFilter] = useState<string>('ALL');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [pathTo, setPathTo] = useState<string | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, zoom: 1 });
  const [viewport, setViewport] = useState({ width: 0, height: 0 });

  const containerRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);
  const fittedRef = useRef<string | null>(null);

  const layout = useMemo(() => (graph ? computeLayout(graph) : null), [graph]);

  const visibleEdges = useMemo(() => {
    const edges = graph?.edges ?? [];
    const needle = query.trim().toLowerCase();
    const matching = new Set<string>();
    if (needle) {
      for (const node of graph?.nodes ?? []) {
        if (
          node.id.toLowerCase().includes(needle) ||
          (node.label ?? '').toLowerCase().includes(needle) ||
          (node.url ?? '').toLowerCase().includes(needle)
        ) {
          matching.add(node.id);
        }
      }
    }
    return edges.filter((edge) => {
      if (verifiedOnly && !(edge.verified === true || normalizeToken(edge.status) === 'VERIFIED')) return false;
      if (riskFilter !== 'ALL' && normalizeToken(edge.risk) !== riskFilter) return false;
      if (statusFilter !== 'ALL' && normalizeToken(edge.status) !== statusFilter) return false;
      if (needle && !(matching.has(edge.source) || matching.has(edge.target))) return false;
      return true;
    });
  }, [graph, query, verifiedOnly, riskFilter, statusFilter]);

  const visibleEdgeIds = useMemo(() => new Set(visibleEdges.map((edge) => edge.id)), [visibleEdges]);

  const matchingNodeIds = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return null;
    const ids = new Set<string>();
    for (const node of graph?.nodes ?? []) {
      if (
        node.id.toLowerCase().includes(needle) ||
        (node.label ?? '').toLowerCase().includes(needle) ||
        (node.url ?? '').toLowerCase().includes(needle)
      ) {
        ids.add(node.id);
      }
    }
    return ids;
  }, [graph, query]);

  const pathInfo = useMemo(
    () => (graph && pathTo ? findPathFromRoot(graph, pathTo) : null),
    [graph, pathTo],
  );
  const pathNodeIds = useMemo(
    () => new Set(pathInfo && pathInfo.found ? pathInfo.nodeIds : []),
    [pathInfo],
  );
  const pathEdgeIds = useMemo(
    () => new Set(pathInfo && pathInfo.found ? pathInfo.edgeIds : []),
    [pathInfo],
  );
  const pathActive = Boolean(pathInfo && pathInfo.found);

  /* ---------------------------------------------------------- viewport */

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const measure = () => setViewport({ width: element.clientWidth, height: element.clientHeight });
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
