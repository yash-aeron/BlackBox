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
