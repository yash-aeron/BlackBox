"""Verification during task execution.

Every meaningful step is checked against the effect the model predicted, and the
task as a whole is checked against machine-readable success conditions.  A step
that silently did nothing is a failure, not a success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..execution.assertions import Predicate, PredicateResult, verify_effects
from ..model.action import ActionResult, EffectKind
from ..model.state import WebsiteState
from ..observation.observation import Observation
from ..perception.state_fingerprint import StateDiff
from .planner import Plan, PlanStep
from .task_parser import TaskSpec


@dataclass
class StepVerification:
    step_index: int
    verified: bool
    expected: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    no_effect: bool = False
    notes: list[str] = field(default_factory=list)
    transition_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "verified": self.verified,
            "expected": self.expected,
            "matched": self.matched,
            "missed": self.missed,
            "no_effect": self.no_effect,
            "notes": self.notes,
            "transition_id": self.transition_id,
        }


@dataclass
class TaskVerification:
    success: bool
    predicates: list[PredicateResult] = field(default_factory=list)
    steps_verified: int = 0
    steps_total: int = 0
    final_state_id: str = ""
    notes: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "steps_verified": self.steps_verified,
            "steps_total": self.steps_total,
            "final_state_id": self.final_state_id,
            "confidence": self.confidence,
            "notes": self.notes,
            "predicates": [
                {"kind": r.predicate.kind.value, "value": r.predicate.value, "satisfied": r.satisfied, "evidence": r.evidence}
                for r in self.predicates
            ],
        }


class Verifier:
    """Checks steps and tasks against the model's expectations."""

    def verify_step(
        self,
        step: PlanStep,
        *,
        action_result: ActionResult,
        diff: StateDiff,
        observation: Observation,
        transition_id: str | None = None,
    ) -> StepVerification:
        expected = []
        for value in step.expected_effect:
            try:
                expected.append(EffectKind(value))
            except ValueError:
                continue
        if not action_result.ok:
            return StepVerification(
                step_index=step.index,
                verified=False,
                expected=[e.value for e in expected],
                notes=[f"action did not complete: {action_result.error or action_result.blocked_reason}"],
                transition_id=transition_id,
            )
        result = verify_effects(step.action, expected, diff, observation)
        return StepVerification(
            step_index=step.index,
            verified=result.verified,
            expected=[e.value for e in expected],
            matched=result.matched,
            missed=result.missed,
            no_effect=result.no_effect,
            notes=result.notes,
            transition_id=transition_id,
        )

    def verify_task(
        self,
        task: TaskSpec,
        plan: Plan,
        *,
        observation: Observation,
        state: WebsiteState | None,
        step_results: list[StepVerification],
        predicates: list[Predicate],
        assertion_engine: Any,
    ) -> TaskVerification:
        results: list[PredicateResult] = []
        if predicates:
            _, results = assertion_engine.evaluate_all(predicates, observation, state)
        satisfied = all(r.satisfied for r in results) if results else False
        verified_steps = sum(1 for step in step_results if step.verified)

        notes: list[str] = []
        if not predicates:
            notes.append("no machine-checkable success condition was provided for this task")
            satisfied = bool(step_results) and verified_steps == len(step_results)
        if step_results and verified_steps < len(step_results):
            notes.append(f"{len(step_results) - verified_steps} step(s) did not produce their predicted effect")
        confidence = 0.0
        if step_results:
            confidence = round(verified_steps / len(step_results), 3)
        if results:
            confidence = round((confidence + (1.0 if satisfied else 0.0)) / 2, 3)

        return TaskVerification(
            success=satisfied,
            predicates=results,
            steps_verified=verified_steps,
            steps_total=len(step_results),
            final_state_id=state.state_id if state else "",
            notes=notes,
            confidence=confidence,
        )
