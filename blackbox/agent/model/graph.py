"""The website behavioral graph.

Nodes are observed states, edges are transitions that were really executed and
observed.  Planning, prerequisite discovery, failure lookup and workflow mining
are all queries over this structure - there is no other source of truth.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .base import normalize_text
from .constraint import Constraint, ConstraintKind
from .evidence import Evidence
from .page import Page
from .state import WebsiteState
from .transition import Transition, TransitionStatus
from .workflow import Workflow


@dataclass
class CostModel:
    """Path cost: prefer short, safe, high-confidence routes."""

    action_weight: float = 1.0
    risk_weight: float = 2.0
    confidence_weight: float = 3.0
    unverified_penalty: float = 2.5

    def __call__(self, transition: Transition) -> float:
        cost = self.action_weight
        cost += self.risk_weight * transition.action.risk.rank
        cost += self.confidence_weight * (1.0 - transition.confidence)
        if transition.status is not TransitionStatus.VERIFIED:
            cost += self.unverified_penalty
        if transition.execution_count and transition.success_rate < 0.5:
            cost *= 1.5
        return cost


class PathResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    found: bool = False
    state_ids: list[str] = Field(default_factory=list)
    transition_ids: list[str] = Field(default_factory=list)
    cost: float = 0.0
    reason: str = ""


class ApplicationGraph(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_id: str = ""
    states: dict[str, WebsiteState] = Field(default_factory=dict)
    transitions: dict[str, Transition] = Field(default_factory=dict)
    workflows: dict[str, Workflow] = Field(default_factory=dict)
    constraints: dict[str, Constraint] = Field(default_factory=dict)
    pages: dict[str, Page] = Field(default_factory=dict)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    root_state_id: str | None = None

    # -- mutation ----------------------------------------------------------
    def upsert_state(self, state: WebsiteState) -> tuple[WebsiteState, bool]:
        """Insert a state or refresh an existing one. Returns (state, created)."""
        state.finalize()
        existing = self.states.get(state.state_id)
        if existing is None:
            state.visit_count = max(1, state.visit_count)
            self.states[state.state_id] = state
            if self.root_state_id is None:
                self.root_state_id = state.state_id
            return state, True
        existing.last_seen = state.last_seen
        existing.visit_count += 1
        existing.observation_id = state.observation_id
        if state.screenshot_ref:
            existing.screenshot_ref = state.screenshot_ref
        if existing.status.value == "STALE":
            existing.status = state.status
        return existing, False

    def add_transition(self, transition: Transition) -> Transition:
        transition.finalize()
        existing = self.transitions.get(transition.transition_id)
        if existing is None:
            self.transitions[transition.transition_id] = transition
            return transition
        existing.updated_at = transition.updated_at
        for effect in transition.observed_effects:
            if effect not in existing.observed_effects:
                existing.observed_effects.append(effect)
        for prereq in transition.preconditions:
            if prereq not in existing.preconditions:
                existing.preconditions.append(prereq)
        for post in transition.postconditions:
            if post not in existing.postconditions:
                existing.postconditions.append(post)
        for evidence_id in transition.evidence_ids:
            if evidence_id not in existing.evidence_ids:
                existing.evidence_ids.append(evidence_id)
        return existing

    def record_execution(self, transition_id: str, *, success: bool, evidence_id: str | None = None) -> None:
        transition = self.transitions.get(transition_id)
        if transition is None:
            return
        if success:
            transition.record_success(evidence_id)
        else:
            transition.record_failure(evidence_id)

    def upsert_workflow(self, workflow: Workflow) -> Workflow:
        workflow.finalize()
        existing = self.workflows.get(workflow.workflow_id)
        if existing is None:
            self.workflows[workflow.workflow_id] = workflow
            return workflow
        existing.confidence = max(existing.confidence, workflow.confidence)
        existing.updated_at = workflow.updated_at
        for evidence_id in workflow.evidence_ids:
            if evidence_id not in existing.evidence_ids:
                existing.evidence_ids.append(evidence_id)
        return existing

    def upsert_constraint(self, constraint: Constraint) -> Constraint:
        constraint.finalize()
        existing = self.constraints.get(constraint.constraint_id)
        if existing is None:
            self.constraints[constraint.constraint_id] = constraint
            return constraint
        for evidence_id in constraint.supporting_evidence:
            existing.support(evidence_id)
        for evidence_id in constraint.contradicting_evidence:
            existing.contradict(evidence_id)
        return existing

    def add_evidence(self, evidence: Evidence) -> Evidence:
        evidence.finalize()
        self.evidence.setdefault(evidence.evidence_id, evidence)
        return evidence

    def upsert_page(self, page: Page) -> Page:
        page.finalize()
        existing = self.pages.get(page.page_id)
        if existing is None:
            self.pages[page.page_id] = page
            return page
        for state_id in page.state_ids:
            if state_id not in existing.state_ids:
                existing.state_ids.append(state_id)
        if page.summary and not existing.summary:
            existing.summary = page.summary
        if page.name and not existing.name:
            existing.name = page.name
        return existing

    def mark_stale(self, *, state_ids: Iterable[str] = (), transition_ids: Iterable[str] = ()) -> None:
        """Website changed: keep the knowledge, mark it as no longer current."""
        from .state import StateStatus

        for state_id in state_ids:
            state = self.states.get(state_id)
            if state:
                state.status = StateStatus.STALE
        for transition_id in transition_ids:
            transition = self.transitions.get(transition_id)
            if transition:
                transition.status = TransitionStatus.STALE

    # -- queries -----------------------------------------------------------
    def state(self, state_id: str) -> WebsiteState | None:
        return self.states.get(state_id)

    def transitions_from(self, state_id: str) -> list[Transition]:
        return [t for t in self.transitions.values() if t.source_state == state_id]

    def transitions_to(self, state_id: str) -> list[Transition]:
        return [t for t in self.transitions.values() if t.target_state == state_id]

    def find_state(
        self,
        *,
        state_id: str | None = None,
        url_contains: str | None = None,
        title_contains: str | None = None,
        text_contains: str | None = None,
        predicate: Callable[[WebsiteState], bool] | None = None,
    ) -> WebsiteState | None:
        if state_id:
            return self.states.get(state_id)
        for state in self.states.values():
            if url_contains and url_contains.lower() not in state.url.lower():
                continue
            if title_contains and title_contains.lower() not in state.title.lower():
                continue
            if text_contains and text_contains.lower() not in normalize_text(state.visible_text):
                continue
            if predicate and not predicate(state):
                continue
            return state
        return None

    def find_states(self, predicate: Callable[[WebsiteState], bool]) -> list[WebsiteState]:
        return [s for s in self.states.values() if predicate(s)]

    def find_transition(
        self,
        *,
        source_state: str | None = None,
        target_state: str | None = None,
        action_signature: str | None = None,
        transition_id: str | None = None,
    ) -> Transition | None:
        if transition_id:
            return self.transitions.get(transition_id)
        for transition in self.transitions.values():
            if source_state and transition.source_state != source_state:
                continue
            if target_state and transition.target_state != target_state:
                continue
            if action_signature and normalize_text(action_signature) not in transition.action.signature():
                continue
            return transition
        return None

    def find_workflow(self, goal: str) -> Workflow | None:
        wanted = normalize_text(goal)
        if not wanted:
            return None
        scored: list[tuple[float, Workflow]] = []
        for workflow in self.workflows.values():
            haystack = normalize_text(f"{workflow.goal} {workflow.name}")
            if wanted in haystack or haystack in wanted:
                scored.append((1.0 + workflow.confidence, workflow))
                continue
            overlap = len(set(wanted.split()) & set(haystack.split()))
            if overlap:
                scored.append((overlap / max(1, len(wanted.split())) + workflow.confidence / 10, workflow))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def find_prerequisites(self, *, subject: str | None = None, state_id: str | None = None) -> list[Constraint]:
        results: list[Constraint] = []
        for constraint in self.constraints.values():
            if constraint.kind is not ConstraintKind.PRECONDITION:
                continue
            if subject and normalize_text(subject) not in normalize_text(constraint.subject):
                continue
            if state_id and constraint.condition.get("state_id") not in (None, state_id):
                continue
            results.append(constraint)
        return sorted(results, key=lambda c: c.confidence, reverse=True)

    def find_known_failure(self, action_signature: str) -> list[Evidence]:
        needle = normalize_text(action_signature)
        results = []
        for evidence in self.evidence.values():
            if needle and needle not in normalize_text(
                f"{evidence.message} {' '.join(e.detail for e in evidence.effects)}"
            ):
                continue
            if any(effect.kind.value in ("PRECONDITION_BLOCKED", "VALIDATION_MESSAGE", "ERROR") for effect in evidence.effects):
                results.append(evidence)
        return results

    def find_related_evidence(self, *, transition_id: str | None = None, hypothesis_id: str | None = None, constraint_id: str | None = None) -> list[Evidence]:
        results = []
        for evidence in self.evidence.values():
            if transition_id and evidence.transition_id == transition_id:
                results.append(evidence)
            elif constraint_id and constraint_id in evidence.notes:
                results.append(evidence)
            elif hypothesis_id and hypothesis_id in evidence.notes:
                results.append(evidence)
        return results

    def find_path(
        self,
        start_state_id: str,
        goal: Callable[[WebsiteState], bool] | str,
        *,
        cost: CostModel | None = None,
        max_expansions: int = 2000,
        allowed_risk: int = 3,
    ) -> PathResult:
        """Dijkstra over verified-ish transitions; keyword goal or predicate."""
        if isinstance(goal, str):
            needle = normalize_text(goal)
            predicate: Callable[[WebsiteState], bool] = lambda s: needle in normalize_text(  # noqa: E731
                f"{s.title} {s.url} {s.semantic_summary} {s.visible_text}"
            )
        else:
            predicate = goal
        cost = cost or CostModel()
        start = self.states.get(start_state_id)
        if start is None:
            return PathResult(found=False, reason=f"unknown start state {start_state_id}")
        if predicate(start):
            return PathResult(found=True, state_ids=[start_state_id], cost=0.0, reason="already at goal")

        best: dict[str, float] = {start_state_id: 0.0}
        came_from: dict[str, tuple[str, str]] = {}
        queue: list[tuple[float, int, str]] = [(0.0, 0, start_state_id)]
        expansions = 0
        while queue and expansions < max_expansions:
            distance, _, state_id = heapq.heappop(queue)
            if distance > best.get(state_id, float("inf")):
                continue
            expansions += 1
            state = self.states.get(state_id)
            if state is None:
                continue
            for transition in self.transitions_from(state_id):
                if transition.status is TransitionStatus.REFUTED:
                    continue
                if transition.action.risk.rank > allowed_risk:
                    continue
                neighbor = transition.target_state
                if neighbor not in self.states:
                    continue
                candidate = distance + cost(transition)
                if candidate < best.get(neighbor, float("inf")):
                    best[neighbor] = candidate
                    came_from[neighbor] = (state_id, transition.transition_id)
                    heapq.heappush(queue, (candidate, expansions, neighbor))
                    if predicate(self.states[neighbor]):
                        return self._reconstruct(neighbor, came_from, best[neighbor])
        return PathResult(found=False, cost=best.get(start_state_id, 0.0), reason="no path found within budget")

    def _reconstruct(self, target: str, came_from: dict[str, tuple[str, str]], cost: float) -> PathResult:
        states = [target]
        transitions: list[str] = []
        cursor = target
        while cursor in came_from:
            previous, transition_id = came_from[cursor]
            transitions.append(transition_id)
            states.append(previous)
            cursor = previous
        states.reverse()
        transitions.reverse()
        return PathResult(found=True, state_ids=states, transition_ids=transitions, cost=cost, reason="path found")

    # -- export ------------------------------------------------------------
    def to_graph_json(self) -> dict[str, Any]:
        nodes = []
        for state in self.states.values():
            nodes.append(
                {
                    "id": state.state_id,
                    "label": state.label(),
                    "url": state.url,
                    "url_key": state.fingerprint.url_key,
                    "page_id": state.page_id,
                    "status": state.status.value,
                    "confidence": state.confidence,
                    "visit_count": state.visit_count,
                    "elements": len(state.visible_elements),
                    "summary": state.semantic_summary,
                    "screenshot_ref": state.screenshot_ref,
                }
            )
        edges = []
        for transition in self.transitions.values():
            edges.append(
                {
                    "id": transition.transition_id,
                    "source": transition.source_state,
                    "target": transition.target_state,
                    "action": transition.action.describe(),
                    "action_type": transition.action.type.value,
                    "risk": transition.action.risk.value,
                    "confidence": transition.confidence,
                    "status": transition.status.value,
                    "verified": transition.status is TransitionStatus.VERIFIED,
                    "success_count": transition.success_count,
                    "failure_count": transition.failure_count,
                    "effects": [e.kind.value for e in transition.observed_effects],
                    "evidence_ids": transition.evidence_ids,
                }
            )
        return {
            "target_id": self.target_id,
            "root_state_id": self.root_state_id,
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "states": len(self.states),
                "transitions": len(self.transitions),
                "verified": sum(1 for t in self.transitions.values() if t.status is TransitionStatus.VERIFIED),
                "workflows": len(self.workflows),
                "constraints": len(self.constraints),
                "pages": len(self.pages),
            },
        }
