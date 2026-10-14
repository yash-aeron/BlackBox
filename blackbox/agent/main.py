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
            detector=self.detector,
            generator=self.generator,
            ranker=self.ranker,
            safety=SafetyClassifier(),
            policy=SafetyPolicy(
                mode=self.registration.mode,
                autonomous_risk=RiskLevel.LOW,
                allow_medium_with_config=self.registration.allow_medium_actions,
                allow_high_with_config=self.registration.allow_high_actions,
                allow_critical_with_config=self.registration.allow_critical_actions,
            ),
            novelty=NoveltyTracker(),
            hypotheses=self.hypotheses,
            causal=CausalEngine(),
            effects=EffectExtractor(),
            preconditions=PreconditionLearner(),
            recovery=self.recovery,
            executor=self.executor,
            approvals=self.approvals,
            ledger=self.ledger,
            assertions=self.assertions,
            confidence=self.confidence,
            network_guard=self.network_guard,
            on_event=self._handle_event,
            storage=self.repository,
        )

    async def _handle_event(self, event: dict[str, Any]) -> None:
        try:
            self.repository.save_event(event.get("type", "event"), event)
        except Exception:  # noqa: BLE001 - event logging must never break a run
            log.debug("could not persist event", exc_info=True)
        if self.on_event is not None:
            result = self.on_event(event)
            if asyncio.iscoroutine(result):
                await result

    async def close(self) -> None:
        self.running = False
        await self.manager.close()
        self.database.close()

    # -- exploration -------------------------------------------------------
    def default_exploration_config(self, **overrides: Any) -> ExplorationConfig:
        budget = ExplorationBudget(
            max_actions=int(overrides.pop("max_actions", self.registration.max_steps)),
            max_duration_seconds=float(
                overrides.pop("max_duration_seconds", self.registration.max_duration_seconds)
            ),
            max_states=int(overrides.pop("max_states", 80)),
            max_transitions=int(overrides.pop("max_transitions", 200)),
        )
        config = ExplorationConfig(
            budget=budget,
            mode=self.registration.mode,
            seed=self.settings.seed,
            capture_screenshots=self.settings.capture_screenshots,
            weights=FingerprintWeights(**overrides.pop("weights", {})),
            thresholds=SimilarityThresholds(**overrides.pop("thresholds", {})),
            configuration_label=str(overrides.pop("configuration_label", "default")),
        )
        for key, value in overrides.items():
            if hasattr(config, key):
                setattr(config, key, value)
        return config

    async def explore(self, config: ExplorationConfig | None = None, *, url: str | None = None) -> ExplorationResult:
        if not self.running:
            await self.start()
        assert self.explorer is not None
        result = await self.explorer.run(url=url or self.registration.base_url, config=config)
        self.mine_workflows()
        self._persist_model_state()
        return result

    def mine_workflows(self, *, save: bool = True, replace: bool = True) -> list[Any]:
        """Extract parameterized workflows from verified transitions."""
        if replace:
            self.graph.workflows = {}
            if save:
                self.repository.clear_workflows()
        miner = WorkflowMiner()
        workflows = miner.mine(self.graph)
        for workflow in workflows:
            self.graph.upsert_workflow(workflow)
            if save:
                self.repository.save_workflow(workflow)
        return workflows

    async def name_workflows_with_llm(self) -> int:
        """Optional: let the provider improve workflow names, never their steps.

        Only the human-facing name, goal and description may change; the steps,
        parameters and evidence are exactly what was verified in the browser.
        """
        if not self.llm.available:
            return 0
        renamed = 0
        for workflow in list(self.graph.workflows.values()):
            steps = "\n".join(step.describe() for step in workflow.steps)
            summary = await self.llm.summarize_workflow(
                steps=steps,
                end_state=workflow.expected_end_state,
                parameters=", ".join(parameter.name for parameter in workflow.parameters),
            )
            if summary is None:
                continue
            workflow.name = summary.name[:60] or workflow.name
            workflow.goal = summary.goal[:60] or workflow.goal
            workflow.confidence = workflow.confidence
            self.repository.save_workflow(workflow)
            renamed += 1
        return renamed

    async def _current_state_id(self) -> tuple[str | None, Any]:
        """Observe the live page and match it to a known state."""
        page = self.manager.page
        observation = await self.observer.observe(page, settle=True)
        elements = self.detector.detect(observation)
        observation.interactive_elements = elements
        labeler = SemanticLabeler()
        identity = identity_for(observation)
        candidate = build_state(
            observation,
            page_identity=identity.url_pattern,
            page_id=identity.page_id,
            semantic_summary=labeler.observation_summary(observation),
        )
        assign_page(candidate, identity)
        known, _ = match_state(candidate, list(self.graph.states.values()))
        return (known.state_id if known else None), observation

    async def run_task(
        self,
        task_text: str,
        *,
        predicates: list[Any] | None = None,
        allow_exploration: bool = True,
        max_exploration_actions: int = 20,
        use_workflows: bool = True,
        use_transitions: bool = True,
    ) -> TaskResult:
        """Parse, plan, execute and verify one task against the learned model."""
        import time

        started = time.monotonic()
        if not self.running:
            await self.start()
        if self.manager.page:
            current_url = await self.manager.page.url()
            if not current_url or current_url.startswith("about:"):
                await self.manager.page.navigate(self.registration.base_url)
        parser = TaskParser()
        task = parser.parse(task_text)
        if predicates:
            task.predicates = [
                {"kind": p.kind.value, "value": p.value, "target": p.target} for p in predicates
            ]
        planner = Planner(parser=parser, use_workflows=use_workflows, use_transitions=use_transitions)
        replanner = Replanner(planner)
        verifier = Verifier()
        result = TaskResult(task=task.to_dict())
        result.model_states_before = len(self.graph.states)

        current_state_id, observation = await self._current_state_id()
        result.current_state_matched = current_state_id is not None
        planning_started = time.perf_counter()
        plan = planner.plan(task, self.graph, current_state_id)
        result.planning_latency_ms = (time.perf_counter() - planning_started) * 1000.0
        result.notes.extend(plan.notes)

        if not plan.usable and allow_exploration:
            used = await self._targeted_exploration(task, observation, max_exploration_actions)
            result.used_exploration = used > 0
            result.actions_executed += used
            if used:
                self.mine_workflows()
                current_state_id, observation = await self._current_state_id()
                planning_started = time.perf_counter()
                plan = planner.plan(task, self.graph, current_state_id)
                result.planning_latency_ms += (time.perf_counter() - planning_started) * 1000.0
                result.notes.append(f"re-planned after {used} targeted exploration action(s)")
                result.notes.extend(plan.notes)

        result.plan = plan.to_dict()
        result.plan_source = plan.source
        result.used_workflow = plan.workflow_id
        if not plan.usable:
            result.notes.append("no usable plan: the model does not yet know a route for this task")
            result.success = False
            analysis = await self._explain_failure_with_llm(task, "no plan was available")
            if analysis:
                result.notes.append(f"model analysis: {analysis}")
            result.duration_seconds = time.monotonic() - started
            result.model_states_after = len(self.graph.states)
            await self._record_task_experiment(task, result)
            return result

        step_verifications: list[StepVerification] = []
        excluded_steps: set[str] = set()
        index = 0
        while index < len(plan.steps):
            step = plan.steps[index]
            action_result = await self.executor.execute(self.manager.page, step.action)
            result.actions_executed += 1
            after = await self.observer.observe(self.manager.page)
            elements = self.detector.detect(after)
            after.interactive_elements = elements
            diff = diff_states(observation, after)
            verification = verifier.verify_step(
                step,
                action_result=action_result,
                diff=diff,
                observation=after,
                transition_id=step.transition_id,
            )
            step_verifications.append(verification)
            if verification.no_effect:
                result.unnecessary_actions += 1
            if step.target_state:
                observed_state = self._match_observation_state(after)
                if observed_state == step.target_state:
                    result.transition_predictions_hit += 1
                else:
                    result.transition_predictions_missed += 1
            result.step_results.append(
                {
                    **verification.to_dict(),
                    "action": step.action.describe(),
                    "status": action_result.status,
                    "duration_ms": round(action_result.duration_ms, 1),
                    "locator": action_result.locator_used,
                    "error": action_result.error,
                }
            )

            evidence = Evidence(
                kind=EvidenceKind.ACTION_RESULT if action_result.ok else EvidenceKind.FAILURE,
                action_id=step.action.action_id,
                transition_id=step.transition_id,
                source_state=current_state_id,
                target_state=verification.transition_id,
                effects=[],
                observations=[observation.observation_id, after.observation_id],
                message=f"task step {step.index}: {step.action.describe()} -> "
                + ("verified" if verification.verified else (action_result.error or "no predicted effect")),
                notes=[f"task:{task.task_id}"],
            ).finalize()
            self.graph.add_evidence(evidence)
            self.repository.save_evidence(evidence)

            if step.transition_id and step.transition_id in self.graph.transitions:
                self.graph.record_execution(
                    step.transition_id, success=verification.verified, evidence_id=evidence.evidence_id
                )
                self.repository.save_transition(self.graph.transitions[step.transition_id])

            if verification.verified:
                observation = after
                index += 1
                continue

            result.recoveries += 1
            decision = replanner.decide(
                task=task,
                plan=plan,
                graph=self.graph,
                current_state_id=current_state_id,
                failed_step=step,
                failure_reason=action_result.error or "step produced no predicted effect",
                step_attempts=1,
                excluded_transitions=set(excluded_steps),
            )
            result.replans = replanner.replans
            result.notes.append(f"step {step.index} recovery: {decision.kind.value} - {decision.reason}")
