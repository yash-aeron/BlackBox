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
  /** Some payloads flatten the description onto the action. */
  description?: string | null;
  [key: string]: unknown;
}

export interface ObservedEffect {
  kind: string;
  detail?: string | null;
  before?: string | null;
  after?: string | null;
  element_id?: string | null;
  message?: string | null;
  [key: string]: unknown;
}

export interface TransitionSummary {
  transition_id: string;
  source_state: string;
  target_state: string;
  action: string;
  action_type?: string | null;
  risk: Risk;
  confidence: number;
  status: TransitionStatus;
  verified?: boolean;
  execution_count?: number | null;
  success_count?: number | null;
  failure_count?: number | null;
  effects?: string[] | null;
  evidence_ids?: string[] | null;
}

export interface TransitionDetail {
  transition_id: string;
  source_state: string;
  target_state: string;
  action: ActionDescriptor;
  observed_effects: ObservedEffect[];
  preconditions: string[];
  postconditions: string[];
  confidence: number;
  status: TransitionStatus;
  execution_count?: number | null;
  success_count?: number | null;
  failure_count?: number | null;
  evidence_ids: string[];
  created_at?: Timestamp;
  updated_at?: Timestamp;
}

/* --------------------------------------------------------------- workflows */

export interface WorkflowParameter {
  name: string;
  label?: string | null;
  kind?: string | null;
  required?: boolean | null;
  example_value?: string | null;
  target_element_id?: string | null;
  [key: string]: unknown;
}

export interface WorkflowStep {
  index: number;
  description?: string | null;
  parameter_names?: string[] | null;
  expected_effect?: string[] | null;
  transition_id?: string | null;
  action?: ActionDescriptor | null;
  optional?: boolean | null;
  [key: string]: unknown;
}

export interface WorkflowSummary {
  workflow_id: string;
  name: string;
  goal: string;
  confidence: number;
  verification_count?: number | null;
  expected_end_state?: string | null;
  start_state_id?: string | null;
  path_state_ids?: string[] | null;
  source?: string | null;
  created_at?: Timestamp;
  updated_at?: Timestamp;
  /** Present when the list endpoint returns full workflows rather than summaries. */
  parameters?: WorkflowParameter[] | null;
  steps?: WorkflowStep[] | null;
  preconditions?: string[] | null;
  evidence_ids?: string[] | null;
  [key: string]: unknown;
}

export interface WorkflowDetail extends WorkflowSummary {
  parameters: WorkflowParameter[];
  steps: WorkflowStep[];
  preconditions: string[];
  evidence_ids: string[];
}

/* ------------------------------------------------- hypotheses/constraints */

export interface Hypothesis {
  hypothesis_id: string;
  statement: string;
  status: string;
  confidence: number;
  supporting_experiments: string[];
  contradicting_experiments: string[];
  evidence: string[];
  updated_at?: Timestamp;
  kind?: string | null;
  subject?: string | null;
  prediction?: Record<string, unknown> | null;
  details?: Record<string, unknown> | null;
  created_at?: Timestamp;
  [key: string]: unknown;
}

export interface Constraint {
  constraint_id: string;
  kind: string;
  scope: string;
  subject: string;
  expression: string;
  message?: string | null;
  status: string;
  confidence: number;
  supporting_evidence: string[];
  contradicting_evidence: string[];
  condition?: Record<string, unknown> | null;
  created_at?: Timestamp;
  updated_at?: Timestamp;
  [key: string]: unknown;
}

/* ---------------------------------------------------------------- evidence */

export interface EvidenceRecord {
  evidence_id: string;
  kind: string;
  experiment_id?: string | null;
  action_id?: string | null;
  source_state?: string | null;
  target_state?: string | null;
  transition_id?: string | null;
  effects?: Array<string | ObservedEffect> | null;
  observations?: string[] | null;
  screenshot_refs?: string[] | null;
  message?: string | null;
  notes?: string[] | null;
  step_index?: number | null;
  session_id?: string | null;
  created_at?: Timestamp;
  [key: string]: unknown;
}

/* ------------------------------------------------------------- experiments */

export interface Experiment {
  experiment_id: string;
  kind: string;
  status: string;
  target_id?: string | null;
  seed?: number | null;
  browser_version?: string | null;
  model_version?: number | null;
  prompt_version?: string | null;
  budget?: Record<string, unknown> | null;
  configuration?: Record<string, unknown> | null;
  started_at?: Timestamp;
  finished_at?: Timestamp;
  steps?: Array<Record<string, unknown>> | null;
  metrics?: Record<string, unknown> | null;
  notes?: string[] | null;
  [key: string]: unknown;
}

/* ----------------------------------------------------------------- metrics */

export interface ObservationMetrics {
  observations?: number | null;
  mean_capture_ms?: number | null;
  [key: string]: unknown;
}

export interface SandboxMetrics {
  blocked_count?: number | null;
  [key: string]: unknown;
}

export interface NetworkGuardMetrics {
  observed_requests?: number | null;
  blocked_agent_requests?: number | null;
  policy?: string | Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface MetricsResponse {
  target_id?: string | null;
  model_version?: number | null;
  states?: number | null;
  transitions?: number | null;
  verified_transitions?: number | null;
  workflows?: number | null;
  constraints?: number | null;
  hypotheses?: number | null;
  evidence?: number | null;
  experiments?: number | null;
  pages?: number | null;
  observation?: ObservationMetrics | null;
  sandbox?: SandboxMetrics | null;
  network_guard?: NetworkGuardMetrics | null;
  [key: string]: unknown;
}

/* -------------------------------------------------------------- approvals */

export interface ApprovalRequest {
  request_id: string;
  action: string;
  target?: string | null;
  risk: Risk;
  reason?: string | null;
  expected_effect?: string | string[] | null;
  evidence?: string[] | null;
  created_at?: Timestamp;
  [key: string]: unknown;
}

export interface ApprovalInbox {
  pending: ApprovalRequest[];
  decisions?: number;
  approved?: number;
  rejected?: number;
}

export interface ApprovalDecisionResponse {
  request_id?: string;
  decision?: string;
  status?: string;
  ok?: boolean;
  [key: string]: unknown;
}

/* -------------------------------------------------------------------- jobs */

export interface JobStatus<R = TaskResult | null> {
  job_id: string;
  kind: string;
  status: JobLifecycle;
  target_id?: string | null;
  started_at?: Timestamp;
  finished_at?: Timestamp;
  duration_seconds?: number | null;
  stop_requested?: boolean;
  error?: string | null;
  result: R;
}

export interface ExploreRequest {
  max_actions?: number | null;
  max_seconds?: number | null;
  max_states?: number | null;
  configuration_label?: string | null;
}

export interface JobHandle {
  job_id: string;
  [key: string]: unknown;
}

/* ------------------------------------------------------------------- tasks */

export interface PredicateSpec {
  kind: string;
  value: string;
  target: string;
}

/** Shape accepted by `POST /tasks` and `predicates` inside `available tasks`. */
export interface PredicateSpecLoose {
  kind: string;
  value?: unknown;
  target?: unknown;
}

export interface TaskRequest {
  text: string;
  predicates: PredicateSpec[];
  allow_exploration: boolean;
  use_workflows: boolean;
  use_transitions: boolean;
  max_exploration_actions: number;
}

export interface BenchmarkTask {
  task_id: string;
  text: string;
  target: string;
  predicates: PredicateSpecLoose[];
  tags: string[];
  difficulty: string;
  expect_blocked: boolean;
  [key: string]: unknown;
}

export interface TaskGoal {
  goal: string;
  entities?: Record<string, string> | Array<string | Record<string, unknown>> | null;
  expected_end_state?: string | null;
  task_id?: string | null;
  constraints?: string[] | null;
  source_text?: string | null;
  parse_source?: string | null;
  predicates?: PredicateSpecLoose[] | null;
  [key: string]: unknown;
}

export interface PlanStep {
  index: number;
  action: string;
  action_type: string;
  description: string;
  target_state?: string | null;
  risk: Risk;
  parameters?: Record<string, unknown> | null;
  transition_id?: string | null;
  source_state?: string | null;
  expected_effect?: string[] | null;
  [key: string]: unknown;
}

export interface TaskPlan {
  plan_id: string;
  source: string;
  workflow_id?: string | null;
  start_state_id?: string | null;
  expected_end_state?: string | null;
  confidence: number;
  estimated_cost?: number | null;
  steps: PlanStep[];
  notes: string[];
  exploration_hint?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface PredicateOutcome {
  kind: string;
  value?: unknown;
  satisfied: boolean;
  evidence?: string | null;
  target?: string | null;
  [key: string]: unknown;
}

export interface TaskVerification {
  success: boolean;
  steps_verified: number;
  steps_total: number;
  confidence: number;
  predicates: PredicateOutcome[];
  final_state_id?: string | null;
  notes?: string[] | null;
}

export interface StepResult {
  step_index: number;
  verified: boolean;
  expected: string[];
  matched: string[];
  missed: string[];
  no_effect: boolean;
  notes: string[];
  action: string;
  status: string;
  duration_ms?: number | null;
  locator?: string | null;
  error?: string | null;
  transition_id?: string | null;
  [key: string]: unknown;
}

export interface TaskResult {
  task: TaskGoal;
  plan: TaskPlan;
  success: boolean;
  actions_executed: number;
  steps_verified: number;
  steps_total: number;
  recoveries: number;
  replans: number;
  /** Workflow id that produced the plan, or null when the plan came from the graph. */
  used_workflow?: string | null;
  used_exploration: boolean;
  duration_seconds: number;
  planning_latency_ms: number;
  verification: TaskVerification;
  step_results: StepResult[];
  notes: string[];
  current_state_matched: boolean;
  plan_source: string;
  transition_predictions_hit: number;
  transition_predictions_missed: number;
  unnecessary_actions: number;
  model_states_before?: number | null;
  model_states_after?: number | null;
  [key: string]: unknown;
}

/* ------------------------------------------------------------------- logs */

export interface LogRecord {
  event_id: string;
  target_id: string;
  kind: string;
  created_at?: Timestamp;
  payload?: Record<string, unknown> | null;
}

/* ------------------------------------------------------------------ events */

/**
 * One SSE message from `/api/targets/{id}/stream` (and one row from `/logs`).
 * The server sends `EventOut`: `{type, ts, target_id, kind, summary, payload}`;
 * `payload` carries the type-specific fields.
 */
export interface AgentEvent {
  type: AgentEventType;
  ts?: Timestamp;
  target_id?: string | null;
  kind?: string | null;
  /** Server-provided one-line summary; preferred over the client fallback. */
  summary?: string | null;
  payload?: Record<string, unknown> | null;
  /** Flattened event fields for servers that inline the payload. */
  [key: string]: unknown;
}
