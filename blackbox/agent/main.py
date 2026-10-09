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

            if decision.kind is ReplanKind.RETRY_STEP:
                continue
            if decision.kind in (ReplanKind.ALTERNATE_ROUTE, ReplanKind.TARGETED_EXPLORATION):
                if step.transition_id:
                    excluded_steps.add(step.transition_id)
                if decision.kind is ReplanKind.TARGETED_EXPLORATION and allow_exploration:
                    used = await self._targeted_exploration(task, after, decision.max_additional_actions)
                    result.actions_executed += used
                    result.used_exploration = result.used_exploration or used > 0
                    if used:
                        self.mine_workflows()
                current_state_id, observation = await self._current_state_id()
                replanned = planner.plan(task, self.graph, current_state_id)
                if replanned.usable:
                    plan = replanned
                    result.plan = plan.to_dict()
                    result.plan_source = plan.source
                    result.used_workflow = plan.workflow_id or result.used_workflow
                    step_verifications = []
                    index = 0
                    result.notes.append("re-planned along an alternative route")
                    continue
            result.notes.append("no viable route remains for this task")
            break

        # Re-observe the settled final state before judging the task: an
        # observation taken the instant a button was clicked can catch a
        # mid-render frame that the user never sees.
        observation = await self.observer.observe(self.manager.page, settle=True)
        observation.interactive_elements = self.detector.detect(observation)
        current_state_id = self._match_observation_state(observation) or current_state_id
        final_success, predicate_results = self.assertions.evaluate_all(
            list(predicates or []), observation, self.graph.states.get(current_state_id or "")
        )
        verification = verifier.verify_task(
            task,
            plan,
            observation=observation,
            state=self.graph.states.get(current_state_id or ""),
            step_results=step_verifications,
            predicates=list(predicates or []),
            assertion_engine=self.assertions,
        )
        if predicates:
            verification.success = final_success
        result.success = verification.success
        result.verification = verification.to_dict()
        result.steps_total = verification.steps_total
        result.steps_verified = verification.steps_verified
        result.notes.extend(verification.notes)
        result.duration_seconds = time.monotonic() - started
        result.model_states_after = len(self.graph.states)

        if result.success and plan.workflow_id and plan.workflow_id in self.graph.workflows:
            workflow = self.graph.workflows[plan.workflow_id]
            workflow.record_verification()
            self.repository.save_workflow(workflow)

        self._persist_model_state()
        await self._record_task_experiment(task, result)
        return result

    async def _explain_failure_with_llm(self, task: TaskSpec, result_summary: str) -> str | None:
        """Optional: ask the provider to explain a failure. Advisory text only."""
        if not self.llm.available:
            return None
        analysis = await self.llm.analyze_failure(
            action=task.goal,
            result=result_summary[:400],
            effects="",
            alerts="",
            hypotheses="; ".join(h.statement[:80] for h in self.hypotheses.all()[:5]),
        )
        if analysis is None:
            return None
        return f"[{analysis.failure_kind} c={analysis.confidence:.2f}] {analysis.explanation[:220]}"

    def _match_observation_state(self, observation: Any) -> str | None:
        """Which known state does this observation correspond to?"""
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
        return known.state_id if known else None

    async def _targeted_exploration(self, task: TaskSpec, observation: Any, max_actions: int) -> int:
        """Explore only the region relevant to the missing knowledge."""
        if self.explorer is None:
            return 0
        config = self.default_exploration_config(
            max_actions=max(4, max_actions),
            max_duration_seconds=120,
            max_states=25,
            configuration_label=f"targeted:{task.goal}",
        )
        config.resume = True
        self.explorer.stopped = False
        result = await self.explorer.run(url=None, config=config)
        return max(0, result.actions_executed)

    def _exploration_hint_for(self, task: TaskSpec) -> dict[str, Any]:
        keywords = [keyword for keyword in [task.entity_noun, task.verb] if keyword]
        region = []
        for state in self.graph.states.values():
            haystack = f"{state.title} {state.page_identity} {state.semantic_summary}".lower()
            if any(keyword in haystack for keyword in keywords):
                region.append({"state_id": state.state_id, "label": state.label(), "url": state.url})
        return {"keywords": keywords, "region": region[:5], "max_actions": 15}

    async def _record_task_experiment(self, task: TaskSpec, result: TaskResult) -> None:
        record = ExperimentRecord(
            target_id=self.registration.target_id,
            kind="task",
            seed=self.settings.seed,
            browser_version=self.manager.browser_version,
            model_version=self.repository.model_version,
            configuration={"task": task.to_dict()},
        )
        record.finish(status="completed" if result.success else "failed", metrics=result.to_dict())
        self.ledger.add(record)
        self.repository.save_experiment(record)

    def _persist_model_state(self) -> None:
        for state in self.graph.states.values():
            self.repository.save_state(state)
        for transition in self.graph.transitions.values():
            self.repository.save_transition(transition)
        for workflow in self.graph.workflows.values():
            self.repository.save_workflow(workflow)
        for constraint in self.graph.constraints.values():
            self.repository.save_constraint(constraint)
        for hypothesis in self.hypotheses.all():
            self.repository.save_hypothesis(
                HypothesisRecord(
                    hypothesis_id=hypothesis.hypothesis_id,
                    statement=hypothesis.statement,
                    status=hypothesis.status.value,
                    confidence=hypothesis.confidence,
                    supporting_experiments=hypothesis.supporting_experiments,
                    contradicting_experiments=hypothesis.contradicting_experiments,
                    evidence=hypothesis.evidence,
                    created_at=hypothesis.created_at,
                    updated_at=hypothesis.updated_at,
                    details={**hypothesis.details, "kind": hypothesis.kind.value},
                )
            )

    # -- model handling ----------------------------------------------------
    def export_model(self, path: str | Path) -> Path:
        self._persist_model_state()
        return self.repository.export_model(path)

    def import_model(self, path: str | Path) -> None:
        self.repository.import_model(path)
        self.graph = self.repository.load_graph()
        if self.explorer is not None:
            self.explorer.graph = self.graph

    def status(self) -> AgentStatus:
        return AgentStatus(
            target_id=self.registration.target_id,
            base_url=self.registration.base_url,
            mode=self.registration.mode.value,
            browser=self.manager.health_report(),
            model=self.repository.stats(),
            sandbox=self.sandbox.snapshot(),
            policy={
                "allow_medium_actions": self.registration.allow_medium_actions,
                "allow_high_actions": self.registration.allow_high_actions,
                "allow_critical_actions": self.registration.allow_critical_actions,
                "approvals": self.approvals.summary(),
            },
            settings=self.settings.describe(),
            llm=self.llm.describe(),
        )

    def graph_json(self) -> dict[str, Any]:
        return self.graph.to_graph_json()


# -- CLI -------------------------------------------------------------------
def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def _cmd_explore(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.headless is not None:
        settings.headless = args.headless
    registration = demo_registration(args.target, mode=args.mode)
    if args.max_actions:
        registration.max_steps = args.max_actions
    agent = BlackBoxAgent(settings, registration)
    try:
        await agent.start()
        config = agent.default_exploration_config(
            max_actions=args.max_actions or 60,
            max_duration_seconds=args.max_seconds or 300,
        )
        result = await agent.explore(config)
        print(json.dumps(result.to_dict(), indent=2))
        graph = agent.graph_json()
        print(
            json.dumps(
                {
                    "states": graph["stats"]["states"],
                    "transitions": graph["stats"]["transitions"],
                    "verified": graph["stats"]["verified"],
                    "workflows": graph["stats"]["workflows"],
                },
                indent=2,
            )
        )
        if args.export:
            path = agent.export_model(args.export)
            print(f"model exported to {path}")
        return 0
    finally:
        await agent.close()


async def _cmd_status(args: argparse.Namespace) -> int:
    settings = Settings()
    registration = demo_registration(args.target, mode=args.mode)
    agent = BlackBoxAgent(settings, registration)
    try:
        print(json.dumps(agent.status().to_dict(), indent=2))
        return 0
    finally:
        await agent.close()


async def _cmd_task(args: argparse.Namespace) -> int:
    settings = Settings()
    if args.headless is not None:
        settings.headless = args.headless
    registration = demo_registration(args.target, mode=args.mode)
    agent = BlackBoxAgent(settings, registration)
    try:
        await agent.start()
        predicates = []
        if args.expect_text:
            from .execution.assertions import Predicate, PredicateKind

            predicates.append(Predicate(kind=PredicateKind.TEXT_PRESENT, value=args.expect_text))
        result = await agent.run_task(args.text, predicates=predicates, allow_exploration=not args.no_explore)
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.success or not args.require_success else 2
    finally:
        await agent.close()


async def _cmd_mine(args: argparse.Namespace) -> int:
    from .learning.workflow_miner import summarize_workflows

    agent = BlackBoxAgent(Settings(), demo_registration(args.target))
    try:
        workflows = agent.mine_workflows()
        print(json.dumps(summarize_workflows(workflows), indent=2, default=str))
        print(f"mined {len(workflows)} workflow(s) from model v{agent.repository.model_version}")
        return 0
    finally:
        agent.database.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blackbox", description="BlackBox black-box website learner")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    explore = sub.add_parser("explore", help="explore a registered target and learn its behavior")
    explore.add_argument("target", choices=["demo_crm", "demo_ecommerce", "demo_project_manager", "demo_forms"])
    explore.add_argument("--mode", default="AUTONOMOUS", choices=[m.value for m in ExecutionMode])
    explore.add_argument("--max-actions", type=int, default=60)
    explore.add_argument("--max-seconds", type=float, default=300)
    explore.add_argument("--headless", dest="headless", action="store_true", default=None)
    explore.add_argument("--headed", dest="headless", action="store_false")
    explore.add_argument("--export", type=str, default=None, help="write the learned model to this JSON path")

    status = sub.add_parser("status", help="show agent configuration, model and browser state")
    status.add_argument("target", choices=["demo_crm", "demo_ecommerce", "demo_project_manager", "demo_forms"])
    status.add_argument("--mode", default="AUTONOMOUS", choices=[m.value for m in ExecutionMode])

    mine = sub.add_parser("mine", help="re-mine workflows from the stored model (no browser)")
    mine.add_argument("target", choices=["demo_crm", "demo_ecommerce", "demo_project_manager", "demo_forms"])

    task = sub.add_parser("task", help="run a natural-language task against a learned model")
    task.add_argument("target", choices=["demo_crm", "demo_ecommerce", "demo_project_manager", "demo_forms"])
    task.add_argument("text", help="e.g. \"Create a customer named Yash with email yash@example.com\"")
    task.add_argument("--mode", default="AUTONOMOUS", choices=[m.value for m in ExecutionMode])
    task.add_argument("--expect-text", default=None, help="visible text that must be present on success")
    task.add_argument("--no-explore", action="store_true", help="never explore; plan only from the learned model")
    task.add_argument("--require-success", action="store_true")
    task.add_argument("--headless", dest="headless", action="store_true", default=None)
    task.add_argument("--headed", dest="headless", action="store_false")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    if args.command == "explore":
        return asyncio.run(_cmd_explore(args))
    if args.command == "status":
        return asyncio.run(_cmd_status(args))
    if args.command == "task":
        return asyncio.run(_cmd_task(args))
    if args.command == "mine":
        return asyncio.run(_cmd_mine(args))
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
