"""Evidence and logs: the audit trail behind every learned claim."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from ..runtime import UnknownTarget, runtime
from ..schemas import EventOut
from .model import evidence_payload, source_or_404

log = logging.getLogger(__name__)
router = APIRouter(tags=["evidence"])


@router.get("/targets/{target_id}/evidence")
def list_evidence(target_id: str, transition_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Evidence records, newest first, optionally for one transition."""
    source = source_or_404(target_id)
    records = list(source.graph.evidence.values())
    if transition_id:
        records = [record for record in records if record.transition_id == transition_id]
    records.sort(key=lambda record: record.created_at or "", reverse=True)
    total = len(records)
    return {
        "target_id": target_id,
        "count": min(total, max(1, limit)),
        "total": total,
        "transition_id": transition_id,
        "evidence": [evidence_payload(record) for record in records[: max(1, limit)]],
    }


@router.get("/targets/{target_id}/evidence/{evidence_id}")
def get_evidence(target_id: str, evidence_id: str) -> dict[str, Any]:
    source = source_or_404(target_id)
    record = source.graph.evidence.get(evidence_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"evidence {evidence_id!r} is not in the model for {target_id!r}"
        )
    payload = evidence_payload(record)
    transition = source.graph.transitions.get(record.transition_id or "")
    payload["transition"] = (
        {
            "transition_id": transition.transition_id,
            "action": transition.action.describe(),
            "status": transition.status.value,
            "confidence": transition.confidence,
        }
        if transition is not None
        else None
    )
    payload["states"] = {
        state_id: (source.graph.states[state_id].label() if state_id in source.graph.states else state_id)
        for state_id in (record.source_state, record.target_state)
        if state_id
    }
    return payload


@router.get("/targets/{target_id}/logs")
def get_logs(target_id: str, limit: int = 200, kind: str | None = None) -> dict[str, Any]:
    """Persisted events from the repository, newest first."""
    try:
        source = runtime.model_source(target_id)
    except UnknownTarget as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository = source.repository
    events: list[dict[str, Any]] = []
    error: str | None = None
    try:
        events = [
            EventOut.from_stored(row).model_dump()
            for row in repository.read_events(limit=max(1, limit), kind=kind)
        ]
    except Exception as exc:  # noqa: BLE001 - a broken log read is not a 500
        error = f"{type(exc).__name__}: {exc}"
        log.warning("could not read events for %s: %s", target_id, exc)
    return {
        "target_id": target_id,
        "count": len(events),
        "kind": kind,
        "limit": max(1, limit),
        "events": events,
        "error": error,
        "kinds": _event_kinds(repository),
    }


def _event_kinds(repository: Any) -> list[dict[str, Any]]:
    try:
        rows = repository.db.query(
            "SELECT kind, COUNT(*) AS n FROM events WHERE target_id = ? GROUP BY kind ORDER BY n DESC",
            (repository.target_id,),
        )
        return [{"kind": row["kind"], "count": int(row["n"])} for row in rows]
    except Exception:  # noqa: BLE001
        return []
