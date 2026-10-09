"""Replanning: when the learned route stops working.

Replanning is bounded and specific.  It may retry a step, take an alternative
route through the graph, or explore only the region relevant to the goal - never
restart full exploration, and never loop forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..model.base import normalize_text
from ..model.graph import ApplicationGraph
from .planner import Plan, PlanStep, Planner
from .task_parser import TaskSpec


class ReplanKind(str, Enum):
    RETRY_STEP = "RETRY_STEP"
    ALTERNATE_ROUTE = "ALTERNATE_ROUTE"
    TARGETED_EXPLORATION = "TARGETED_EXPLORATION"
    ABANDON = "ABANDON"


@dataclass
class ReplanDecision:
    kind: ReplanKind
    reason: str
    plan: Plan | None = None
    exploration_hint: dict[str, Any] = field(default_factory=dict)
    retry_step_index: int | None = None
    max_additional_actions: int = 15


class Replanner:
    """Decides how to recover a failing plan."""

    def __init__(self, planner: Planner | None = None, *, max_replans: int = 3, max_retries_per_step: int = 2) -> None:
        self.planner = planner or Planner()
        self.max_replans = max_replans
        self.max_retries_per_step = max_retries_per_step
        self.replans = 0
        self.history: list[dict[str, Any]] = []

    def decide(
        self,
        *,
        task: TaskSpec,
        plan: Plan,
        graph: ApplicationGraph,
        current_state_id: str,
        failed_step: PlanStep | None,
        failure_reason: str,
        step_attempts: int,
        excluded_transitions: set[str] | None = None,
    ) -> ReplanDecision:
        self.replans += 1
        if self.replans > self.max_replans:
            decision = ReplanDecision(
                kind=ReplanKind.ABANDON,
                reason=f"replan budget exhausted after {self.max_replans} attempts ({failure_reason})",
            )
            self._record(decision, task, failed_step)
            return decision

        if failed_step is not None and step_attempts < self.max_retries_per_step:
            decision = ReplanDecision(
                kind=ReplanKind.RETRY_STEP,
                reason=f"retrying step {failed_step.index} after: {failure_reason}",
                retry_step_index=failed_step.index,
                plan=plan,
            )
            self._record(decision, task, failed_step)
            return decision

        alternative = self._alternate_route(task, graph, current_state_id, excluded_transitions or set())
        if alternative is not None and alternative.usable:
            decision = ReplanDecision(
                kind=ReplanKind.ALTERNATE_ROUTE,
                reason=f"routing around the failed step ({failure_reason})",
                plan=alternative,
            )
            self._record(decision, task, failed_step)
            return decision

        hint = self._exploration_hint(task, graph, current_state_id, failure_reason)
        if hint.get("region"):
            decision = ReplanDecision(
                kind=ReplanKind.TARGETED_EXPLORATION,
                reason=f"no known route from here; exploring the region relevant to {goal_words(task)}",
                exploration_hint=hint,
                max_additional_actions=int(hint.get("max_actions", 15)),
            )
            self._record(decision, task, failed_step)
            return decision

        decision = ReplanDecision(
            kind=ReplanKind.ABANDON,
            reason=f"no route and no relevant region to explore: {failure_reason}",
        )
        self._record(decision, task, failed_step)
        return decision

    # -- internals ---------------------------------------------------------
    def _alternate_route(
        self, task: TaskSpec, graph: ApplicationGraph, current_state_id: str, excluded: set[str]
    ) -> Plan | None:
        if not graph.states or current_state_id not in graph.states:
            return None
        original = graph.transitions
        if excluded:
            pruned = ApplicationGraph(
                target_id=graph.target_id,
                states=graph.states,
                transitions={k: v for k, v in original.items() if k not in excluded},
                workflows=graph.workflows,
                constraints=graph.constraints,
                pages=graph.pages,
                evidence=graph.evidence,
                root_state_id=graph.root_state_id,
            )
        else:
            pruned = graph
        plan = self.planner.plan(task, pruned, current_state_id)
        return plan if plan.usable else None

    def _exploration_hint(
        self, task: TaskSpec, graph: ApplicationGraph, current_state_id: str, failure_reason: str
    ) -> dict[str, Any]:
        keywords = [word for word in goal_words(task)]
        region: list[dict[str, Any]] = []
        for state in graph.states.values():
            haystack = normalize_text(f"{state.title} {state.page_identity} {state.semantic_summary}")
            if any(keyword in haystack for keyword in keywords):
                region.append(
                    {
                        "state_id": state.state_id,
                        "label": state.label(),
                        "url": state.url,
                        "summary": state.semantic_summary[:160],
                    }
                )
        current = graph.states.get(current_state_id)
        if current is not None and not region:
            region.append(
                {
                    "state_id": current.state_id,
                    "label": current.label(),
                    "url": current.url,
                    "summary": current.semantic_summary[:160],
                }
            )
        return {
            "reason": failure_reason,
            "keywords": keywords,
            "start_state_id": current_state_id,
            "region": region[:5],
            "max_actions": 15,
        }

    def _record(self, decision: ReplanDecision, task: TaskSpec, step: PlanStep | None) -> None:
        self.history.append(
            {
                "kind": decision.kind.value,
                "reason": decision.reason,
                "task": task.goal,
                "step": (step.description or step.action.describe()) if step else None,
            }
        )

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for entry in self.history:
            counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
        return {"replans": self.replans, "by_kind": counts, "recent": self.history[-10:]}


def goal_words(task: TaskSpec) -> list[str]:
    words = [task.entity_noun, task.verb]
    for value in task.entities.values():
        if value and len(value) < 30 and value.isalpha():
            words.append(normalize_text(value))
    return [word for word in words if word]
