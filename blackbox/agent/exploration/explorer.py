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
from .action_ranker import ActionRanker
from .experiment import BudgetState, ExperimentLedger, ExperimentRecord, ExplorationBudget
from .novelty import NoveltyTracker
from .safety import ApprovalBroker, ApprovalRequest, ExecutionMode, SafetyClassifier, SafetyPolicy

log = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], Awaitable[None] | None]


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
            budget=config.budget,
            configuration=config.to_dict(),
        )
        self.ledger.add(record)
        result = ExplorationResult(experiment_id=record.experiment_id, target_id=self.target_id)
        started = time.monotonic()

        page = self.manager.page
        start_url = url or self.base_url
        if page is None or not self.manager.alive():
            page = await self.manager.start()

        already_there = False
        if config.resume and self.graph.states:
            current_url = await page.url()
            for state in self.graph.states.values():
                if state.fingerprint.url_key and state.fingerprint.url_key in current_url:
                    already_there = True
                    break
        if not already_there:
            try:
                await page.navigate(start_url)
            except Exception as exc:  # noqa: BLE001 - report and stop cleanly
                result.stop_reason = f"could not open {start_url}: {exc}"
                record.finish(status="failed", metrics=result.to_dict())
                return result

        try:
            while True:
                exhausted = budget.exhausted()
                if exhausted:
                    result.stop_reason = exhausted
                    break

                state, observation = await self._observe_and_register(page, budget, result)
                self.current_state_id = state.state_id

                candidates = self._candidates(state, observation)
                if not candidates:
                    result.stop_reason = "no candidate actions remain in the current state"
                    break

                risks = {
                    action.action_id: self.safety.classify(
                        action,
                        element=self._element_for(action, observation),
                        observation=observation,
                        history=[entry[1] for entry in self.recent_actions],
                    )
                    for action in candidates
                }
                hypothesis_actions = {
                    action.action_id for action in candidates if action.origin == "hypothesis"
                }
                ranked = self.ranker.rank(
                    candidates,
                    state=state,
                    novelty=self.novelty,
                    risks=risks,
                    hypothesis_action_ids=hypothesis_actions,
                    element_lookup={e.element_id: e for e in observation.interactive_elements},
                )
                await self._emit(
                    "ranking",
                    {
                        "state_id": state.state_id,
                        "top": [self.ranker.explain(item) for item in ranked[:5]],
                    },
                )

                chosen = await self._select_action(ranked, observation, risks, result)
                if chosen is None:
                    if all(self.novelty.is_repeat(state.state_id, item.action) for item in ranked):
                        result.stop_reason = "all candidate actions exhausted in reachable states"
                        break
                    continue

                outcome = await self._execute_and_learn(page, state, observation, chosen, budget, result, risks)
                budget.actions += 1
                if config.pause_between_actions_ms:
                    await asyncio.sleep(config.pause_between_actions_ms / 1000.0)
                self.recent_actions.append((state.state_id, chosen.action.signature()))
                self.recent_actions = self.recent_actions[-12:]

                if outcome == "no_progress":
                    self._actions_without_new_state = getattr(self, "_actions_without_new_state", 0) + 1
                else:
                    self._actions_without_new_state = 0
                if getattr(self, "_actions_without_new_state", 0) >= config.max_actions_without_new_state:
                    result.stop_reason = (
                        "no new states or verified transitions in the last "
                        f"{config.max_actions_without_new_state} actions"
                    )
                    break
                if self.stopped:
                    result.stop_reason = result.stop_reason or "stopped by operator"
                    break
                if self.novelty.stuck(self.recent_actions):
                    result.stop_reason = "exploration loop detected (repeating known states and actions)"
                    break
        except OriginViolation as exc:
            result.stop_reason = f"origin boundary reached: {exc}"
            await self._emit("origin_blocked", {"reason": str(exc)})
        except Exception as exc:  # noqa: BLE001 - never lose the model on a crash
            log.exception("exploration failed")
            result.stop_reason = f"error: {exc}"
        finally:
            result.duration_seconds = time.monotonic() - started
            if self.action_latencies:
                result.mean_action_ms = sum(self.action_latencies) / len(self.action_latencies)
            if self.observation_latencies:
                result.mean_observation_ms = sum(self.observation_latencies) / len(self.observation_latencies)
            result.recoveries = self.recovery.recoveries
            result.hypotheses_proposed = len(self.hypotheses.hypotheses)
            result.hypotheses_verified = sum(
                1 for h in self.hypotheses.hypotheses.values() if h.status is HypothesisStatus.VERIFIED
            )
            result.metrics.update(
                {
                    "novelty": self.novelty.summary(),
                    "hypotheses": self.hypotheses.summary(),
                    "causal": self.causal.summary(),
                    "budget": budget.snapshot(),
                    "sandbox": self.manager.sandbox.snapshot(),
                    "network_guard": self.network_guard.summary() if self.network_guard else None,
                }
            )
            record.finish(
                status="completed" if not result.stop_reason.startswith("error") else "failed",
                metrics=result.to_dict(),
            )
            if self.storage is not None:
                try:
                    self.storage.save_experiment(record)
                except Exception:  # noqa: BLE001 - persistence failure must not mask results
                    log.exception("could not persist experiment record")
            await self._emit("exploration_finished", result.to_dict())
        return result

    def stop(self) -> None:
        self.stopped = True

    # -- observation -------------------------------------------------------
    async def _observe_and_register(
        self, page, budget: BudgetState, result: ExplorationResult
    ) -> tuple[WebsiteState, Observation]:
        timer = Timer()
        observation = await self.observer.observe(page, settle=True)
        self.observation_latencies.append(timer.elapsed_ms)
        self.last_observation = observation

        elements = self.detector.detect(observation)
        observation.interactive_elements = elements
        self.last_elements = elements

        summary = self.labeler.observation_summary(observation)
        identity = identity_for(observation)
        candidate = build_state(
            observation,
            page_identity=identity.url_pattern,
            page_id=identity.page_id,
            semantic_summary=summary,
            session=SessionContext(
                authenticated=any("session" in name.lower() or "auth" in name.lower() for name in observation.browser_state.cookie_names),
                cookie_names=observation.browser_state.cookie_names[:10],
            ),
        )
        assign_page(candidate, identity)

        known, similarity = match_state(
            candidate,
            list(self.graph.states.values()),
            weights=self.config_weights,
            thresholds=self.config_thresholds,
        )
        if known is None:
            state, created = self.graph.upsert_state(candidate)
            if created:
                result.new_states += 1
                page_record = to_page(identity, state.state_id, summary=summary)
                self.graph.upsert_page(page_record)
                if self.storage is not None:
                    self.storage.save_state(state, similarity=similarity)
                await self._emit(
                    "new_state",
                    {
                        "state": {
                            "state_id": state.state_id,
                            "label": state.label(),
                            "url": state.url,
                            "elements": len(state.visible_elements),
                            "summary": state.semantic_summary,
                            "similarity": similarity.total if similarity else None,
                        }
                    },
                )
                # New page layout: propose hypotheses about disabled controls.
                for hypothesis in self.hypotheses.propose_requirements(observation, elements):
                    await self._emit("hypothesis", {"hypothesis": hypothesis.__dict__, "status": hypothesis.status.value})
        else:
            state = known
            state.visit_count += 1
            state.last_seen = candidate.last_seen
            state.observation_id = candidate.observation_id
            if candidate.screenshot_ref:
                state.screenshot_ref = candidate.screenshot_ref

        if self.storage is not None:
            self.storage.save_observation_meta(observation, state.state_id)

        budget.states = len(self.graph.states)
        visits = self.novelty.note_state(candidate.fingerprint)
        if visits > self.novelty.max_same_state_visits:
            log.debug("state %s visited %d times", state.state_id, visits)
        return state, observation

    @property
    def config_weights(self) -> FingerprintWeights:
        return getattr(self, "_weights", FingerprintWeights())

    @property
    def config_thresholds(self) -> SimilarityThresholds:
        return getattr(self, "_thresholds", SimilarityThresholds())

    def set_similarity_config(self, weights: FingerprintWeights, thresholds: SimilarityThresholds) -> None:
        self._weights = weights
        self._thresholds = thresholds

    # -- candidate generation ---------------------------------------------
    def _candidates(self, state: WebsiteState, observation: Observation) -> list[Action]:
        actions = self.generator.generate(
            observation, elements=observation.interactive_elements, state_id=state.state_id
        )
        # Hypothesis-driven experiments first: they are the point of the loop.
        for hypothesis in self.hypotheses.all():
            if hypothesis.status in (HypothesisStatus.REFUTED, HypothesisStatus.STALE):
                continue
            if (hypothesis.details or {}).get("observation_id") not in (None, observation.observation_id):
                continue
            for action in self.generator.for_hypothesis(hypothesis, observation, observation.interactive_elements):
                if all(existing.signature() != action.signature() for existing in actions):
                    actions.append(action)
        return actions

    def _element_for(self, action: Action, observation: Observation):
        if not action.target:
            return None
        return next(
            (e for e in observation.interactive_elements if e.element_id == action.target.element_id),
            None,
        )

    async def _element_provider(self, element_id: str):
        """Re-observe and return a fresh element (locator regeneration)."""
        try:
            page = self.manager.page
            observation = await self.observer.observe(page, settle=False)
            elements = self.detector.detect(observation)
            observation.interactive_elements = elements
            self.last_elements = elements
            return next((e for e in elements if e.element_id == element_id), None)
        except Exception:  # noqa: BLE001
            return None

    # -- safety gate -------------------------------------------------------
    async def _select_action(
        self,
        ranked: list[Any],
        observation: Observation,
        risks: dict[str, Any],
        result: ExplorationResult,
    ):
        for item in ranked:
            assessment = risks.get(item.action.action_id)
            risk = assessment.risk if assessment else RiskLevel.LOW
            if self.policy.permits(risk):
                return item
            result.actions_blocked += 1
            if self.policy.mode is ExecutionMode.SAFE:
                await self._emit(
                    "action_blocked",
                    {
                        "action": item.action.describe(),
                        "risk": risk.value,
                        "reason": "SAFE mode permits LOW risk only",
                        "reasons": assessment.reasons if assessment else [],
                    },
                )
                continue
            element = self._element_for(item.action, observation)
            request = ApprovalRequest(
                request_id="",
                action=item.action.describe(),
                target=item.action.target.label() if item.action.target else "page",
                reason="; ".join((assessment.reasons if assessment else [])[:3]),
                expected_effect=", ".join(e.value for e in item.action.expected_effect[:3]),
                risk=risk.value,
                evidence=[observation.observation_id],
            )
            result.approvals_requested += 1
            await self._emit(
                "approval_requested",
                {
                    "action": request.action,
                    "target": request.target,
                    "risk": request.risk,
                    "reason": request.reason,
                    "expected_effect": request.expected_effect,
                },
            )
            granted = await self.approvals.request(request)
            evidence = Evidence(
                kind=EvidenceKind.HUMAN_DECISION,
                message=f"{'approved' if granted else 'rejected'} {request.action} ({request.risk})",
                notes=[request.request_id],
            ).finalize()
            self.graph.add_evidence(evidence)
            if granted:
                result.approvals_granted += 1
                return item
            await self._emit(
                "action_denied",
                {"action": item.action.describe(), "risk": risk.value, "reason": "not approved"},
            )
        return None

    # -- execute, compare, learn ------------------------------------------
    async def _execute_and_learn(
        self,
        page,
        source_state: WebsiteState,
        before: Observation,
        ranked_item: Any,
        budget: BudgetState,
        result: ExplorationResult,
        risks: dict[str, Any],
    ) -> str:
        action: Action = ranked_item.action
        element = self._element_for(action, before)
        await self._emit(
            "action_proposed",
            {
                "action": action.describe(),
                "action_type": action.type.value,
                "target": action.target.label() if action.target else "page",
                "risk": ranked_item.risk.value,
                "confidence": action.confidence,
                "rationale": action.rationale,
                "score": ranked_item.score,
                "reasons": ranked_item.reasons,
                "state_id": source_state.state_id,
            },
        )

        timer = Timer()
        action_result: ActionResult = await self.executor.execute(page, action, element=element)
        self.action_latencies.append(timer.elapsed_ms)
        result.actions_executed += 1

        if not action_result.ok:
            failure = self.recovery.classify(action_result, error=action_result.error)
            decision = self.recovery.decide(
                failure,
                attempt=action_result.attempts,
                replan_available=True,
                known_state_available=bool(self.graph.states),
            )
            self.recovery.record(decision, action_id=action.action_id, detail=str(action_result.error))
            evidence = Evidence(
                kind=EvidenceKind.FAILURE,
                experiment_id=result.experiment_id,
                action_id=action.action_id,
                source_state=source_state.state_id,
                message=f"{action.describe()}: {action_result.error or action_result.blocked_reason}",
                notes=[decision.strategy.value],
            ).finalize()
            self.graph.add_evidence(evidence)
            result.actions_failed += 1
            self.novelty.note_action(source_state.state_id, action, effects=0)
            await self._emit(
                "action_result",
                {
                    "action": action.describe(),
                    "status": action_result.status,
                    "error": action_result.error,
                    "attempts": action_result.attempts,
                    "recovery": decision.strategy.value,
                },
            )
            if decision.strategy is RecoveryStrategy.RESTART_BROWSER and budget.browser_restarts <= budget.budget.max_browser_restarts:
                budget.browser_restarts += 1
                await self.manager.restart(resume_url=source_state.url)
            return "failed"

        after = await self.observer.observe(page)
        elements = self.detector.detect(after)
        after.interactive_elements = elements
        self.last_elements = elements
        await self._check_origin(page, source_state, result)

        diff = diff_states(before, after)
        attribution = self.causal.attribute(action, diff, after, source_state_id=source_state.state_id)
        effects = self.effects.extract(action, diff, after, attribution)
        verification = verify_effects(action, action.expected_effect, diff, after)

        target_state, created = self._register_target_state(after, diff, source_state)
        if created:
            result.new_states += 1

        transition = Transition(
            source_state=source_state.state_id,
            action=action,
            target_state=target_state.state_id,
            confidence=0.5,
        ).finalize()
        self.effects.apply(transition, effects)
        evidence = Evidence(
            kind=EvidenceKind.ACTION_RESULT,
            experiment_id=result.experiment_id,
            step_index=budget.actions,
            action_id=action.action_id,
            source_state=source_state.state_id,
            target_state=target_state.state_id,
            transition_id=transition.transition_id,
            effects=effects,
            observations=[before.observation_id, after.observation_id],
            screenshot_refs=[ref for ref in [before.screenshot_reference, after.screenshot_reference] if ref],
            message=describe_effects(effects),
        ).finalize()
        self.graph.add_evidence(evidence)

        if verification.verified:
            transition.record_success(evidence.evidence_id)
            result.verified_transitions += 1
        else:
            transition.record_failure(evidence.evidence_id)
            transition.status = TransitionStatus.PROPOSED if diff.changed else TransitionStatus.REFUTED if not diff.changed else transition.status

        existing = self.graph.transitions.get(transition.transition_id)
        stored = self.graph.add_transition(transition)
        if existing is None:
            result.new_transitions += 1
            if verification.verified:
                stored.record_success(evidence.evidence_id)
        else:
            if verification.verified:
                stored.record_success(evidence.evidence_id)
            else:
                stored.record_failure(evidence.evidence_id)
        self.graph.record_execution(
            stored.transition_id, success=verification.verified, evidence_id=evidence.evidence_id
        )

        self.novelty.note_action(
            source_state.state_id, action, target_state=target_state.state_id, effects=len([e for e in effects if e.kind is not EffectKind.NO_EFFECT])
        )
        self.novelty.note_transition(source_state.state_id, action, target_state.state_id)

        # Preconditions and hypotheses from what the browser just told us.
        learned = self.preconditions.from_effects(action, effects, after, source_state.state_id)
        for constraint in learned:
            stored_constraint = self.graph.upsert_constraint(constraint)
            stored_constraint.support(evidence.evidence_id)
            if self.storage is not None:
                self.storage.save_constraint(stored_constraint)
            await self._emit("constraint_learned", {"constraint": stored_constraint.model_dump(mode="json")})
        for hypothesis in self.hypotheses.propose_validation(after, action_label=action.describe()):
            await self._emit("hypothesis", {"hypothesis": hypothesis.__dict__, "status": hypothesis.status.value})
        await self._resolve_hypotheses(action, before, after, effects, evidence.evidence_id)

        if self.storage is not None:
            self.storage.save_transition(stored)
            self.storage.save_evidence(evidence)

        budget.transitions = len(self.graph.transitions)
        await self._emit(
            "action_result",
            {
                "action": action.describe(),
                "status": "OK",
                "locator": action_result.locator_used,
                "duration_ms": round(action_result.duration_ms, 1),
                "verified": verification.verified,
                "effects": [effect.model_dump(mode="json") for effect in effects[:6]],
                "diff": diff.to_dict(),
                "transition": {
                    "transition_id": stored.transition_id,
                    "source": stored.source_state,
                    "target": stored.target_state,
                    "status": stored.status.value,
                    "confidence": stored.confidence,
                },
                "similarity": None,
            },
        )
        return "progress" if (created or (existing is None and verification.verified)) else "no_progress"

    def _register_target_state(self, after: Observation, diff, source_state: WebsiteState) -> tuple[WebsiteState, bool]:
        summary = self.labeler.observation_summary(after)
        identity = identity_for(after)
        candidate = build_state(
            after,
            page_identity=identity.url_pattern,
            page_id=identity.page_id,
            semantic_summary=summary,
        )
        assign_page(candidate, identity)
        known, similarity = match_state(
            candidate,
            list(self.graph.states.values()),
            weights=self.config_weights,
            thresholds=self.config_thresholds,
        )
        if known is not None:
            known.visit_count += 1
            known.last_seen = candidate.last_seen
            return known, False
        state, created = self.graph.upsert_state(candidate)
        if created:
            self.graph.upsert_page(to_page(identity, state.state_id, summary=summary))
            if self.storage is not None:
                self.storage.save_state(state, similarity=similarity)
        return state, created

    async def _resolve_hypotheses(
        self, action: Action, before: Observation, after: Observation, effects: list[Any], evidence_id: str
    ) -> None:
        enabled_change = {
            change for change in (e.detail for e in effects if e.kind in (EffectKind.ELEMENTS_CHANGED, EffectKind.ELEMENTS_ADDED))
        }
        for hypothesis in self.hypotheses.all():
            if hypothesis.status in (HypothesisStatus.VERIFIED, HypothesisStatus.REFUTED, HypothesisStatus.STALE):
                continue
            prediction = hypothesis.prediction or {}
            if prediction.get("field_id") and action.target and action.target.element_id == prediction["field_id"]:
                enables = prediction.get("enables_label", "")
                control = next(
                    (e for e in after.interactive_elements if enables and enables.lower() in e.label().lower()), None
                )
                observed = {"element_id": control.element_id if control else "", "enabled": control.enabled if control else False}
                outcome = self.hypotheses.resolve_prediction(hypothesis, observed)
                if outcome is True:
                    hypothesis.support("field-fill", evidence_id, decisive=True)
                    self._constraint_from_hypothesis(hypothesis, evidence_id)
                elif outcome is False:
                    hypothesis.contradict("field-fill", evidence_id)
                await self._emit(
                    "hypothesis",
                    {
                        "hypothesis": hypothesis.__dict__,
                        "status": hypothesis.status.value,
                        "statement": hypothesis.statement,
                    },
                )
            elif prediction.get("message") and any(
                e.kind in (EffectKind.VALIDATION_MESSAGE, EffectKind.PRECONDITION_BLOCKED) for e in effects
            ):
                hypothesis.support("observed-message", evidence_id)

    def _constraint_from_hypothesis(self, hypothesis, evidence_id: str) -> None:
        from ..model.constraint import Constraint, ConstraintKind, ConstraintScope

        prediction = hypothesis.prediction or {}
        constraint = Constraint(
            kind=ConstraintKind.PRECONDITION,
            scope=ConstraintScope.ELEMENT,
            subject=prediction.get("enables_label", hypothesis.subject)[:120],
            expression=f"{prediction.get('field_label', 'field')} != empty",
            condition={
                "field": prediction.get("field_label", ""),
                "field_ids": [prediction.get("field_id", "")],
                "enables": prediction.get("enables", ""),
                "state_id": (hypothesis.details or {}).get("observation_id"),
            },
            message=hypothesis.statement[:300],
            confidence=min(0.9, hypothesis.confidence),
        ).finalize()
        self.graph.upsert_constraint(constraint).support(evidence_id, verified=True)
        if self.storage is not None:
            self.storage.save_constraint(constraint)

    async def _check_origin(self, page, source_state: WebsiteState, result: ExplorationResult) -> None:
        url = await page.url()
        decision = self.manager.sandbox.is_allowed(url)
        if decision.allowed:
            return
        evidence = Evidence(
            kind=EvidenceKind.BLOCKED_ACTION,
            source_state=source_state.state_id,
            message=f"page moved outside authorized origins: {url}",
            notes=[decision.reason],
        ).finalize()
        self.graph.add_evidence(evidence)
        await self._emit("origin_blocked", {"url": url, "reason": decision.reason})
        # Return to the last authorized state rather than following the page out.
        try:
            await page.navigate(source_state.url)
        except Exception:  # noqa: BLE001
            pass
        raise OriginViolation(url, decision.reason)
