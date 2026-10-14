"""BlackBox: the agent facade that wires the system together.

``BlackBoxAgent`` owns one target: its browser, its observation pipeline, its
model, and its persistence.  Exploration and task execution are the two things
callers do with it, and both write evidence into the same behavioral model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings, demo_registration
from .execution.assertions import AssertionEngine
from .execution.executor import ExecutionConfig, Executor
from .execution.locator import LocatorResolver
from .execution.recovery import RecoveryManager
from .exploration.action_generator import ActionGenerator
from .exploration.action_ranker import ActionRanker
from .exploration.experiment import ExperimentLedger, ExperimentRecord, ExplorationBudget
from .exploration.explorer import ExplorationConfig, ExplorationResult, Explorer
from .exploration.novelty import NoveltyTracker
from .exploration.safety import (
    ApprovalBroker,
    ExecutionMode,
    RiskLevel,
    SafetyClassifier,
    SafetyPolicy,
)
from .learning.causal import CausalEngine
from .learning.confidence import ConfidenceModel
from .learning.effects import EffectExtractor
from .learning.hypothesis import HypothesisEngine
from .learning.preconditions import PreconditionLearner
from .model.evidence import Evidence, EvidenceKind
from .model.graph import ApplicationGraph
from .model.website import HypothesisRecord
from .observation.browser import PageObserver
from .observation.network_guard import NetworkGuard
from .perception.element_detector import ElementDetector
from .perception.page_classifier import assign_page, identity_for
from .perception.state_fingerprint import (
    FingerprintWeights,
    SimilarityThresholds,
    build_state,
    diff_states,
    match_state,
)
from .perception.semantic_labeler import SemanticLabeler
from .planning.planner import Plan, Planner
from .planning.replanner import ReplanKind, Replanner
from .planning.task_parser import TaskParser, TaskSpec
from .planning.verifier import StepVerification, TaskVerification, Verifier
from .learning.workflow_miner import WorkflowMiner
from .llm.provider import LLMProvider, build_provider
from .storage.database import Database
from .storage.repository import Repository
from .storage.targets import TargetRegistration
from blackbox.browser.manager import BrowserManager
from blackbox.browser.permissions import default_policy
from blackbox.browser.sandbox import Sandbox

log = logging.getLogger(__name__)


@dataclass
class AgentStatus:
    target_id: str
    base_url: str
    mode: str
    browser: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    sandbox: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    llm: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "base_url": self.base_url,
            "mode": self.mode,
            "browser": self.browser,
            "model": self.model,
            "sandbox": self.sandbox,
            "policy": self.policy,
            "settings": self.settings,
            "llm": self.llm,
        }


@dataclass
class TaskResult:
    """Outcome of one natural-language task executed against the learned model."""

    task: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    success: bool = False
    actions_executed: int = 0
    steps_verified: int = 0
    steps_total: int = 0
    recoveries: int = 0
    replans: int = 0
    used_workflow: str | None = None
    used_exploration: bool = False
    duration_seconds: float = 0.0
    verification: dict[str, Any] = field(default_factory=dict)
    step_results: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    planning_latency_ms: float = 0.0
    model_states_before: int = 0
    model_states_after: int = 0
    current_state_matched: bool = False
    plan_source: str = "none"
    transition_predictions_hit: int = 0
    transition_predictions_missed: int = 0
    unnecessary_actions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "plan": self.plan,
            "success": self.success,
            "actions_executed": self.actions_executed,
            "steps_verified": self.steps_verified,
            "steps_total": self.steps_total,
            "recoveries": self.recoveries,
            "replans": self.replans,
            "used_workflow": self.used_workflow,
            "used_exploration": self.used_exploration,
            "duration_seconds": round(self.duration_seconds, 2),
            "planning_latency_ms": round(self.planning_latency_ms, 2),
            "verification": self.verification,
            "step_results": self.step_results,
            "notes": self.notes,
            "model_states_before": self.model_states_before,
            "model_states_after": self.model_states_after,
            "current_state_matched": self.current_state_matched,
            "plan_source": self.plan_source,
            "transition_predictions_hit": self.transition_predictions_hit,
            "transition_predictions_missed": self.transition_predictions_missed,
            "unnecessary_actions": self.unnecessary_actions,
        }


class BlackBoxAgent:
    """One authorized target, fully wired."""

    def __init__(
        self,
        settings: Settings,
        registration: TargetRegistration,
        *,
        on_event: Any | None = None,
        load_existing_model: bool = True,
    ) -> None:
        self.settings = settings
        self.registration = registration
        self.on_event = on_event
        self.running = False

        settings.ensure_dirs()
        self.database = Database(settings.database_dsn)
        self.database.connect()
        self.repository = Repository(self.database, registration.target_id)
        self.repository.save_target(registration)

        self.sandbox = Sandbox(allowed_origins=registration.normalized_origins())
        self.network_guard = NetworkGuard(allowed_origins=registration.normalized_origins())
        self.permissions = default_policy(settings.artifacts_dir / "downloads")
        self.manager = BrowserManager(
            sandbox=self.sandbox,
            permissions=self.permissions,
            artifacts_dir=settings.artifacts_dir,
            headless=settings.headless,
            recording=settings.recording,
            window_size=(settings.viewport_width, settings.viewport_height),
            executable=Path(settings.chromium_executable) if settings.chromium_executable else None,
        )
        self.observer = PageObserver(
            settings.artifacts_dir,
            capture_screenshots=settings.capture_screenshots,
            network_guard=self.network_guard,
        )

        policy = SafetyPolicy(
            mode=registration.mode,
            autonomous_risk=RiskLevel.LOW,
            allow_medium_with_config=registration.allow_medium_actions,
            allow_high_with_config=registration.allow_high_actions,
            allow_critical_with_config=registration.allow_critical_actions,
        )
        self.approvals = ApprovalBroker(
            timeout_seconds=settings.approval_timeout_seconds,
            unattended=settings.unattended,
        )
        self.ledger = ExperimentLedger()
        # Continue in the newest model version so a restart reuses what was
        # learned instead of starting over in a fresh version.
        self.repository.use_latest_version()
        self.graph = self.repository.load_graph() if load_existing_model else ApplicationGraph(target_id=registration.target_id)
        self.graph.target_id = registration.target_id

        self.detector = ElementDetector()
        self.generator = ActionGenerator()
        self.ranker = ActionRanker()
        self.hypotheses = HypothesisEngine()
        self.recovery = RecoveryManager()
        self.confidence = ConfidenceModel()
        self.assertions = AssertionEngine()
        self.executor = Executor(
            resolver=LocatorResolver(network_guard=self.network_guard),
            config=ExecutionConfig(upload_fixtures=settings.artifacts_dir / "fixtures"),
        )

        self.explorer: Explorer | None = None
        # Optional: no LLM is required, and the deterministic path never waits on one.
        self.llm: LLMProvider = build_provider(settings)
        self._loaded_model_stats = self.repository.stats()

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self.running:
            return
        page = await self.manager.start()
        self._build_explorer()
        self.running = True
        self.repository.save_event(
            "session_start",
            {
                "browser_version": self.manager.browser_version,
                "model_version": self.repository.model_version,
                "states_loaded": len(self.graph.states),
                "transitions_loaded": len(self.graph.transitions),
            },
        )
        log.info(
            "BlackBox ready for %s at %s (browser %s, model v%d, %d states loaded)",
            self.registration.target_id,
            self.registration.base_url,
            self.manager.browser_version,
            self.repository.model_version,
            len(self.graph.states),
        )

    def _build_explorer(self) -> None:
        self.explorer = Explorer(
            target_id=self.registration.target_id,
            base_url=self.registration.base_url,
            manager=self.manager,
            observer=self.observer,
            graph=self.graph,
