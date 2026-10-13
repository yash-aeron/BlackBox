"""Exploration and task execution: background jobs that never block a request.

Both endpoints return a ``job_id`` immediately and do the work in an
``asyncio`` task owned by the runtime.  Only one explore/task job runs per target
at a time: a second Chromium process driving the same site would corrupt the
model rather than extend it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from ..runtime import UnknownTarget, runtime
from ..schemas import ExploreRequest, JobStatus, TaskRequest

log = logging.getLogger(__name__)
router = APIRouter(tags=["execution"])


def _require_known(target_id: str) -> None:
    if not runtime.is_known(target_id):
        raise HTTPException(status_code=404, detail=f"target {target_id!r} is not registered")


def _reject_if_busy(target_id: str) -> None:
    active = runtime.active_job(target_id)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"target {target_id!r} already has a {active['kind']} job running "
                f"({active['job_id']}); stop it first (POST /api/targets/{target_id}/stop) "
                "or wait for it to finish."
            ),
        )


def _build_predicates(request: TaskRequest) -> list[Any]:
    predicates: list[Any] = []
    for spec in request.predicates:
        try:
            predicates.append(spec.to_predicate())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return predicates


@router.post("/targets/{target_id}/explore", status_code=202)
async def start_exploration(target_id: str, payload: ExploreRequest) -> dict[str, Any]:
    """Start an exploration in the background; returns ``{job_id}``."""
    _require_known(target_id)
    _reject_if_busy(target_id)
    overrides = payload.overrides()

    async def runner() -> dict[str, Any]:
        agent = await runtime.ensure_agent(target_id)
        config = agent.default_exploration_config(**overrides)
        result = await agent.explore(config)
        payload_out = result.to_dict()
        payload_out["graph"] = agent.graph_json()["stats"]
        return payload_out

    job = runtime.submit_job(target_id, "explore", runner)
    return {
        "job_id": job["job_id"],
        "target_id": target_id,
        "kind": "explore",
        "status": job["status"],
        "request": overrides,
    }


@router.post("/targets/{target_id}/tasks", status_code=202)
async def start_task(target_id: str, payload: TaskRequest) -> dict[str, Any]:
    """Start a natural-language task in the background; returns ``{job_id}``."""
    _require_known(target_id)
    _reject_if_busy(target_id)
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="task text must not be empty")
    predicates = _build_predicates(payload)
    options = {
        "allow_exploration": bool(payload.allow_exploration),
        "use_workflows": bool(payload.use_workflows),
        "use_transitions": bool(payload.use_transitions),
        "max_exploration_actions": max(0, int(payload.max_exploration_actions)),
    }

    async def runner() -> dict[str, Any]:
        agent = await runtime.ensure_agent(target_id)
        result = await agent.run_task(text, predicates=predicates, **options)
        return result.to_dict()

    def status_from(result: Any) -> str:
        if isinstance(result, dict) and result.get("success"):
            return "succeeded"
        return "failed"

    job = runtime.submit_job(target_id, "task", runner, status_from=status_from)
    return {
        "job_id": job["job_id"],
        "target_id": target_id,
        "kind": "task",
        "status": job["status"],
        "task": text,
        "predicates": [spec.model_dump() for spec in payload.predicates],
        "options": options,
    }


@router.get("/targets/{target_id}/jobs")
def list_jobs(target_id: str, limit: int = 20, kind: str | None = None) -> dict[str, Any]:
    """Recent jobs for this target, newest first."""
    _require_known(target_id)
    jobs = runtime.list_jobs(target_id, limit=limit, kind=kind)
    return {"target_id": target_id, "count": len(jobs), "jobs": jobs}


@router.get("/targets/{target_id}/jobs/{job_id}", response_model=JobStatus)
def get_job(target_id: str, job_id: str) -> dict[str, Any]:
    """Job status, and the full ``TaskResult.to_dict()`` once it has finished."""
    _require_known(target_id)
    job = runtime.get_job(target_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} is not known for target {target_id!r}")
    return job
