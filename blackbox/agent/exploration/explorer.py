"""The exploration engine.

    OBSERVE -> BUILD STATE -> IDENTIFY UNKNOWN BEHAVIOR -> GENERATE CANDIDATES
    -> SAFETY FILTER -> RANK BY INFORMATION GAIN -> EXECUTE -> OBSERVE RESULT
    -> COMPARE STATES -> IDENTIFY EFFECTS -> UPDATE HYPOTHESES -> UPDATE GRAPH

Nothing is written to the model that was not observed, and every write carries
the evidence that produced it.  The loop stops safely when any budget is spent,
when the site stops yielding new states, or when the agent would have to leave
the authorized origins.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from blackbox.browser.manager import BrowserManager
from blackbox.browser.sandbox import OriginViolation
from ..execution.assertions import AssertionEngine, verify_effects
from ..execution.executor import ExecutionConfig, Executor
from ..execution.locator import LocatorResolver
from ..execution.recovery import FailureKind, RecoveryManager, RecoveryStrategy
from ..learning.causal import CausalEngine
from ..learning.confidence import ConfidenceModel
from ..learning.effects import EffectExtractor, describe_effects
from ..learning.hypothesis import HypothesisEngine, HypothesisStatus
from ..learning.preconditions import PreconditionLearner
from ..model.action import Action, ActionResult, ActionType, EffectKind, RiskLevel
from ..model.base import Timer, stable_id
from ..model.evidence import Evidence, EvidenceKind
from ..model.graph import ApplicationGraph
from ..model.state import SessionContext, WebsiteState
from ..model.transition import Transition, TransitionStatus
from ..observation.browser import PageObserver
from ..observation.network_guard import NetworkGuard
from ..observation.observation import Observation
from ..perception.element_detector import ElementDetector
from ..perception.page_classifier import assign_page, identity_for, to_page
from ..perception.semantic_labeler import SemanticLabeler
from ..perception.state_fingerprint import (
    FingerprintWeights,
    SimilarityThresholds,
    build_state,
    compare_fingerprints,
    diff_states,
    match_state,
)
from .action_generator import ActionGenerator
from .action_ranker import ActionRanker, RankedAction
from .experiment import BudgetState, ExperimentLedger, ExperimentRecord, ExplorationBudget
from .novelty import NoveltyTracker
from .safety import ApprovalBroker, ApprovalRequest, ExecutionMode, SafetyClassifier, SafetyPolicy

log = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], Awaitable[None] | None]


def find_frontier_state(
    graph: ApplicationGraph,
    novelty: NoveltyTracker,
    current_state_id: str,
    candidate_actions_per_state: dict[str, list[Action]] | None = None,
) -> str | None:
    """Find the best reachable state in the graph that has unexplored candidate actions."""
    candidate_actions_per_state = candidate_actions_per_state or {}
    candidates_scores: list[tuple[float, str]] = []

    for state_id in graph.states:
        if state_id == current_state_id:
            continue
        actions = candidate_actions_per_state.get(state_id, [])
        novel_count = 0
        if actions:
            novel_count = sum(1 for a in actions if not novelty.is_repeat(state_id, a))
        else:
            visits = novelty.state_action_counts.get(state_id, 0)
            if visits < novelty.max_same_state_visits:
                novel_count = max(1, novelty.max_same_state_visits - visits)

        if novel_count > 0:
            path = graph.find_path(current_state_id, lambda s, sid=state_id: s.state_id == sid)
            if path.found:
                priority = novel_count / (path.cost + 1.0)
                candidates_scores.append((priority, state_id))

    if not candidates_scores:
        return None
    candidates_scores.sort(key=lambda x: -x[0])
    return candidates_scores[0][1]


@dataclass
class ExplorationConfig:
    budget: ExplorationBudget = field(default_factory=ExplorationBudget)
    mode: ExecutionMode = ExecutionMode.SUPERVISED
    seed: int = 7
    capture_screenshots: bool = True
    settle_quiet_ms: int = 250
    max_actions_without_new_state: int = 40
    """Stop after this many consecutive actions that produced neither a new state
    nor a newly verified transition.  Sites where behavior lives *inside* one
    state (filters, validation, conditional fields) still make progress through
    verified transitions, so this does not cut exploration short prematurely."""
    pause_between_actions_ms: int = 0
    resume: bool = False
    weights: FingerprintWeights = field(default_factory=FingerprintWeights)
    thresholds: SimilarityThresholds = field(default_factory=SimilarityThresholds)
    configuration_label: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget.to_dict(),
            "mode": self.mode.value,
            "seed": self.seed,
            "capture_screenshots": self.capture_screenshots,
            "settle_quiet_ms": self.settle_quiet_ms,
            "max_actions_without_new_state": self.max_actions_without_new_state,
            "weights": vars(self.weights),
            "thresholds": vars(self.thresholds),
            "configuration_label": self.configuration_label,
        }


@dataclass
class ExplorationResult:
    experiment_id: str = ""
    target_id: str = ""
    stop_reason: str = ""
    actions_executed: int = 0
    actions_blocked: int = 0
    actions_failed: int = 0
    approvals_requested: int = 0
    approvals_granted: int = 0
    new_states: int = 0
    new_transitions: int = 0
    verified_transitions: int = 0
    recoveries: int = 0
    hypotheses_proposed: int = 0
    hypotheses_verified: int = 0
    duration_seconds: float = 0.0
    mean_action_ms: float = 0.0
    mean_observation_ms: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "target_id": self.target_id,
            "stop_reason": self.stop_reason,
            "actions_executed": self.actions_executed,
            "actions_blocked": self.actions_blocked,
            "actions_failed": self.actions_failed,
            "approvals_requested": self.approvals_requested,
            "approvals_granted": self.approvals_granted,
            "new_states": self.new_states,
            "new_transitions": self.new_transitions,
            "verified_transitions": self.verified_transitions,
            "recoveries": self.recoveries,
            "hypotheses_proposed": self.hypotheses_proposed,
            "hypotheses_verified": self.hypotheses_verified,
            "duration_seconds": round(self.duration_seconds, 2),
            "mean_action_ms": round(self.mean_action_ms, 2),
            "mean_observation_ms": round(self.mean_observation_ms, 2),
            "metrics": self.metrics,
        }


class Explorer:
    """Drives one exploration session against an authorized target."""

    def __init__(
        self,
        *,
        target_id: str,
        base_url: str,
        manager: BrowserManager,
        observer: PageObserver,
        graph: ApplicationGraph,
        detector: ElementDetector | None = None,
        generator: ActionGenerator | None = None,
        ranker: ActionRanker | None = None,
        safety: SafetyClassifier | None = None,
        policy: SafetyPolicy | None = None,
        novelty: NoveltyTracker | None = None,
        hypotheses: HypothesisEngine | None = None,
        causal: CausalEngine | None = None,
        effects: EffectExtractor | None = None,
        preconditions: PreconditionLearner | None = None,
        recovery: RecoveryManager | None = None,
        executor: Executor | None = None,
        approvals: ApprovalBroker | None = None,
        ledger: ExperimentLedger | None = None,
        assertions: AssertionEngine | None = None,
        confidence: ConfidenceModel | None = None,
        network_guard: NetworkGuard | None = None,
        on_event: EventSink | None = None,
        storage: Any | None = None,
    ) -> None:
        self.target_id = target_id
        self.base_url = base_url
        self.manager = manager
        self.observer = observer
        self.graph = graph
        self.detector = detector or ElementDetector()
        self.labeler = SemanticLabeler()
        self.generator = generator or ActionGenerator()
        self.ranker = ranker or ActionRanker()
        self.safety = safety or SafetyClassifier()
        self.policy = policy or SafetyPolicy()
        self.novelty = novelty or NoveltyTracker()
        self.hypotheses = hypotheses or HypothesisEngine()
        self.causal = causal or CausalEngine()
        self.effects = effects or EffectExtractor()
        self.preconditions = preconditions or PreconditionLearner()
        self.recovery = recovery or RecoveryManager()
        self.confidence = confidence or ConfidenceModel()
        self.assertions = assertions or AssertionEngine()
        self.ledger = ledger or ExperimentLedger()
        self.network_guard = network_guard
        self.on_event = on_event
        self.storage = storage
        self.approvals = approvals or ApprovalBroker()

        self.resolver = LocatorResolver(network_guard=network_guard)
        self.executor = executor or Executor(
            resolver=self.resolver,
            config=ExecutionConfig(),
            element_provider=self._element_provider,
        )

        self.current_state_id: str | None = None
        self.last_observation: Observation | None = None
        self.last_elements: list[Any] = []
        self.action_latencies: list[float] = []
        self.observation_latencies: list[float] = []
        self.recent_actions: list[tuple[str, str]] = []
        self.stopped = False

    # -- events ------------------------------------------------------------
    async def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        event = {"type": kind, "ts": time.time(), "target_id": self.target_id, **payload}
        try:
            result = self.on_event(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - observers must not break exploration
            log.exception("event sink failed for %s", kind)

    # -- main loop ---------------------------------------------------------
    async def run(self, *, url: str | None = None, config: ExplorationConfig | None = None) -> ExplorationResult:
        config = config or ExplorationConfig()
        self.set_similarity_config(config.weights, config.thresholds)
        budget = BudgetState(budget=config.budget)
        self.novelty.max_repeated_action = config.budget.max_repeated_action
        self.novelty.max_same_state_visits = config.budget.max_same_state_visits

        record = ExperimentRecord(
            target_id=self.target_id,
            kind="exploration",
            seed=config.seed,
            browser_version=self.manager.browser_version,
            model_version=1,
