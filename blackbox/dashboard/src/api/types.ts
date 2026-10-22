/**
 * Wire types for the BlackBox HTTP API (`/api`).
 *
 * These mirror the FastAPI schemas in `blackbox/api/schemas/models.py` and the
 * behavioural-model objects they serialise (`AgentStatus`, `TaskResult`,
 * `ApplicationGraph.to_graph_json()`, `WebsiteState`, `Transition`, `Action`,
 * `Workflow`, `Hypothesis`, `Constraint`, `Evidence`, `ExperimentRecord`,
 * `ApprovalRequest`). Fields are optional wherever the server may omit them, so
 * a backend that grows a field never breaks the dashboard.
 */

/** Unix seconds (float) or ISO-8601, depending on the endpoint. Always tolerant. */
export type Timestamp = number | string | null;

/**
 * Known values first, then `(string & {})` so a new server-side value still
 * typechecks while the editor keeps autocompleting the known ones.
 */
export type Risk = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | (string & {});
export type TransitionStatus = 'PROPOSED' | 'VERIFIED' | 'REFUTED' | 'STALE' | (string & {});
export type StateStatus = 'CURRENT' | 'STALE' | 'UNKNOWN' | (string & {});
export type JobLifecycle =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | (string & {});
export type ApprovalDecision = 'APPROVE' | 'REJECT' | 'PAUSE' | 'STOP';
export type AgentEventType =
  | 'new_state'
  | 'action_proposed'
  | 'action_result'
  | 'ranking'
  | 'action_blocked'
  | 'action_denied'
  | 'approval_requested'
  | 'approval_resolved'
  | 'constraint_learned'
  | 'hypothesis'
  | 'origin_blocked'
  | 'exploration_finished'
  | 'job_started'
  | 'job_finished'
  | 'session_start'
  | 'network_blocked'
  | 'stop_requested'
  | (string & {});

/* ------------------------------------------------------------------ health */

export interface HealthResponse {
  status: string;
  browser: string;
  version: string;
}

/* ----------------------------------------------------------------- targets */

/** `BehavioralModel.summary()`-shaped counts. */
export interface ModelSummary {
  target_id?: string | null;
  base_url?: string | null;
  model_version?: number | null;
  states?: number | null;
  transitions?: number | null;
  verified_transitions?: number | null;
  workflows?: number | null;
  constraints?: number | null;
  hypotheses?: number | null;
  pages?: number | null;
  evidence?: number | null;
  experiments?: number | null;
  [key: string]: unknown;
}

export interface TargetSummary {
  target_id: string;
  base_url: string;
  mode: string;
  stats: ModelSummary;
  allowed_origins?: string[];
  max_steps?: number | null;
  max_duration_seconds?: number | null;
  allow_medium_actions?: boolean;
  allow_high_actions?: boolean;
  allow_critical_actions?: boolean;
  authorized_by?: string;
  notes?: string;
  registered?: boolean;
  live?: boolean;
  browser_alive?: boolean;
  start_error?: string | null;
  model_version?: number | null;
  job_counts?: Record<string, number>;
  pending_approvals?: number;
}

export interface RegisterTargetRequest {
  target_id: string;
  base_url: string;
  allowed_origins: string[];
  mode: string;
  max_steps: number;
  max_duration_seconds: number;
  allow_medium_actions: boolean;
  allow_high_actions: boolean;
  allow_critical_actions: boolean;
  authorized_by: string;
  notes?: string;
}

/* ------------------------------------------------------------ agent status */

export interface SandboxStatus {
  allowed_origins?: string[];
  blocked_count?: number;
  blocked?: string[];
  [key: string]: unknown;
}

export interface BrowserStatus {
  alive?: boolean;
  browser_version?: string | null;
  headless?: boolean | null;
  restarts?: number;
  crashes?: number;
  last_error?: string | null;
  profile?: string | null;
  sandbox?: SandboxStatus | null;
  [key: string]: unknown;
}

export interface PolicyStatus {
  allow_low_actions?: boolean;
  allow_medium_actions?: boolean;
  allow_high_actions?: boolean;
  allow_critical_actions?: boolean;
  approvals?: ApprovalInbox | null;
  [key: string]: unknown;
}

export interface AgentStatus {
  target_id?: string;
  base_url?: string;
  mode?: string;
  browser?: BrowserStatus | null;
  model?: ModelSummary | null;
  sandbox?: SandboxStatus | null;
  policy?: PolicyStatus | null;
  settings?: Record<string, unknown> | null;
  llm?: Record<string, unknown> | null;
  [key: string]: unknown;
}

/* ------------------------------------------------------------------- graph */

export interface GraphNode {
  id: string;
  label: string;
  url: string;
  url_key?: string | null;
  page_id?: string | null;
  status: StateStatus;
  confidence?: number | null;
  visit_count?: number | null;
  /** Number of visible elements observed in this state. */
  elements?: number | null;
  summary?: string | null;
  screenshot_ref?: string | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  /** Human-readable action description, e.g. `CLICK Save customer`. */
  action: string;
  action_type?: string | null;
  risk: Risk;
  confidence?: number | null;
  status: TransitionStatus;
  verified?: boolean;
  success_count?: number | null;
  failure_count?: number | null;
  /** Observed effect kinds, e.g. `["DIALOG_OPENED"]`. */
  effects?: string[] | null;
  evidence_ids?: string[] | null;
}

export interface GraphStats {
  states?: number | null;
  transitions?: number | null;
  verified?: number | null;
  workflows?: number | null;
  constraints?: number | null;
  pages?: number | null;
  [key: string]: unknown;
}

export interface GraphResponse {
  target_id?: string;
  root_state_id: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: GraphStats;
}

/* ------------------------------------------------------------------ states */

export interface LocatorCandidate {
  strategy?: string | null;
  selector?: string | null;
  confidence?: number | null;
  evidence?: string | null;
  params?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface ValueState {
  value?: string | null;
  checked?: boolean | null;
  selected?: string | null;
  disabled_reason?: string | null;
}

/**
 * One observed element. Canonical field names come from `model/element.py`;
 * the short aliases (`role`, `name`, `value`, `locators`) are accepted too so
 * payloads from other producers still render.
 */
export interface ElementInfo {
  element_id?: string | null;
  semantic_role?: string | null;
  role?: string | null;
  accessible_name?: string | null;
  name?: string | null;
  visible_text?: string | null;
  tag?: string | null;
  enabled?: boolean | null;
  visible?: boolean | null;
  focused?: boolean | null;
  editable?: boolean | null;
  value_state?: ValueState | null;
  value?: string | null;
  checked?: boolean | null;
  locator_candidates?: LocatorCandidate[] | null;
  locators?: Array<LocatorCandidate | string> | null;
  confidence?: number | null;
  section?: string | null;
  in_dialog?: boolean | null;
  in_form?: string | null;
  attributes?: Record<string, string> | null;
  [key: string]: unknown;
}

export interface DialogInfo {
  title?: string | null;
  role?: string | null;
  kind?: string | null;
  actions?: string[] | null;
  buttons?: string[] | null;
  text?: string | null;
  message?: string | null;
  [key: string]: unknown;
}

export interface FormFieldInfo {
  element_id?: string | null;
  name?: string | null;
  label?: string | null;
  role?: string | null;
  required?: boolean | null;
  value?: string | null;
  checked?: boolean | null;
  options?: string[] | null;
  [key: string]: unknown;
}

export interface FormInfo {
  form_id?: string | null;
  name?: string | null;
  action_hint?: string | null;
  action?: string | null;
  method?: string | null;
  fields?: FormFieldInfo[] | null;
  submit_element_ids?: string[] | null;
  in_dialog?: boolean | null;
  [key: string]: unknown;
}

export interface StateFingerprint {
  url?: string | null;
  url_key?: string | null;
  title?: string | null;
  text_signature?: string | null;
  text_shingles?: string[] | null;
  element_signature?: string[] | null;
  accessibility_signature?: string[] | null;
  form_signature?: string[] | null;
  selected_controls?: Record<string, string> | null;
  dialog_signature?: string[] | null;
  pagination_signature?: string | null;
  screenshot_hash?: string | null;
  semantic_summary?: string | null;
  [key: string]: unknown;
}

export interface StateSummary {
  state_id: string;
  url?: string | null;
  url_key?: string | null;
  title?: string | null;
  status?: StateStatus;
  confidence?: number | null;
  visit_count?: number | null;
  semantic_summary?: string | null;
  screenshot_ref?: string | null;
  first_seen?: Timestamp;
  last_seen?: Timestamp;
}

export interface StateDetail extends StateSummary {
  fingerprint?: StateFingerprint | null;
  visible_text?: string | null;
  visible_elements?: ElementInfo[] | null;
  elements?: number | null;
  dialogs?: DialogInfo[] | null;
  forms?: FormInfo[] | null;
  pagination?: Record<string, unknown> | null;
  session?: Record<string, unknown> | null;
  observation_id?: string | null;
  page_id?: string | null;
}

/* ------------------------------------------------------------- transitions */

export interface ActionTarget {
  element_id?: string | null;
  role?: string | null;
  name?: string | null;
  tag?: string | null;
  locators?: Array<LocatorCandidate | string> | null;
  [key: string]: unknown;
}

export interface ActionDescriptor {
  action_id?: string | null;
  type: string;
  target?: ActionTarget | null;
  parameters?: Record<string, unknown> | null;
  expected_effect?: string[] | null;
  risk?: Risk;
  confidence?: number | null;
  origin?: string | null;
  rationale?: string | null;
