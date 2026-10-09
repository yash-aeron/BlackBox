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
                score += 0.5
            score += workflow.confidence * 0.2
            if score > 1.0 and (best is None or score > best[0]):
                best = (score, workflow)
        return best[1] if best else None

    def _instantiate(self, workflow: Workflow, bound: dict[str, str]) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for step in workflow.steps:
            action = step.action.model_copy(deep=True)
            for name in step.parameter_names:
                value = bound.get(name)
                if value is None:
                    continue
                if action.type in (ActionType.TYPE,):
                    action.parameters["value"] = value
                elif action.type is ActionType.SELECT:
                    action.parameters["option"] = value
            action.origin = "planner"
            action.rationale = f"workflow {workflow.goal} step {step.index}"
            steps.append(
                PlanStep(
                    index=step.index,
                    action=action,
                    description=step.description,
                    transition_id=step.transition_id,
                    expected_effect=step.expected_effect,
                    parameter_names=step.parameter_names,
                )
            )
        return steps

    # -- graph search ------------------------------------------------------
    def _plan_from_graph(self, task: TaskSpec, graph: ApplicationGraph, start_state_id: str) -> Plan:
        goal_predicate, goal_description = self._goal_predicate(task, graph)
        allowed_risk = RiskLevel.HIGH.rank if task.verb in ("delete",) else RiskLevel.MEDIUM.rank
        path = graph.find_path(start_state_id, goal_predicate, cost=self.cost, allowed_risk=allowed_risk)
        if path.found and len(path.transition_ids) <= self.max_steps:
            steps = self._steps_from_transitions(graph, path.transition_ids)
            confidence = self._path_confidence(graph, path.transition_ids)
            return Plan(
                task=task,
                steps=steps,
                source="graph",
                start_state_id=start_state_id,
                expected_end_state=goal_description,
                confidence=confidence,
                estimated_cost=path.cost,
                notes=[f"planned over {len(steps)} learned transitions by graph search"],
            )
        hint = self._exploration_hint(task, graph, start_state_id)
        return Plan(
            task=task,
            source="none",
            start_state_id=start_state_id,
            expected_end_state=goal_description,
            notes=[
                "no learned route matches this task",
                f"targeted exploration required around: {hint.get('keywords')}",
            ],
            exploration_hint=hint,
        )

    def _steps_from_transitions(self, graph: ApplicationGraph, transition_ids: list[str]) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for index, transition_id in enumerate(transition_ids):
            transition = graph.transitions.get(transition_id)
            if transition is None:
                continue
            steps.append(
                PlanStep(
                    index=index,
                    action=transition.action.model_copy(deep=True),
                    description=transition.action.describe(),
                    transition_id=transition.transition_id,
                    expected_effect=[effect.kind.value for effect in transition.observed_effects][:4],
                    source_state=transition.source_state,
                    target_state=transition.target_state,
                )
            )
        return steps

    def _path_confidence(self, graph: ApplicationGraph, transition_ids: list[str]) -> float:
        confidences = [
            graph.transitions[tid].confidence for tid in transition_ids if tid in graph.transitions
        ]
        verified = [
            1.0 if graph.transitions[tid].status is TransitionStatus.VERIFIED else 0.6
            for tid in transition_ids
            if tid in graph.transitions
        ]
        if not confidences:
            return 0.0
        base = sum(confidences) / len(confidences)
        verification = sum(verified) / len(verified)
        return round(min(0.95, base * 0.6 + verification * 0.4), 3)

    # -- goal state identification ----------------------------------------
    def _goal_predicate(self, task: TaskSpec, graph: ApplicationGraph) -> tuple[Callable[[WebsiteState], bool], str]:
        """Prefer the task's own machine-checkable success conditions as the goal."""
        declared = self._declared_goal(task)
        if declared is not None:
            predicate, description = declared
            if any(predicate(state) for state in graph.states.values()):
                return predicate, description
            # No known state satisfies the declared conditions yet: keep them as
            # the goal (so targeted exploration has a target) rather than
            # silently falling back to something easier to satisfy.
            return predicate, description

        noun = task.entity_noun
        verb = task.verb

        success_states: set[str] = set()
        for transition in graph.transitions.values():
            if transition.status is TransitionStatus.REFUTED:
                continue
            for effect in transition.observed_effects:
                if effect.kind.value in ("SUCCESS_MESSAGE", "DATA_CHANGED"):
                    label = normalize_text(
                        f"{transition.action.target.label() if transition.action.target else ''} {effect.message or ''}"
                    )
                    if noun and noun in label:
                        success_states.add(transition.target_state)
                    elif not noun:
                        success_states.add(transition.target_state)
        for transition in graph.transitions.values():
            for effect in transition.observed_effects:
                if effect.kind.value == "DIALOG_CLOSED" and transition.target_state in graph.states:
                    label = normalize_text(transition.action.target.label() if transition.action.target else "")
                    if noun and noun in label:
                        success_states.add(transition.target_state)

        entity_values = [normalize_text(value) for value in task.entities.values() if value]

        def predicate(state: WebsiteState) -> bool:
            if state.state_id in success_states:
                return True
            haystack = normalize_text(f"{state.title} {state.semantic_summary} {state.visible_text[:1500]}")
            if noun and noun not in haystack:
                return False
            if verb == "search":
                return any(noun in normalize_text(e.label()) for e in state.visible_elements)
            if verb in ("view", "filter"):
                return noun in haystack
            if entity_values and any(value and value in haystack for value in entity_values):
                return True
            return noun in haystack and any(
                normalize_text(e.label()) and noun in normalize_text(e.label()) for e in state.visible_elements
            )

        description = f"{task.expected_end_state or task.goal}"
        return predicate, description

    def _declared_goal(self, task: TaskSpec) -> tuple[Callable[[WebsiteState], bool], str] | None:
        """Build a state-level goal from the task's declared success conditions."""
        specs = list(getattr(task, "predicates", []) or [])
        if not specs:
            return None
        checks: list[tuple[str, str]] = []  # (kind, value/target)
        for spec in specs:
            kind = str(spec.get("kind", "")).upper()
            if not kind:
                continue
            checks.append((kind, str(spec.get("value", ""))))
            if spec.get("target"):
                checks.append((kind + ":target", str(spec["target"])))
        if not checks:
            return None

        def predicate(state: WebsiteState) -> bool:
            text = normalize_text(f"{state.title} {state.visible_text[:6000]} {state.semantic_summary}")
            labels = [normalize_text(element.label()) for element in state.visible_elements]
            for kind, value in checks:
                wanted = normalize_text(value)
                if not wanted:
                    continue
                if kind == "TEXT_PRESENT":
                    if wanted not in text and not any(wanted in label for label in labels):
                        return False
                elif kind == "TEXT_ABSENT":
                    if wanted in text or any(wanted in label for label in labels):
                        return False
                elif kind == "URL_CONTAINS":
                    if wanted not in state.url.lower():
                        return False
                elif kind in ("ALERT_CONTAINS", "STATE_MATCHES"):
                    if wanted not in text:
                        return False
                elif kind == "DIALOG_OPEN":
                    if not state.dialogs:
                        return False
                    if not any(wanted in normalize_text(f"{d.title} {d.text} {' '.join(d.actions)}") for d in state.dialogs):
                        return False
                elif kind == "TABLE_ROW_CONTAINS":
                    found = False
                    for row_text in self._table_rows(state):
                        if wanted in row_text:
                            found = True
                            break
                    if not found:
                        return False
                elif kind == "ELEMENT_PRESENT":
                    if not any(wanted in label for label in labels):
                        return False
                elif kind == "ELEMENT_ABSENT":
                    if any(wanted in label for label in labels):
                        return False
                elif kind == "VALUE_EQUALS:target":
                    target = wanted
                    for element in state.visible_elements:
                        if target in normalize_text(element.label()):
                            if normalize_text(element.value_state.value or "") != normalize_text(
                                next((v for k, v in checks if k == "VALUE_EQUALS"), "")
                            ):
                                return False
                            break
                    else:
                        return False
                elif kind == "VALUE_EQUALS":
                    # The value itself is only meaningful together with its target.
                    continue
            return True

        return predicate, f"declared success conditions: {'; '.join(k for k, _ in checks if not k.endswith(':target'))}"

    @staticmethod
    def _table_rows(state: WebsiteState) -> list[str]:
        """Row text for table-like content observed on a state.

        Table structure is re-derived from the visible elements' sections and the
        state text, because the persisted state keeps elements rather than raw
        table payloads.
        """
        rows: list[str] = []
        for line in state.visible_text.split("\n"):
            cleaned = line.strip()
            if cleaned and len(cleaned) < 300:
                rows.append(normalize_text(cleaned))
        return rows

    def _match_state_id(self, graph: ApplicationGraph, task: TaskSpec) -> str | None:
        """Pick the most likely current entry state for the task's noun."""
        if not task.entity_noun:
            return graph.root_state_id
        candidates = [
            state
            for state in graph.states.values()
            if task.entity_noun in normalize_text(f"{state.title} {state.page_identity} {state.semantic_summary}")
        ]
        if not candidates:
            return graph.root_state_id
        candidates.sort(key=lambda s: (-s.visit_count, s.state_id))
        return candidates[0].state_id

    def _exploration_hint(self, task: TaskSpec, graph: ApplicationGraph, start_state_id: str) -> dict[str, Any]:
        keywords = [keyword for keyword in [task.entity_noun, task.verb] if keyword]
        region_states: list[dict[str, Any]] = []
        for state in graph.states.values():
            haystack = normalize_text(f"{state.title} {state.page_identity} {state.semantic_summary}")
            if any(keyword in haystack for keyword in keywords):
                region_states.append(
                    {
                        "state_id": state.state_id,
                        "label": state.label(),
                        "url": state.url,
                        "summary": state.semantic_summary[:160],
                    }
                )
        region_states.sort(key=lambda item: -len(item["summary"]))
        return {
            "reason": "no route found; explore the region that mentions the goal",
            "keywords": keywords,
            "start_state_id": start_state_id,
            "region": region_states[:5],
            "max_actions": 25,
        }
