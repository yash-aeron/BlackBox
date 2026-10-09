"""Workflow discovery: mine goal-shaped, parameterized procedures from the graph.

A workflow is not an arbitrary action sequence and not a recording.  It is:

* anchored on a transition whose observed effect was a *success* (a confirmation
  message or an observed data change) - navigation alone is never a goal;
* prefixed with the data-entry steps that were actually performed in the same
  state before that submission succeeded, lifted into named parameters;
* prefixed with the navigation route that leads to that state, so it can be
  re-run from the site's entry point;
* only promoted to "verified" when it has been executed again as a whole.

Everything here is derived from evidence the browser produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..model.action import Action, ActionType, EffectKind
from ..model.base import normalize_text, stable_id
from ..model.element import ElementRole
from ..model.graph import ApplicationGraph
from ..model.transition import Transition, TransitionStatus
from ..model.workflow import ParameterKind, Workflow, WorkflowParameter, WorkflowStep, WorkflowTemplate
from ..perception.semantic_labeler import SemanticIntent, SemanticLabeler

SUCCESS_VERBS = {
    "created": "create",
    "added": "create",
    "saved": "save",
    "updated": "update",
    "changed": "update",
    "deleted": "delete",
    "removed": "remove",
    "emptied": "empty",
    "exported": "export",
    "downloaded": "download",
    "submitted": "submit",
    "placed": "checkout",
    "applied": "apply",
    "confirmed": "confirm",
    "sent": "send",
    "uploaded": "upload",
    "selected": "select",
    "generated": "generate",
}

ENTITY_NOUNS = (
    "customer",
    "lead",
    "contact",
    "deal",
    "record",
    "entry",
    "product",
    "order",
    "task",
    "project",
    "report",
    "invoice",
    "user",
    "account",
    "application",
    "booking",
    "ticket",
    "item",
    "member",
    "coupon",
    "document",
    "setting",
    "profile",
    "cart",
    "form",
)

GOAL_LABELS: dict[SemanticIntent, str] = {
    SemanticIntent.CREATE: "create",
    SemanticIntent.UPDATE: "update",
    SemanticIntent.DELETE: "delete",
    SemanticIntent.EXPORT: "export",
    SemanticIntent.DOWNLOAD: "download",
    SemanticIntent.UPLOAD: "upload",
    SemanticIntent.SEARCH: "search",
    SemanticIntent.FILTER: "filter",
    SemanticIntent.SUBMIT: "submit",
    SemanticIntent.PAY: "checkout",
    SemanticIntent.SEND: "send",
}

MEANINGFUL_EFFECTS = {EffectKind.SUCCESS_MESSAGE, EffectKind.DATA_CHANGED}
DATA_ENTRY_TYPES = {ActionType.TYPE, ActionType.SELECT, ActionType.CHECK, ActionType.UNCHECK, ActionType.UPLOAD}
NAVIGATION_TYPES = {
    ActionType.NAVIGATE,
    ActionType.NAVIGATE_BACK,
    ActionType.SCROLL,
    ActionType.WAIT_FOR_STATE,
    ActionType.CLOSE_DIALOG,
}

CANCEL_WORDS = re.compile(r"\b(cancel|close|dismiss|back|no)\b", re.IGNORECASE)


class WorkflowMiner:
    """Extracts reusable, parameterized workflows from verified transitions."""

    def __init__(self, *, min_steps: int = 2, max_steps: int = 12, max_preparation: int = 6, max_navigation: int = 5) -> None:
        self.min_steps = min_steps
        self.max_steps = max_steps
        self.max_preparation = max_preparation
        self.max_navigation = max_navigation
        self.labeler = SemanticLabeler()

    # -- public API --------------------------------------------------------
    def mine(self, graph: ApplicationGraph, *, state_labels: dict[str, str] | None = None) -> list[Workflow]:
        workflows: list[Workflow] = []
        for transition in graph.transitions.values():
            if transition.status is TransitionStatus.REFUTED:
                continue
            if not self._is_meaningful(transition):
                continue
            workflow = self._build(graph, transition)
            if workflow is not None:
                workflows.append(workflow)
        deduped = self._deduplicate(workflows)
        for template in self.generalize(deduped):
            graph.upsert_template(template)
        return deduped

    def generalize(self, workflows: list[Workflow]) -> list[WorkflowTemplate]:
        """Abstract concrete workflows into reusable parameterized templates across entity types."""
        templates: dict[str, WorkflowTemplate] = {}
        for workflow in workflows:
            parts = workflow.goal.split("_")
            intent = parts[0] if parts else "action"
            entity = parts[1] if len(parts) > 1 else "entity"

            has_data_entry = any(step.action.type in DATA_ENTRY_TYPES for step in workflow.steps)
            has_submit = any(step.action.type is ActionType.CLICK for step in workflow.steps)
            pattern = "FORM_SUBMISSION" if (has_data_entry and has_submit) else "DIRECT_ACTION"

            param_templates = [f"{p.name}:{p.kind.value}" for p in workflow.parameters]
            template_key = f"{intent}_{pattern}"

            if template_key not in templates:
                template_id = stable_id("tmpl", template_key, str(len(workflow.steps)))
                templates[template_key] = WorkflowTemplate(
                    template_id=template_id,
                    name=f"{intent.title()} {entity.title()} Template ({pattern.replace('_', ' ').title()})",
                    intent=intent,
                    entity_noun=entity,
                    pattern=pattern,
                    parameter_templates=param_templates,
                    step_count=len(workflow.steps),
                    concrete_workflow_ids=[workflow.workflow_id],
                    confidence=workflow.confidence,
                )
            else:
                tmpl = templates[template_key]
                if workflow.workflow_id not in tmpl.concrete_workflow_ids:
                    tmpl.concrete_workflow_ids.append(workflow.workflow_id)
                tmpl.confidence = max(tmpl.confidence, workflow.confidence)
                for p in param_templates:
                    if p not in tmpl.parameter_templates:
                        tmpl.parameter_templates.append(p)
        return list(templates.values())

    # -- goal derivation ---------------------------------------------------
    def goal_for(self, graph: ApplicationGraph, terminal: Transition) -> tuple[str, str]:
        """Derive a goal such as ``create_customer`` from observed evidence."""
        message = ""
        for effect in terminal.observed_effects:
            if effect.kind in MEANINGFUL_EFFECTS and (effect.message or effect.detail):
                message = normalize_text(effect.message or effect.detail)
                break
        verb = ""
        for word, mapped in SUCCESS_VERBS.items():
            if word in message:
                verb = mapped
                break
        noun = self._entity_noun(graph, terminal, message)
        if not verb:
            verb = self._intent_verb(terminal)
        if verb and noun:
            return f"{verb}_{noun}", f"{verb} a {noun}"
        if verb:
            return verb, verb
        return "perform_task", "perform a task"

    def _intent_verb(self, transition: Transition) -> str:
        label = transition.action.target.label() if transition.action.target else ""
        intent = self.labeler.label_element(_PseudoElement(label, transition.action)).intent
        return GOAL_LABELS.get(intent, "perform")

    def _entity_noun(self, graph: ApplicationGraph, terminal: Transition, message: str) -> str:
        end_state = graph.states.get(terminal.target_state)
        page_haystack = normalize_text(
            " ".join(
                [
                    end_state.title if end_state else "",
                    end_state.page_identity if end_state else "",
                    end_state.semantic_summary if end_state else "",
                ]
            )
        )
        for noun in ENTITY_NOUNS:
            if noun in message:
                return noun
        for noun in ENTITY_NOUNS:
            if noun in page_haystack:
                return noun
        label = normalize_text(terminal.action.target.label() if terminal.action.target else "")
        for noun in ENTITY_NOUNS:
            if noun in label:
                return noun
        start_state = graph.states.get(terminal.source_state)
        start_haystack = normalize_text(
            f"{start_state.title if start_state else ''} {start_state.page_identity if start_state else ''}"
        )
        for noun in ENTITY_NOUNS:
            if noun in start_haystack:
                return noun
        words = [word for word in page_haystack.split() if len(word) > 3 and word not in ("dashboard", "loading", "sample")]
        return words[-1] if words else ""

    # -- construction ------------------------------------------------------
    def _is_meaningful(self, transition: Transition) -> bool:
        return any(effect.kind in MEANINGFUL_EFFECTS for effect in transition.observed_effects)

    def _build(self, graph: ApplicationGraph, terminal: Transition) -> Workflow | None:
        state_id = terminal.source_state
        preparation = self._preparation_for(graph, state_id, before=self._first_evidence_time(graph, terminal))
        if not preparation and self._is_bare_navigation(terminal):
            return None
        if not preparation and len(terminal.observed_effects) == 0:
            return None

        navigation = self._navigation_prefix(graph, state_id)
        steps = navigation + preparation + [terminal]
        if len(steps) > self.max_steps:
            steps = steps[-self.max_steps :]
        if len(steps) < self.min_steps and not preparation:
            return None

        parameters = self._parameters(steps)
        goal, goal_phrase = self.goal_for(graph, terminal)
        end_state = graph.states.get(terminal.target_state)
        start_state = graph.states.get(steps[0].source_state)

        workflow_steps: list[WorkflowStep] = []
        for index, transition in enumerate(steps):
            parameter_names = [
                parameter.name
                for parameter in parameters
                if transition.action.target is not None
                and transition.action.target.element_id == parameter.target_element_id
            ]
            workflow_steps.append(
                WorkflowStep(
                    index=index,
                    action=transition.action,
                    description=self._step_description(transition, parameter_names),
                    expected_effect=[effect.kind.value for effect in transition.observed_effects][:4],
                    transition_id=transition.transition_id,
                    parameter_names=parameter_names,
                )
            )

        confidence = min(0.9, sum(t.confidence for t in steps) / max(1, len(steps)))
        workflow = Workflow(
            name=goal.replace("_", " ").title(),
            goal=goal,
            steps=workflow_steps,
            parameters=parameters,
            expected_end_state=goal_phrase,
            start_state_id=steps[0].source_state,
            path_state_ids=[steps[0].source_state, *[t.target_state for t in steps]],
            confidence=round(confidence, 3),
            evidence_ids=[eid for transition in steps for eid in transition.evidence_ids],
            source="discovered",
        )
        workflow.finalize()
        if start_state is not None:
            workflow.preconditions = [f"start at {start_state.label()}"]
        for transition in steps:
            workflow.preconditions.extend(transition.preconditions)
        workflow.preconditions = sorted(set(workflow.preconditions))[:8]
        return workflow

    def _is_bare_navigation(self, transition: Transition) -> bool:
        return transition.action.type in NAVIGATION_TYPES or transition.action.type is ActionType.CLICK and not transition.action.target

    def _preparation_for(
        self, graph: ApplicationGraph, state_id: str, *, before: str
    ) -> list[Transition]:
        """Data entry performed in this state before the successful submission.

        Only self-loops are considered, they must have produced an observed value
        or form change, and they must have happened before the submission that
        succeeded - the temporal ordering comes from the evidence records.
        """
        candidates: list[Transition] = []
        for transition in graph.transitions.values():
            if transition.source_state != state_id or transition.target_state != state_id:
                continue
            if transition.status is TransitionStatus.REFUTED:
                continue
            if transition.action.type not in DATA_ENTRY_TYPES:
                continue
            if not any(
                effect.kind in (EffectKind.VALUE_CHANGED, EffectKind.FORM_CHANGED)
                for effect in transition.observed_effects
            ):
                continue
            if self._first_evidence_time(graph, transition) > before:
                continue
            label = transition.action.target.label() if transition.action.target else ""
            if CANCEL_WORDS.search(label):
                continue
            candidates.append(transition)

        candidates.sort(key=lambda transition: self._first_evidence_time(graph, transition))

        # One field, one parameter: keep the first successful write per target.
        chosen: list[Transition] = []
        seen_targets: set[str] = set()
        for transition in candidates:
            target_key = transition.action.target.element_id if transition.action.target else transition.transition_id
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            chosen.append(transition)
            if len(chosen) >= self.max_preparation:
                break
        return chosen

    def _navigation_prefix(self, graph: ApplicationGraph, state_id: str) -> list[Transition]:
        """The shortest learned route from the entry state to the workflow state."""
        entry = graph.root_state_id
        if not entry or entry == state_id or entry not in graph.states:
            return []
        path = graph.find_path(entry, lambda state: state.state_id == state_id, max_expansions=500)
        if not path.found:
            return []
        prefix = [
            graph.transitions[transition_id]
            for transition_id in path.transition_ids
            if transition_id in graph.transitions
            and graph.transitions[transition_id].source_state
            != graph.transitions[transition_id].target_state
        ]
        if len(prefix) > self.max_navigation:
            return []
        return prefix

    def _first_evidence_time(self, graph: ApplicationGraph, transition: Transition) -> str:
        times = [
            graph.evidence[evidence_id].created_at
            for evidence_id in transition.evidence_ids
            if evidence_id in graph.evidence
        ]
        return min(times) if times else transition.created_at

    # -- parameters --------------------------------------------------------
    def _parameters(self, transitions: list[Transition]) -> list[WorkflowParameter]:
        parameters: dict[str, WorkflowParameter] = {}
        for transition in transitions:
            action = transition.action
            if action.type not in DATA_ENTRY_TYPES or action.target is None:
                continue
            label = action.target.label() or action.target.element_id
            if CANCEL_WORDS.search(label):
                continue
            name = self._parameter_name(label, action)
            if name in parameters:
                continue
            parameters[name] = WorkflowParameter(
                name=name,
                label=label,
                kind=self._parameter_kind(action),
                required=True,
                example_value=str(action.parameters.get("value") or action.parameters.get("option") or "")[:120],
                target_element_id=action.target.element_id,
            )
        return list(parameters.values())

    def _parameter_name(self, label: str, action: Action) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", "_", normalize_text(label)).strip("_")
        return cleaned[:40] if cleaned else f"param_{stable_id('p', action.signature())[2:8]}"

    def _parameter_kind(self, action: Action) -> ParameterKind:
        if action.type is ActionType.SELECT:
            return ParameterKind.SELECT
        if action.type in (ActionType.CHECK, ActionType.UNCHECK):
            return ParameterKind.CHECKBOX
        if action.type is ActionType.UPLOAD:
            return ParameterKind.FILE
        value = str(action.parameters.get("value", ""))
        if "@" in value and "." in value:
            return ParameterKind.EMAIL
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return ParameterKind.DATE
        if re.fullmatch(r"-?\d+(\.\d+)?", value):
            return ParameterKind.NUMBER
        return ParameterKind.TEXT

    def _step_description(self, transition: Transition, parameter_names: list[str]) -> str:
        action = transition.action
        base = action.describe()
        for effect in transition.observed_effects:
            if effect.kind is EffectKind.SUCCESS_MESSAGE and effect.message:
                return f"{base} -> {effect.message[:60]}"
            if effect.kind in (EffectKind.VALIDATION_MESSAGE, EffectKind.PRECONDITION_BLOCKED) and effect.message:
                return f"{base} -> blocked: {effect.message[:60]}"
        if parameter_names:
            return f"{base} (parameters: {', '.join(parameter_names)})"
        return base

    def _deduplicate(self, workflows: list[Workflow]) -> list[Workflow]:
        """Keep the shortest route for each goal, and drop supersets."""
        best: dict[str, Workflow] = {}
        for workflow in workflows:
            key = workflow.goal
            current = best.get(key)
            if current is None:
                best[key] = workflow
                continue
            better = (
                len(workflow.steps) < len(current.steps)
                or (len(workflow.steps) == len(current.steps) and workflow.confidence > current.confidence)
            )
            if better:
                best[key] = workflow
        return sorted(best.values(), key=lambda w: (w.goal, len(w.steps)))


class _PseudoElement:
    """Adapter so the semantic labeler can interpret an action target."""

    def __init__(self, label: str, action: Action) -> None:
        self.element_id = action.target.element_id if action.target else ""
        self.accessible_name = label
        self.visible_text = label
        self.attributes = {"type": "", "name": ""}
        self.semantic_role = ElementRole.BUTTON


def summarize_workflows(workflows: list[Workflow]) -> list[dict[str, Any]]:
    return [
        {
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "goal": workflow.goal,
            "steps": len(workflow.steps),
            "parameters": [parameter.name for parameter in workflow.parameters],
            "confidence": workflow.confidence,
            "verification_count": workflow.verification_count,
            "expected_end_state": workflow.expected_end_state[:120],
            "description": workflow.describe(),
        }
        for workflow in workflows
    ]
