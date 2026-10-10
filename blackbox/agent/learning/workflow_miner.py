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
