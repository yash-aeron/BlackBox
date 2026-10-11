"""Planning: use what was learned before exploring anything again.

Two planning modes, in order of preference:

1. **Workflow reuse** - a parameterized workflow already verified for this goal
   is instantiated with the task's entities.
2. **Graph search** - A*/Dijkstra over verified transitions from the current
   state to a state that satisfies the task's goal.

If neither yields a route, the plan says so and names the region to explore,
instead of re-exploring the whole website.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..model.action import Action, ActionType, RiskLevel
from ..model.base import normalize_text, stable_id
from ..model.graph import ApplicationGraph, CostModel
from ..model.state import WebsiteState
from ..model.transition import Transition, TransitionStatus
from ..model.workflow import Workflow
from ..perception.semantic_labeler import SemanticIntent, SemanticLabeler
from .task_parser import TaskParser, TaskSpec


@dataclass
class PlanStep:
    index: int
    action: Action
    description: str = ""
    transition_id: str | None = None
    expected_effect: list[str] = field(default_factory=list)
    parameter_names: list[str] = field(default_factory=list)
    source_state: str = ""
    target_state: str = ""
    verify: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "action": self.action.describe(),
            "action_type": self.action.type.value,
            "description": self.description,
            "transition_id": self.transition_id,
            "target_state": self.target_state,
            "risk": self.action.risk.value,
            "parameters": {k: v for k, v in self.action.parameters.items() if k != "file"},
        }


@dataclass
class Plan:
    task: TaskSpec
    steps: list[PlanStep] = field(default_factory=list)
    source: str = "none"  # workflow | graph | none
    workflow_id: str | None = None
    start_state_id: str = ""
    expected_end_state: str = ""
    confidence: float = 0.0
    estimated_cost: float = 0.0
    notes: list[str] = field(default_factory=list)
    exploration_hint: dict[str, Any] = field(default_factory=dict)
    plan_id: str = ""

    def __post_init__(self) -> None:
        if not self.plan_id:
            self.plan_id = stable_id("plan", self.task.task_id, self.source, [s.action.action_id for s in self.steps])

    @property
    def usable(self) -> bool:
        return bool(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task": self.task.to_dict(),
            "source": self.source,
            "workflow_id": self.workflow_id,
            "start_state_id": self.start_state_id,
            "expected_end_state": self.expected_end_state,
            "confidence": round(self.confidence, 3),
            "estimated_cost": round(self.estimated_cost, 3),
            "steps": [step.to_dict() for step in self.steps],
            "notes": self.notes,
            "exploration_hint": self.exploration_hint,
        }


class Planner:
    """Builds a plan from the learned model."""

    def __init__(
        self,
        *,
        cost: CostModel | None = None,
        parser: TaskParser | None = None,
        max_steps: int = 20,
        use_workflows: bool = True,
        use_transitions: bool = True,
    ) -> None:
        self.cost = cost or CostModel()
        self.parser = parser or TaskParser()
        self.max_steps = max_steps
        # Ablation switches: which parts of the learned model may be used.
        self.use_workflows = use_workflows
        self.use_transitions = use_transitions
        self.labeler = SemanticLabeler()
        self.last_plan: Plan | None = None

    # -- entry point -------------------------------------------------------
    def plan(self, task: TaskSpec, graph: ApplicationGraph, current_state_id: str | None) -> Plan:
        if not graph.states:
            plan = Plan(
                task=task,
                source="none",
                notes=["no learned model for this target yet; exploration is required first"],
                exploration_hint={"reason": "empty model", "keywords": [task.entity_noun or task.verb]},
            )
            self.last_plan = plan
            return plan

        start_state_id = current_state_id or self._match_state_id(graph, task) or graph.root_state_id or ""
        if not start_state_id or start_state_id not in graph.states:
            start_state_id = next(iter(graph.states))

        workflow_plan = self._plan_from_workflow(task, graph, start_state_id) if self.use_workflows else None

        if not self.use_transitions:
            # Ablation: states are known but transitions are not reusable, so the
            # agent has to explore again for every task.
            if workflow_plan is not None:
                self.last_plan = workflow_plan
                return workflow_plan
            plan = Plan(
                task=task,
                source="none",
                start_state_id=start_state_id,
                notes=["transition reuse disabled (ablation); exploration required"],
                exploration_hint={
                    "reason": "transition reuse disabled",
                    "keywords": [task.entity_noun or task.verb],
                    "start_state_id": start_state_id,
                    "region": [],
                    "max_actions": 25,
                },
            )
            self.last_plan = plan
            return plan

        graph_plan = self._plan_from_graph(task, graph, start_state_id)

        # Prefer the shortest route that reaches the goal: a workflow that also
        # creates a record is the wrong plan for "open the form", while a learned
        # workflow is usually the right plan when it is the shorter one.
        if workflow_plan is not None and graph_plan.usable:
            if len(graph_plan.steps) < len(workflow_plan.steps):
                graph_plan.notes.append(
                    f"chosen over workflow {workflow_plan.workflow_id}: "
                    f"{len(graph_plan.steps)} steps vs {len(workflow_plan.steps)}"
                )
                self.last_plan = graph_plan
                return graph_plan
            workflow_plan.notes.append(
                f"chosen over the raw graph route: {len(workflow_plan.steps)} steps vs {len(graph_plan.steps)}"
            )
            self.last_plan = workflow_plan
            return workflow_plan
        if workflow_plan is not None:
            self.last_plan = workflow_plan
            return workflow_plan
        self.last_plan = graph_plan
        return graph_plan

    # -- workflow reuse ----------------------------------------------------
    def _plan_from_workflow(self, task: TaskSpec, graph: ApplicationGraph, start_state_id: str) -> Plan | None:
        workflow = self._best_workflow(task, graph)
        if workflow is None:
            return None
        bound = self.parser.bind_parameters(task, [parameter.name for parameter in workflow.parameters])
        missing = [
            parameter.name
            for parameter in workflow.parameters
            if parameter.required and parameter.name not in bound
        ]
        steps = self._instantiate(workflow, bound)
        if not steps:
            return None

        notes = [f"reused learned workflow {workflow.workflow_id} ({workflow.goal})"]
        if missing:
            notes.append(f"parameters using synthesized defaults: {', '.join(missing)}")
        plan = Plan(
            task=task,
            steps=steps,
            source="workflow",
            workflow_id=workflow.workflow_id,
            start_state_id=start_state_id,
            expected_end_state=workflow.expected_end_state,
            confidence=min(0.95, workflow.confidence + 0.15),
            estimated_cost=float(len(steps)),
            notes=notes,
        )
        # A workflow learned from another start state still needs a route to its
        # entry point; if the current state differs, prepend the path.
        if workflow.start_state_id and workflow.start_state_id != start_state_id and workflow.start_state_id in graph.states:
            path = graph.find_path(start_state_id, lambda s: s.state_id == workflow.start_state_id, cost=self.cost)
            if path.found and len(path.transition_ids) > 1:
                prefix = self._steps_from_transitions(graph, path.transition_ids)
                plan.steps = prefix + plan.steps
                plan.notes.append(f"prefixed with {len(prefix)} navigation step(s) to reach the workflow entry point")
            elif not path.found and start_state_id != workflow.start_state_id:
                plan.notes.append("workflow entry state is not reachable from the current state")
                plan.confidence *= 0.7
        for index, step in enumerate(plan.steps):
            step.index = index
        return plan

    def _best_workflow(self, task: TaskSpec, graph: ApplicationGraph) -> Workflow | None:
        if task.goal:
            found = graph.find_workflow(task.goal)
            if found is not None:
                return found
        noun = task.entity_noun
        best: tuple[float, Workflow] | None = None
        for workflow in graph.workflows.values():
            haystack = normalize_text(f"{workflow.goal} {workflow.name}")
            score = 0.0
            if task.verb and task.verb in haystack:
                score += 0.5
            if noun and noun in haystack:
