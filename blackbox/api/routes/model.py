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


@router.get("/targets/{target_id}/transitions")
def list_transitions(
    target_id: str, status: str | None = None, source_state: str | None = None, limit: int = 500
) -> dict[str, Any]:
    source = source_or_404(target_id)
    transitions = list(source.graph.transitions.values())
    if status:
        wanted = status.strip().upper()
        transitions = [item for item in transitions if item.status.value == wanted]
    if source_state:
        transitions = [item for item in transitions if item.source_state == source_state]
    transitions.sort(key=lambda item: (-item.confidence, item.transition_id))
    return {
        "target_id": target_id,
        "count": len(transitions),
        "transitions": [transition_payload(item) for item in transitions[: max(1, limit)]],
    }


@router.get("/targets/{target_id}/transitions/{transition_id}")
def get_transition(target_id: str, transition_id: str) -> dict[str, Any]:
    source = source_or_404(target_id)
    transition = source.graph.transitions.get(transition_id)
    if transition is None:
        raise HTTPException(
            status_code=404, detail=f"transition {transition_id!r} is not in the model for {target_id!r}"
        )
    payload = transition_payload(transition, detail=True)
    evidence = [
        evidence_payload(item)
        for item in source.graph.evidence.values()
        if item.transition_id == transition_id
    ]
    payload["evidence"] = sorted(evidence, key=lambda item: item.get("created_at") or "")
    payload["source_state_label"] = _label(source, transition.source_state)
    payload["target_state_label"] = _label(source, transition.target_state)
    return payload


def _label(source: ModelSource, state_id: str) -> str:
    state = source.graph.states.get(state_id)
    return state.label() if state is not None else state_id


@router.get("/targets/{target_id}/workflows")
def list_workflows(target_id: str) -> dict[str, Any]:
    source = source_or_404(target_id)
    workflows = sorted(source.graph.workflows.values(), key=lambda item: (-item.confidence, item.workflow_id))
    return {
        "target_id": target_id,
        "count": len(workflows),
        "workflows": [workflow_payload(workflow) for workflow in workflows],
    }


@router.post("/targets/{target_id}/workflows/mine")
def mine_workflows(target_id: str) -> dict[str, Any]:
    """Re-mine parameterized workflows from the stored (or live) model.

    Declared before ``/workflows/{workflow_id}`` so the literal path wins.
    """
    source = source_or_404(target_id)
    if source.live and source.agent is not None:
        workflows = source.agent.mine_workflows()
    else:
        mined = WorkflowMiner().mine(source.graph)
        source.repository.clear_workflows()
        for workflow in mined:
            source.graph.upsert_workflow(workflow)
            source.repository.save_workflow(workflow)
        workflows = mined
    return {
        "target_id": target_id,
        "count": len(workflows),
        "workflows": [workflow_payload(workflow) for workflow in workflows],
    }


@router.get("/targets/{target_id}/workflows/{workflow_id}")
def get_workflow(target_id: str, workflow_id: str) -> dict[str, Any]:
    source = source_or_404(target_id)
    workflow = source.graph.workflows.get(workflow_id)
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"workflow {workflow_id!r} is not in the model for {target_id!r}"
        )
    payload = workflow_payload(workflow)
    payload["evidence"] = [
        evidence_payload(item)
        for item in source.graph.evidence.values()
        if item.evidence_id in set(workflow.evidence_ids)
    ]
    payload["states"] = [
        state_payload(state)
        for state_id in workflow.path_state_ids
        if (state := source.graph.states.get(state_id)) is not None
    ]
    return payload


@router.get("/targets/{target_id}/hypotheses")
def list_hypotheses(target_id: str) -> dict[str, Any]:
    source = source_or_404(target_id)
    records = _hypothesis_records(source)
    return {"target_id": target_id, "count": len(records), "hypotheses": records}


def _hypothesis_records(source: ModelSource) -> list[dict[str, Any]]:
    """Live hypotheses when an agent is running, otherwise the stored records."""
    if source.live and source.agent is not None:
        live = []
        for hypothesis in source.agent.hypotheses.all():
            live.append(
                {
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "statement": hypothesis.statement,
                    "kind": hypothesis.kind.value,
                    "status": hypothesis.status.value,
                    "confidence": hypothesis.confidence,
                    "subject": hypothesis.subject,
                    "prediction": hypothesis.prediction,
                    "supporting_experiments": list(hypothesis.supporting_experiments),
                    "contradicting_experiments": list(hypothesis.contradicting_experiments),
                    "evidence": list(hypothesis.evidence),
                    "details": hypothesis.details,
                    "created_at": hypothesis.created_at,
                    "updated_at": hypothesis.updated_at,
                }
            )
        if live:
            return live
    try:
        model = source.repository.load_model()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load hypotheses for %s: %s", source.target_id, exc)
        return []
    return [
        {
            "hypothesis_id": record.hypothesis_id,
            "statement": record.statement,
            "kind": str(record.details.get("kind", "")),
            "status": record.status,
            "confidence": record.confidence,
            "subject": str(record.details.get("subject", "")),
            "prediction": record.details.get("prediction", {}),
            "supporting_experiments": list(record.supporting_experiments),
            "contradicting_experiments": list(record.contradicting_experiments),
            "evidence": list(record.evidence),
            "details": record.details,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        for record in model.hypotheses
    ]


@router.get("/targets/{target_id}/constraints")
def list_constraints(target_id: str) -> dict[str, Any]:
    source = source_or_404(target_id)
    constraints = sorted(source.graph.constraints.values(), key=lambda item: (-item.confidence, item.constraint_id))
    return {
        "target_id": target_id,
        "count": len(constraints),
        "constraints": [constraint_payload(constraint) for constraint in constraints],
    }


@router.get("/targets/{target_id}/experiments")
def list_experiments(target_id: str, limit: int = 20) -> dict[str, Any]:
    source = source_or_404(target_id)
    experiments: list[dict[str, Any]] = []
    try:
        experiments = list(source.repository.load_model().experiments)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load experiments for %s: %s", target_id, exc)
    if source.live and source.agent is not None:
        seen = {str(item.get("experiment_id")) for item in experiments}
        live = [record.to_dict() for record in source.agent.ledger.records]
        experiments = [item for item in live if str(item.get("experiment_id")) not in seen] + experiments
    experiments = experiments[: max(1, limit)]
    return {
        "target_id": target_id,
        "count": len(experiments),
        "experiments": experiments,
        "summary": source.agent.ledger.summary() if source.live and source.agent is not None else {},
    }


@router.get("/targets/{target_id}/metrics")
def get_metrics(target_id: str) -> dict[str, Any]:
    """Counts plus fingerprint, observation, sandbox and network-guard stats."""
    source = source_or_404(target_id)
    stats = source.stats()
    slot = runtime.slot(target_id)
    agent = source.agent

    observer_stats: dict[str, Any] = {"observations": 0}
    mean_observation_ms = 0.0
    source_of_mean = "none"
    if agent is not None:
        try:
            observer_stats = agent.observer.stats()
            if observer_stats.get("observations"):
                mean_observation_ms = float(observer_stats.get("mean_capture_ms") or 0.0)
                source_of_mean = "agent.observer.stats()"
        except Exception:  # noqa: BLE001
            log.warning("observer stats unavailable for %s", target_id, exc_info=True)

    persisted_observations = 0
    persisted_mean_capture = 0.0
    try:
        row = source.repository.db.query_one(
            "SELECT COUNT(*) AS n, AVG(captured_ms) AS mean_ms FROM observations WHERE target_id = ?",
            (target_id,),
        )
        if row:
            persisted_observations = int(row.get("n") or 0)
            persisted_mean_capture = round(float(row.get("mean_ms") or 0.0), 2)
    except Exception:  # noqa: BLE001
        log.debug("could not read observation stats", exc_info=True)
    if source_of_mean == "none" and persisted_observations:
        mean_observation_ms = persisted_mean_capture
        source_of_mean = "observations table"

    sandbox: dict[str, Any] = {}
    network_guard: dict[str, Any] = {}
    if agent is not None:
        try:
            sandbox = agent.sandbox.snapshot()
        except Exception:  # noqa: BLE001
            sandbox = {}
        try:
            network_guard = agent.network_guard.summary()
        except Exception:  # noqa: BLE001
            network_guard = {}

    return {
        "target_id": target_id,
        "base_url": source.registration.base_url,
        "live": source.live,
        "model_version": stats.get("model_version", 0),
        "counts": {
            "states": stats.get("states", 0),
            "transitions": stats.get("transitions", 0),
            "verified_transitions": stats.get("verified_transitions", 0),
            "workflows": stats.get("workflows", 0),
            "constraints": stats.get("constraints", 0),
            "hypotheses": stats.get("hypotheses", 0),
            "evidence": stats.get("evidence", 0),
            "experiments": stats.get("experiments", 0),
            "pages": stats.get("pages", 0),
        },
        "fingerprints": _fingerprint_stats(source),
        "observations": observer_stats,
        "observations_persisted": persisted_observations,
        "observations_persisted_mean_ms": persisted_mean_capture,
        "mean_observation_ms": round(mean_observation_ms, 2),
        "mean_observation_ms_source": source_of_mean,
        "sandbox": sandbox,
        "sandbox_blocked_count": int(sandbox.get("blocked_count") or 0),
        "network_guard": network_guard,
        "events_by_type": dict(slot.event_counts),
        "events_buffered": len(slot.events),
        "jobs": slot.job_counts(),
        "pending_approvals": len(slot.pending_approvals),
    }


# -- model files -----------------------------------------------------------
@router.post("/targets/{target_id}/model/export")
def export_model(target_id: str) -> dict[str, Any]:
    """Write the learned model to ``<artifacts>/models/{target_id}-v{version}.json``."""
    source = source_or_404(target_id)
    version = source.repository.model_version
    safe_id = "".join(char for char in target_id if char.isalnum() or char in "-_.") or "target"
    destination = Path(runtime.settings.artifacts_dir) / "models" / f"{safe_id}-v{version}.json"
    if source.live and source.agent is not None:
        written = source.agent.export_model(destination)
    else:
        written = source.repository.export_model(destination)
    return {
        "target_id": target_id,
        "path": str(written),
        "model_version": version,
        "bytes": Path(written).stat().st_size if Path(written).exists() else 0,
    }


@router.post("/targets/{target_id}/model/import")
def import_model(target_id: str, payload: ImportModelRequest) -> dict[str, Any]:
    """Import a model JSON as a new version of this target's model."""
    source = source_or_404(target_id)
    path = Path(payload.path)
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"model file {payload.path!r} does not exist")
    try:
        if source.live and source.agent is not None:
            source.agent.import_model(path)
            version = source.agent.repository.model_version
            stats = source.agent.repository.stats()
        else:
            source.repository.import_model(path)
            version = source.repository.model_version
            stats = source.repository.stats()
    except Exception as exc:  # noqa: BLE001 - a bad import is a 400, not a 500
        raise HTTPException(status_code=400, detail=f"could not import {payload.path!r}: {type(exc).__name__}: {exc}") from exc
    return {
        "target_id": target_id,
        "imported_from": str(path),
        "new_version": version,
        "stats": stats,
    }


# -- screenshots -----------------------------------------------------------
@router.get("/targets/{target_id}/screenshots/{filename}")
def get_screenshot(target_id: str, filename: str) -> FileResponse:
    """Serve one captured PNG, refusing anything outside the screenshots dir."""
    root = (Path(runtime.settings.artifacts_dir) / "screenshots").resolve()
    try:
        candidate = (root / filename).resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"screenshot {filename!r} not found") from exc
    try:
        inside = candidate.is_relative_to(root)
    except AttributeError:  # pragma: no cover - Python < 3.9
        inside = root in candidate.parents
    if not inside or candidate == root:
        raise HTTPException(
            status_code=404, detail=f"screenshot {filename!r} is not inside {root} (path traversal refused)"
        )
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"screenshot {filename!r} not found in {root}")
    return FileResponse(candidate, media_type="image/png", filename=candidate.name)
