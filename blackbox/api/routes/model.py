"""Model, graph, states, transitions, workflows, hypotheses, metrics, screenshots.

Every read here works against the stored model when no agent is live, so the
dashboard can inspect what BlackBox has learned without launching a browser.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from blackbox.agent.learning.workflow_miner import WorkflowMiner
from blackbox.agent.model.constraint import Constraint
from blackbox.agent.model.evidence import Evidence
from blackbox.agent.model.state import StateStatus, WebsiteState
from blackbox.agent.model.transition import Transition, TransitionStatus
from blackbox.agent.model.workflow import Workflow

from ..runtime import ModelSource, UnknownTarget, runtime
from ..schemas import GraphResponse, ImportModelRequest, ModelSummary

log = logging.getLogger(__name__)
router = APIRouter(tags=["model"])


def source_or_404(target_id: str) -> ModelSource:
    """The model for this target (live agent or stored), or a 404."""
    try:
        return runtime.model_source(target_id)
    except UnknownTarget as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# -- serialisers -----------------------------------------------------------
def state_payload(state: WebsiteState, *, detail: bool = False) -> dict[str, Any]:
    screenshot = Path(state.screenshot_ref).name if state.screenshot_ref else None
    if detail:
        return {
            **state.model_dump(mode="json"),
            "label": state.label(),
            "screenshot_filename": screenshot,
        }
    return {
        "state_id": state.state_id,
        "label": state.label(),
        "title": state.title,
        "url": state.url,
        "url_key": state.fingerprint.url_key,
        "page_id": state.page_id,
        "page_identity": state.page_identity,
        "status": state.status.value,
        "confidence": state.confidence,
        "visit_count": state.visit_count,
        "elements": len(state.visible_elements),
        "interactive_elements": len(state.interactive_elements()),
        "forms": len(state.forms),
        "dialogs": [dialog.model_dump(mode="json") for dialog in state.dialogs],
        "summary": state.semantic_summary,
        "screenshot_ref": state.screenshot_ref,
        "screenshot_filename": screenshot,
        "observation_id": state.observation_id,
        "first_seen": state.first_seen,
        "last_seen": state.last_seen,
    }


def transition_payload(transition: Transition, *, detail: bool = False) -> dict[str, Any]:
    payload = {
        "transition_id": transition.transition_id,
        "source_state": transition.source_state,
        "target_state": transition.target_state,
        "action": transition.action.model_dump(mode="json"),
        "action_description": transition.action.describe(),
        "action_type": transition.action.type.value,
        "risk": transition.action.risk.value,
        "status": transition.status.value,
        "verified": transition.status is TransitionStatus.VERIFIED,
        "confidence": transition.confidence,
        "observed_effects": [effect.model_dump(mode="json") for effect in transition.observed_effects],
        "preconditions": list(transition.preconditions),
        "postconditions": list(transition.postconditions),
        "evidence_ids": list(transition.evidence_ids),
        "execution_count": transition.execution_count,
        "success_count": transition.success_count,
        "failure_count": transition.failure_count,
        "success_rate": round(transition.success_rate, 3),
        "created_at": transition.created_at,
        "updated_at": transition.updated_at,
    }
    if detail:
        payload["action_expected_effect"] = [effect.value for effect in transition.action.expected_effect]
        payload["action_rationale"] = transition.action.rationale
        payload["action_origin"] = transition.action.origin
    return payload


def workflow_payload(workflow: Workflow) -> dict[str, Any]:
    return {
        "workflow_id": workflow.workflow_id,
        "name": workflow.name,
        "goal": workflow.goal,
        "expected_end_state": workflow.expected_end_state,
        "start_state_id": workflow.start_state_id,
        "confidence": workflow.confidence,
        "verification_count": workflow.verification_count,
        "source": workflow.source,
        "preconditions": list(workflow.preconditions),
        "parameters": [
            {
                "name": parameter.name,
                "label": parameter.label,
                "kind": parameter.kind.value,
                "required": parameter.required,
                "example_value": parameter.example_value,
                "target_element_id": parameter.target_element_id,
            }
            for parameter in workflow.parameters
        ],
        "steps": [
            {
                "index": step.index,
                "description": step.description or step.action.describe(),
                "action": step.action.describe(),
                "action_type": step.action.type.value,
                "risk": step.action.risk.value,
                "parameters": {key: value for key, value in step.action.parameters.items() if key != "file"},
                "expected_effect": list(step.expected_effect),
                "transition_id": step.transition_id,
                "parameter_names": list(step.parameter_names),
                "optional": step.optional,
            }
            for step in workflow.steps
        ],
        "path_state_ids": list(workflow.path_state_ids),
        "evidence_ids": list(workflow.evidence_ids),
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
    }


def constraint_payload(constraint: Constraint) -> dict[str, Any]:
    return {
        "constraint_id": constraint.constraint_id,
        "kind": constraint.kind.value,
        "scope": constraint.scope.value,
        "subject": constraint.subject,
        "expression": constraint.expression,
        "condition": constraint.condition,
        "message": constraint.message,
        "status": constraint.status.value,
        "confidence": constraint.confidence,
        "supporting_evidence": list(constraint.supporting_evidence),
        "contradicting_evidence": list(constraint.contradicting_evidence),
        "created_at": constraint.created_at,
        "updated_at": constraint.updated_at,
    }


def evidence_payload(evidence: Evidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "kind": evidence.kind.value,
        "experiment_id": evidence.experiment_id,
        "session_id": evidence.session_id,
        "step_index": evidence.step_index,
        "action_id": evidence.action_id,
        "source_state": evidence.source_state,
        "target_state": evidence.target_state,
        "transition_id": evidence.transition_id,
        "effects": [effect.model_dump(mode="json") for effect in evidence.effects],
        "observations": list(evidence.observations),
        "screenshot_refs": list(evidence.screenshot_refs),
        "screenshot_filenames": [Path(ref).name for ref in evidence.screenshot_refs],
        "message": evidence.message,
        "notes": list(evidence.notes),
        "created_at": evidence.created_at,
    }


# -- model -----------------------------------------------------------------
@router.get("/targets/{target_id}/model")
def get_model(target_id: str) -> dict[str, Any]:
    """``{summary, stats, versions}`` for the target's newest stored model."""
    source = source_or_404(target_id)
    stats = source.stats()
    summary = ModelSummary.model_validate({**stats, "base_url": source.registration.base_url})
    versions = []
    for row in source.repository.list_model_versions():
        record = dict(row)
        notes = record.get("notes")
        if isinstance(notes, str):
            try:
                record["notes"] = json.loads(notes)
            except ValueError:
                record["notes"] = [notes]
        versions.append(record)
    fingerprints = _fingerprint_stats(source)
    return {
        "target_id": target_id,
        "base_url": source.registration.base_url,
        "live": source.live,
        "summary": summary.model_dump(),
        "stats": stats,
        "versions": versions,
        "fingerprints": fingerprints,
    }

def _fingerprint_stats(source: ModelSource) -> dict[str, Any]:
    states = list(source.graph.states.values())
    if not states:
        return {
            "states_current": 0,
            "states_stale": 0,
            "mean_confidence": 0.0,
            "mean_visit_count": 0.0,
            "total_elements": 0,
            "distinct_url_keys": 0,
            "distinct_page_ids": 0,
            "fingerprint_signals": 0,
        }
    return {
        "states_current": sum(1 for state in states if state.status is StateStatus.CURRENT),
        "states_stale": sum(1 for state in states if state.status is StateStatus.STALE),
        "mean_confidence": round(sum(state.confidence for state in states) / len(states), 3),
        "mean_visit_count": round(sum(state.visit_count for state in states) / len(states), 2),
        "total_elements": sum(len(state.visible_elements) for state in states),
        "distinct_url_keys": len({state.fingerprint.url_key for state in states if state.fingerprint.url_key}),
        "distinct_page_ids": len({state.page_id for state in states if state.page_id}),
        "fingerprint_signals": sum(
            len(state.fingerprint.element_signature)
            + len(state.fingerprint.accessibility_signature)
            + len(state.fingerprint.form_signature)
            for state in states
        ),
    }


@router.get("/targets/{target_id}/graph", response_model=GraphResponse)
def get_graph(target_id: str) -> dict[str, Any]:
    """The learned behavioral graph - exactly what the dashboard renders."""
    source = source_or_404(target_id)
    return source.graph.to_graph_json()


@router.get("/targets/{target_id}/states")
def list_states(target_id: str) -> dict[str, Any]:
    source = source_or_404(target_id)
    states = sorted(source.graph.states.values(), key=lambda state: (-state.visit_count, state.state_id))
    return {
        "target_id": target_id,
        "count": len(states),
        "states": [state_payload(state) for state in states],
    }


@router.get("/targets/{target_id}/states/{state_id}")
def get_state(target_id: str, state_id: str) -> dict[str, Any]:
    source = source_or_404(target_id)
    state = source.graph.states.get(state_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"state {state_id!r} is not in the model for {target_id!r}")
    payload = state_payload(state, detail=True)
    payload["incoming_transitions"] = [
        transition_payload(transition) for transition in source.graph.transitions_to(state_id)
    ]
    payload["outgoing_transitions"] = [
        transition_payload(transition) for transition in source.graph.transitions_from(state_id)
    ]
    return payload
