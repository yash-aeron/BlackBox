"""Target registration, health, status, task library, stop and approvals."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from blackbox.agent.exploration.safety import ExecutionMode
from blackbox.agent.storage.targets import TargetRegistration
from blackbox.benchmarks.runner import TaskLibrary

from ..runtime import API_VERSION, RuntimeUnavailable, UnknownTarget, runtime
from ..schemas import ApprovalDecisionRequest, RegisterTargetRequest, TargetSummary

router = APIRouter(tags=["targets"])

DECISIONS = ("APPROVE", "REJECT", "PAUSE", "STOP")


def origin_of(url: str) -> str:
    """``scheme://host[:port]`` with a lower-cased host, as the sandbox sees it."""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    port = ""
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        port = ""
    return f"{parsed.scheme}://{(parsed.hostname or '').lower()}{port}"


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness plus a summary of which browsers this process has open."""
    live: list[dict[str, Any]] = []
    for registration in runtime.list_registrations():
        agent = runtime.live_agent(registration.target_id)
        if agent is None:
            continue
        live.append(
            {
                "target_id": registration.target_id,
                "alive": agent.manager.alive(),
                "browser_version": agent.manager.browser_version,
                "running": agent.running,
            }
        )
    return {
        "status": "ok",
        "browser": {
            "agents": len(live),
            "live": live,
            "browser_version": next((item["browser_version"] for item in live if item["browser_version"]), ""),
        },
        "version": API_VERSION,
        "database": runtime.settings.database_dsn,
        "artifacts_dir": str(runtime.settings.artifacts_dir),
        "targets": len(runtime.list_registrations()),
    }


@router.get("/targets")
def list_targets() -> dict[str, Any]:
    """Every registered target with its last-known model counts."""
    summaries = runtime.target_summaries()
    return {"targets": summaries, "count": len(summaries)}


@router.post("/targets", response_model=TargetSummary, status_code=201)
def register_target(payload: RegisterTargetRequest) -> dict[str, Any]:
    """Register one target: this is the record of what a human authorized."""
    origins = [str(origin).strip() for origin in payload.allowed_origins if str(origin).strip()]
    if not origins:
        raise HTTPException(
            status_code=400,
            detail=(
                "allowed_origins must not be empty: BlackBox only touches origins a human "
                "explicitly authorized, so name the origin(s) it may open."
            ),
        )
    base_origin = origin_of(payload.base_url)
    normalized: list[str] = []
    for origin in origins:
        candidate = origin_of(origin)
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    normalized.sort()
    if base_origin not in normalized:
        raise HTTPException(
            status_code=400,
            detail=(
                f"base_url origin {base_origin!r} is not among allowed_origins {normalized}. "
                "Add the base_url origin so the agent is allowed to open its own start page."
            ),
        )

    try:
        mode = ExecutionMode(str(payload.mode).strip().upper())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ExecutionMode)
        raise HTTPException(status_code=400, detail=f"unknown mode {payload.mode!r}; allowed: {allowed}") from exc

    try:
        registration = TargetRegistration(
            target_id=payload.target_id,
            base_url=payload.base_url,
            allowed_origins=origins,
            mode=mode,
            max_steps=int(payload.max_steps),
            max_duration_seconds=float(payload.max_duration_seconds),
            allow_medium_actions=bool(payload.allow_medium_actions),
            allow_high_actions=bool(payload.allow_high_actions),
            allow_critical_actions=bool(payload.allow_critical_actions),
            authorized_by=payload.authorized_by,
            notes=payload.notes,
        )
    except ValidationError as exc:
        problems = "; ".join(str(error.get("msg")) for error in exc.errors())
        raise HTTPException(status_code=400, detail=f"invalid target registration: {problems}") from exc

    runtime.register(registration)
    return runtime.target_summary(registration.target_id, registration=registration)


@router.get("/targets/{target_id}/status")
async def target_status(target_id: str) -> dict[str, Any]:
    """``agent.status().to_dict()`` - starting the agent (and its browser) if needed.

    A browser that cannot start is reported inside the status body rather than as
    an error: the dashboard needs to be told *why* nothing is happening.
    """
    try:
        runtime.require_registration(target_id)
    except UnknownTarget as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        agent = await runtime.ensure_agent(target_id)
    except RuntimeUnavailable as exc:
        return runtime.degraded_status(target_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - never crash on a browser problem
        return runtime.degraded_status(target_id, f"{type(exc).__name__}: {exc}")

    try:
        return agent.status().to_dict()
    except Exception as exc:  # noqa: BLE001
        return runtime.degraded_status(target_id, f"{type(exc).__name__}: {exc}")


@router.get("/targets/{target_id}/tasks/available")
def available_tasks(target_id: str) -> dict[str, Any]:
    """The benchmark task definitions for this target, if a task file exists."""
    if not runtime.is_known(target_id):
        raise HTTPException(status_code=404, detail=f"target {target_id!r} is not registered")
    library = TaskLibrary()
    error: str | None = None
    tasks: list[dict[str, Any]] = []
    try:
        tasks = [task.to_dict() for task in library.load(target_id)]
    except FileNotFoundError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    return {
        "target_id": target_id,
        "count": len(tasks),
        "tasks": tasks,
        "available_targets": library.available_targets(),
        "error": error,
    }


@router.post("/targets/{target_id}/stop")
async def stop_target(target_id: str) -> dict[str, Any]:
    """Ask the running exploration to stop at the next safe point."""
    try:
        return await runtime.stop(target_id)
    except UnknownTarget as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/targets/{target_id}/approvals")
def list_approvals(target_id: str) -> dict[str, Any]:
    """Pending human approvals, from the broker and the runtime's listener."""
    if not runtime.is_known(target_id):
        raise HTTPException(status_code=404, detail=f"target {target_id!r} is not registered")
    slot = runtime.slot(target_id)
    agent = runtime.live_agent(target_id)
    pending: dict[str, dict[str, Any]] = {}
    if agent is not None:
        for request in agent.approvals.pending.values():
            pending[request.request_id] = {
                "request_id": request.request_id,
                "action": request.action,
                "target": request.target,
                "reason": request.reason,
                "expected_effect": request.expected_effect,
                "risk": request.risk,
                "evidence": list(request.evidence or []),
                "created_at": request.created_at,
            }
    for request_id, payload in slot.pending_approvals.items():
        pending.setdefault(request_id, payload)
    summary = agent.approvals.summary() if agent is not None else {"pending": [], "decisions": 0, "approved": 0, "rejected": 0}
    return {
        "target_id": target_id,
        "live": agent is not None,
        "unattended": bool(runtime.settings.unattended),
        "count": len(pending),
        "pending": sorted(pending.values(), key=lambda item: item.get("created_at") or 0),
        "summary": summary,
    }


@router.post("/targets/{target_id}/approvals/{request_id}")
def resolve_approval(target_id: str, request_id: str, payload: ApprovalDecisionRequest) -> dict[str, Any]:
    """Resolve one pending approval; PAUSE/STOP also stop the explorer."""
    decision = payload.normalized()
    if decision not in DECISIONS:
        raise HTTPException(
            status_code=400, detail=f"unknown decision {payload.decision!r}; allowed: {', '.join(DECISIONS)}"
        )
    if not runtime.is_known(target_id):
        raise HTTPException(status_code=404, detail=f"target {target_id!r} is not registered")

    slot = runtime.slot(target_id)
    agent = runtime.live_agent(target_id)
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no live agent for {target_id!r}: approvals only exist while an agent is running. "
                "Start an exploration or a task first."
            ),
        )

    explorer = getattr(agent, "explorer", None)
    if decision in ("PAUSE", "STOP") and explorer is not None:
        explorer.stopped = True
    resolved = bool(agent.approvals.resolve(request_id, decision))
    slot.pending_approvals.pop(request_id, None)
    slot.publish(
        {
            "type": "approval_resolved",
            "request_id": request_id,
            "decision": decision,
            "resolved": resolved,
        }
    )
    if not resolved and decision not in ("PAUSE", "STOP"):
        raise HTTPException(
            status_code=404, detail=f"approval {request_id!r} is not pending for target {target_id!r}"
        )
    return {
        "target_id": target_id,
        "request_id": request_id,
        "decision": decision,
        "resolved": resolved,
        "stop_requested": decision in ("PAUSE", "STOP"),
    }
