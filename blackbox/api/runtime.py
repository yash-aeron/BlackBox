"""The API runtime: one long-lived owner of agents, events and jobs.

The agent itself knows nothing about HTTP.  ``AgentRuntime`` is the process-wide
singleton that:

* owns at most one ``BlackBoxAgent`` per target and starts it lazily, so a REST
  call that only reads a stored model never launches a browser;
* keeps a bounded in-memory deque of live events per target and fans them out to
  any number of SSE subscribers;
* keeps a bounded job registry (``job_id -> status``) and runs exploration and
  task work as ``asyncio`` tasks, so a long run never blocks an HTTP response;
* registers an approvals listener on every agent so the dashboard can list and
  resolve the risky actions the safety gate has paused.

Reading a model for a target that has no live agent goes straight to the
repository: ``GET /graph`` on a stored model must not cost a Chromium launch, and
must not write anything to the shared model database.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from blackbox.agent.config import Settings
from blackbox.agent.exploration.safety import ApprovalRequest
from blackbox.agent.main import BlackBoxAgent
from blackbox.agent.model.graph import ApplicationGraph
from blackbox.agent.storage.database import Database, loads
from blackbox.agent.storage.repository import Repository
from blackbox.agent.storage.targets import TargetRegistration

log = logging.getLogger(__name__)

API_VERSION = "0.1.0"
EVENT_BUFFER = 500
JOB_HISTORY = 50
SUBSCRIBER_QUEUE = 256

JobRunner = Callable[[], Awaitable[Any]]


class UnknownTarget(KeyError):
    """The target is neither registered in this process nor stored in the database."""


class RuntimeUnavailable(RuntimeError):
    """A live agent could not be created or started for this target."""


@dataclass
class ModelSource:
    """Whatever can answer questions about one target's learned model."""

    target_id: str
    registration: TargetRegistration
    graph: ApplicationGraph
    repository: Repository
    agent: BlackBoxAgent | None = None

    @property
    def live(self) -> bool:
        return self.agent is not None

    def stats(self) -> dict[str, Any]:
        if self.agent is not None:
            return self.agent.repository.stats()
        return self.repository.stats()


class AgentSlot:
    """Per-target state: the agent (if any), live events, subscribers, jobs."""

    def __init__(self, target_id: str) -> None:
        self.target_id = target_id
        self.agent: BlackBoxAgent | None = None
        self.registration: TargetRegistration | None = None
        self.start_error: str | None = None
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENT_BUFFER)
        self.event_counts: Counter[str] = Counter()
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.job_order: deque[str] = deque(maxlen=JOB_HISTORY)
        self.pending_approvals: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()

    # -- events ------------------------------------------------------------
    def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("target_id", self.target_id)
        event.setdefault("ts", time.time())
        self.events.append(event)
        self.event_counts[str(event.get("type", "event"))] += 1
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled browser tab must never stall the agent.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    def subscribe(self, *, replay: int = 0, queue_size: int = SUBSCRIBER_QUEUE) -> tuple[asyncio.Queue, list[dict]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        backlog = list(self.events)[-replay:] if replay > 0 else []
        self.subscribers.add(queue)
        return queue, backlog

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    # -- jobs --------------------------------------------------------------
    def job_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter(str(job.get("status")) for job in self.jobs.values())
        return dict(counts)

    def active_jobs(self, kinds: tuple[str, ...] = ("explore", "task")) -> list[dict[str, Any]]:
        return [
            job
            for job in self.jobs.values()
            if job.get("kind") in kinds and job.get("status") in ("queued", "running")
        ]


class AgentRuntime:
    """Process-wide owner of agents, live events and background jobs."""

    _instance: "AgentRuntime | None" = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._slots: dict[str, AgentSlot] = {}
        self._db: Database | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    # -- singleton ---------------------------------------------------------
    @classmethod
    def instance(cls) -> "AgentRuntime":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # -- database ----------------------------------------------------------
    @property
    def database(self) -> Database:
        """One read/write handle used only for registry and read-only reads.

        Agents open their own connections, exactly as the CLI does.
        """
        if self._db is None:
            self._db = Database(self.settings.database_dsn)
            self._db.connect()
        return self._db

    # -- slots and registrations ------------------------------------------
    def slot(self, target_id: str) -> AgentSlot:
        slot = self._slots.get(target_id)
        if slot is None:
            slot = AgentSlot(target_id)
            self._slots[target_id] = slot
        return slot

    def register(self, registration: TargetRegistration) -> TargetRegistration:
        """Remember a target for this process.

        Registration is deliberately in-memory: it records a human decision, and
        the agent persists it when (and only when) it actually starts work.
        """
        registration.updated_at = registration.updated_at or time.time()
        slot = self.slot(registration.target_id)
        slot.registration = registration
        return registration

    def stored_registration(self, target_id: str) -> TargetRegistration | None:
        try:
            row = self.database.query_one("SELECT payload FROM targets WHERE target_id = ?", (target_id,))
        except Exception:  # noqa: BLE001 - a missing database is not a crash
            log.warning("could not read targets table", exc_info=True)
            return None
        payload = loads(row["payload"]) if row else None
        if not payload:
            return None
        try:
            return TargetRegistration.model_validate(payload)
        except Exception:  # noqa: BLE001
            log.warning("stored target %s could not be parsed", target_id, exc_info=True)
            return None

    def registration(self, target_id: str) -> TargetRegistration | None:
        slot = self._slots.get(target_id)
        if slot is not None and slot.registration is not None:
            return slot.registration
        return self.stored_registration(target_id)

    def require_registration(self, target_id: str) -> TargetRegistration:
        registration = self.registration(target_id)
        if registration is None:
            raise UnknownTarget(
                f"target {target_id!r} is not registered. Register it with POST /api/targets first."
            )
        return registration

    def is_known(self, target_id: str) -> bool:
        if target_id in self._slots and self._slots[target_id].registration is not None:
            return True
        return self.stored_registration(target_id) is not None

    def list_registrations(self) -> list[TargetRegistration]:
        found: dict[str, TargetRegistration] = {}
        try:
            for row in self.database.query("SELECT payload FROM targets ORDER BY target_id"):
                payload = loads(row["payload"])
                if not payload:
                    continue
                with contextlib.suppress(Exception):
                    found[str(payload["target_id"])] = TargetRegistration.model_validate(payload)
        except Exception:  # noqa: BLE001 - listing must degrade, not fail
            log.warning("could not list stored targets", exc_info=True)
        for target_id, slot in self._slots.items():
            if slot.registration is not None:
                found[target_id] = slot.registration
        return [found[key] for key in sorted(found)]

    # -- model reads -------------------------------------------------------
    def readonly_repository(self, target_id: str) -> Repository:
        """A repository positioned on the newest stored model version (read-only)."""
        repository = Repository(self.database, target_id)
        version = repository.latest_version()
        repository.model_version = version or 1
        return repository

    def model_source(self, target_id: str) -> ModelSource:
        """The live agent's graph when there is one, otherwise the stored model."""
        slot = self._slots.get(target_id)
        if slot is not None and slot.agent is not None:
            agent = slot.agent
            return ModelSource(
                target_id=target_id,
                registration=agent.registration,
                graph=agent.graph,
                repository=agent.repository,
                agent=agent,
            )
        registration = self.require_registration(target_id)
        repository = self.readonly_repository(target_id)
        return ModelSource(
            target_id=target_id,
            registration=registration,
            graph=repository.load_graph(),
            repository=repository,
        )

    # -- agent lifecycle ---------------------------------------------------
    def live_agent(self, target_id: str) -> BlackBoxAgent | None:
        slot = self._slots.get(target_id)
        return slot.agent if slot is not None else None

    def _build_agent(self, slot: AgentSlot, registration: TargetRegistration) -> BlackBoxAgent:
        agent = BlackBoxAgent(self.settings, registration, on_event=self._event_sink(slot))
        self._attach_approvals(slot, agent)
        slot.agent = agent
        slot.registration = registration
        return agent

    def _event_sink(self, slot: AgentSlot) -> Callable[[dict[str, Any]], None]:
        def sink(event: dict[str, Any]) -> None:
            try:
                slot.publish(dict(event))
            except Exception:  # noqa: BLE001 - the sink must never break the agent
                log.warning("event sink failed for %s", slot.target_id, exc_info=True)

        return sink

    def _attach_approvals(self, slot: AgentSlot, agent: BlackBoxAgent) -> None:
        def listener(request: ApprovalRequest) -> None:
            payload = {
                "request_id": request.request_id,
                "action": request.action,
                "target": request.target,
                "reason": request.reason,
                "expected_effect": request.expected_effect,
                "risk": request.risk,
                "evidence": list(request.evidence or []),
                "created_at": request.created_at,
            }
            slot.pending_approvals[request.request_id] = payload
            slot.publish({"type": "approval_requested", **payload})

        agent.approvals.listeners.append(listener)

    async def ensure_agent(self, target_id: str, *, start: bool = True) -> BlackBoxAgent:
        """Create (and by default start) this target's agent, at most once."""
        registration = self.require_registration(target_id)
        slot = self.slot(target_id)
        async with slot.lock:
            agent = slot.agent
            if agent is None:
                try:
                    agent = self._build_agent(slot, registration)
                except Exception as exc:  # noqa: BLE001 - report it as an API error
                    slot.start_error = f"{type(exc).__name__}: {exc}"
                    raise RuntimeUnavailable(
                        f"could not initialize the agent for {target_id!r}: {slot.start_error}"
                    ) from exc
            if start and not agent.running:
                try:
                    await agent.start()
                    slot.start_error = None
                except Exception as exc:  # noqa: BLE001 - e.g. no browser available
                    slot.start_error = f"{type(exc).__name__}: {exc}"
                    raise RuntimeUnavailable(
                        f"could not start the browser for {target_id!r}: {slot.start_error}"
                    ) from exc
            return agent

    async def stop(self, target_id: str) -> dict[str, Any]:
        """Ask the running explorer to stop at the next safe point."""
        registration = self.require_registration(target_id)
        slot = self.slot(target_id)
        agent = slot.agent
        explorer = getattr(agent, "explorer", None) if agent is not None else None
        if explorer is not None:
            try:
                explorer.stopped = True
            except Exception:  # noqa: BLE001
                log.warning("could not set explorer.stopped for %s", target_id, exc_info=True)
        for job in slot.active_jobs():
            job["stop_requested"] = True
        detail = {
            "target_id": registration.target_id,
            "stop_requested": True,
            "agent_running": bool(agent is not None and agent.running),
            "explorer_present": explorer is not None,
            "active_jobs": [job["job_id"] for job in slot.active_jobs()],
        }
        slot.publish({"type": "stop_requested", **detail})
        return detail

    async def shutdown(self) -> None:
        """Cancel background work and close every browser this process opened."""
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for slot in list(self._slots.values()):
            agent = slot.agent
            if agent is not None:
                with contextlib.suppress(Exception):
                    await agent.close()
            slot.agent = None
        self._tasks.clear()
        if self._db is not None:
            with contextlib.suppress(Exception):
                self._db.close()
            self._db = None

    # -- jobs --------------------------------------------------------------
    def submit_job(
        self,
        target_id: str,
        kind: str,
        runner: JobRunner,
        *,
        status_from: Callable[[Any], str] | None = None,
    ) -> dict[str, Any]:
        """Queue background work and return its registry entry immediately."""
        slot = self.slot(target_id)
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        job: dict[str, Any] = {
            "job_id": job_id,
            "target_id": target_id,
            "kind": kind,
            "status": "queued",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "stop_requested": False,
            "result": None,
            "_status_from": status_from,
        }
        slot.jobs[job_id] = job
        slot.job_order.append(job_id)
        # job_order is a bounded deque, so the registry stays bounded too.
        for stale in list(slot.jobs):
            if stale not in slot.job_order:
                slot.jobs.pop(stale, None)
        task = asyncio.create_task(self._run_job(slot, job, runner), name=f"{kind}:{target_id}:{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run_job(self, slot: AgentSlot, job: dict[str, Any], runner: JobRunner) -> None:
        job["status"] = "running"
        job["started_at"] = time.time()
        slot.publish({"type": "job_started", "job_id": job["job_id"], "job_kind": job["kind"]})
        try:
            result = await runner()
            job["result"] = result if isinstance(result, (dict, list)) else {"value": result}
            chooser = job.get("_status_from")
            status = "succeeded"
            if chooser is not None:
                with contextlib.suppress(Exception):
                    status = str(chooser(result))
            job["status"] = status
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["error"] = "job was cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - a failed job is a status, not a crash
            job["status"] = "failed"
            job["error"] = f"{type(exc).__name__}: {exc}"
            log.exception("job %s failed", job["job_id"])
        finally:
            job["finished_at"] = time.time()
            started = job.get("started_at") or job["finished_at"]
            job["duration_seconds"] = round(job["finished_at"] - started, 2)
            slot.publish(
                {
                    "type": "job_finished",
                    "job_id": job["job_id"],
                    "job_kind": job["kind"],
                    "status": job["status"],
                    "duration_seconds": job["duration_seconds"],
                    "error": job["error"],
                }
            )

    @staticmethod
    def public_job(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items() if not key.startswith("_")}

    def get_job(self, target_id: str, job_id: str) -> dict[str, Any] | None:
        slot = self._slots.get(target_id)
        if slot is None:
            return None
        job = slot.jobs.get(job_id)
        return self.public_job(job) if job is not None else None

    def list_jobs(self, target_id: str, *, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
        slot = self._slots.get(target_id)
        if slot is None:
            return []
        jobs = [slot.jobs[job_id] for job_id in reversed(slot.job_order) if job_id in slot.jobs]
        if kind:
            jobs = [job for job in jobs if job.get("kind") == kind]
        return [self.public_job(job) for job in jobs[: max(1, limit)]]

    def active_job(self, target_id: str, kinds: tuple[str, ...] = ("explore", "task")) -> dict[str, Any] | None:
        slot = self._slots.get(target_id)
        if slot is None:
            return None
        active = slot.active_jobs(kinds)
        return self.public_job(active[0]) if active else None

    # -- targets overview --------------------------------------------------
    def target_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for registration in self.list_registrations():
            summaries.append(self.target_summary(registration.target_id, registration=registration))
        return summaries

    def target_summary(
        self, target_id: str, *, registration: TargetRegistration | None = None
    ) -> dict[str, Any]:
        registration = registration or self.require_registration(target_id)
        slot = self._slots.get(target_id) or self.slot(target_id)
        agent = slot.agent
        stats: dict[str, Any] = {}
        model_version: int | None = None
        try:
            if agent is not None:
                stats = agent.repository.stats()
                model_version = agent.repository.model_version
            else:
                repository = self.readonly_repository(target_id)
                stats = repository.stats()
                model_version = repository.model_version
        except Exception:  # noqa: BLE001 - a broken model must not break the target list
            log.warning("could not read stats for %s", target_id, exc_info=True)
        return {
            "target_id": registration.target_id,
            "base_url": registration.base_url,
            "mode": registration.mode.value,
            "allowed_origins": registration.normalized_origins(),
            "max_steps": registration.max_steps,
            "max_duration_seconds": registration.max_duration_seconds,
            "allow_medium_actions": registration.allow_medium_actions,
            "allow_high_actions": registration.allow_high_actions,
            "allow_critical_actions": registration.allow_critical_actions,
            "authorized_by": registration.authorized_by,
            "notes": registration.notes,
            "registered": True,
            "live": agent is not None,
            "browser_alive": bool(agent is not None and agent.manager.alive()),
            "start_error": slot.start_error,
            "model_version": model_version,
            "stats": stats,
            "job_counts": slot.job_counts(),
            "pending_approvals": len(slot.pending_approvals)
            + (len(agent.approvals.pending) if agent is not None else 0),
        }

    def degraded_status(self, target_id: str, error: str | None = None) -> dict[str, Any]:
        """A status-shaped answer for a target whose browser will not start."""
        registration = self.require_registration(target_id)
        slot = self._slots.get(target_id)
        agent = slot.agent if slot is not None else None
        try:
            stats = (
                agent.repository.stats() if agent is not None else self.readonly_repository(target_id).stats()
            )
        except Exception:  # noqa: BLE001
            stats = {}
        browser: dict[str, Any] = {"alive": False, "browser_version": "", "start_error": error}
        sandbox: dict[str, Any] = {}
        policy: dict[str, Any] = {}
        if agent is not None:
            with contextlib.suppress(Exception):
                browser = agent.manager.health_report()
            with contextlib.suppress(Exception):
                sandbox = agent.sandbox.snapshot()
            with contextlib.suppress(Exception):
                policy = {"approvals": agent.approvals.summary()}
        return {
            "target_id": registration.target_id,
            "base_url": registration.base_url,
            "mode": registration.mode.value,
            "browser": browser,
            "model": stats,
            "sandbox": sandbox,
            "policy": policy,
            "settings": self.settings.describe(),
            "degraded": True,
            "start_error": error,
        }


# The process-wide runtime the routers use.  Constructing it reads the
# environment (database DSN, artifacts dir) but touches no file until first use.
runtime = AgentRuntime.instance()
