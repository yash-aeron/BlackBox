"""Request and response models for the BlackBox HTTP API.

The shapes here are deliberately plain dictionaries-in-pydantic: the behavioral
model already has rich pydantic types of its own, and the API's job is to expose
them faithfully rather than to re-invent a parallel schema.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


# -- requests --------------------------------------------------------------
class RegisterTargetRequest(_Model):
    """Body of ``POST /api/targets``: the authorization record for one site."""

    target_id: str
    base_url: str
    allowed_origins: list[str] = Field(default_factory=list)
    mode: str = "SUPERVISED"
    max_steps: int = 300
    max_duration_seconds: float = 600.0
    allow_medium_actions: bool = False
    allow_high_actions: bool = False
    allow_critical_actions: bool = False
    authorized_by: str = ""
    notes: str = ""


class ExploreRequest(_Model):
    """Body of ``POST /api/targets/{target_id}/explore``."""

    max_actions: int | None = None
    max_seconds: float | None = None
    max_states: int | None = None
    configuration_label: str = "api"

    def overrides(self) -> dict[str, Any]:
        """Only the fields the caller actually set, in agent-native names."""
        values: dict[str, Any] = {"configuration_label": self.configuration_label or "api"}
        if self.max_actions is not None:
            values["max_actions"] = int(self.max_actions)
        if self.max_seconds is not None:
            values["max_duration_seconds"] = float(self.max_seconds)
        if self.max_states is not None:
            values["max_states"] = int(self.max_states)
        return values


class PredicateSpec(_Model):
    """One machine-checkable success condition."""

    kind: str
    value: str = ""
    target: str = ""

    def to_predicate(self) -> Any:
        """Build an assertion-engine ``Predicate``; raises ValueError on a bad kind."""
        from blackbox.agent.execution.assertions import Predicate, PredicateKind

        try:
            kind = PredicateKind(str(self.kind).strip().upper())
        except ValueError as exc:
            allowed = ", ".join(k.value for k in PredicateKind)
            raise ValueError(f"unknown predicate kind {self.kind!r}; allowed: {allowed}") from exc
        return Predicate(kind=kind, value=self.value, target=self.target)


class TaskRequest(_Model):
    """Body of ``POST /api/targets/{target_id}/tasks``."""

    text: str
    predicates: list[PredicateSpec] = Field(default_factory=list)
    allow_exploration: bool = True
    use_workflows: bool = True
    use_transitions: bool = True
    max_exploration_actions: int = 20


class ApprovalDecisionRequest(_Model):
    """Body of ``POST /api/targets/{target_id}/approvals/{request_id}``."""

    decision: str

    def normalized(self) -> str:
        return str(self.decision).strip().upper()


class ImportModelRequest(_Model):
    """Body of ``POST /api/targets/{target_id}/model/import``."""

    path: str


# -- responses -------------------------------------------------------------
class JobStatus(_Model):
    """A background exploration or task job."""

    job_id: str
    target_id: str = ""
    kind: str = "explore"
    status: str = "queued"  # queued | running | succeeded | failed | cancelled
    started_at: float | None = None
    finished_at: float | None = None
    duration_seconds: float | None = None
    error: str | None = None
    stop_requested: bool = False
    result: dict[str, Any] | None = None


class TargetSummary(_Model):
    """One registered target plus its last-known model counts."""

    target_id: str
    base_url: str = ""
    mode: str = "SUPERVISED"
    allowed_origins: list[str] = Field(default_factory=list)
    max_steps: int = 0
    max_duration_seconds: float = 0.0
    allow_medium_actions: bool = False
    allow_high_actions: bool = False
    allow_critical_actions: bool = False
    authorized_by: str = ""
    notes: str = ""
    registered: bool = True
    live: bool = False
    browser_alive: bool = False
    start_error: str | None = None
    model_version: int | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    job_counts: dict[str, int] = Field(default_factory=dict)
    pending_approvals: int = 0


class ModelSummary(_Model):
    """``BehavioralModel.summary()``-shaped counts for one target."""

    target_id: str = ""
    base_url: str = ""
    model_version: int = 0
    states: int = 0
    transitions: int = 0
    verified_transitions: int = 0
    workflows: int = 0
    constraints: int = 0
    hypotheses: int = 0
    pages: int = 0
    evidence: int = 0
    experiments: int = 0


class GraphResponse(_Model):
    """Exactly ``ApplicationGraph.to_graph_json()``, typed."""

    target_id: str = ""
    root_state_id: str | None = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class EventOut(_Model):
    """One live or persisted event, with a one-line human summary."""

    type: str = "event"
    ts: float = 0.0
    target_id: str | None = None
    kind: str | None = None
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "EventOut":
        kind = str(event.get("type") or event.get("kind") or "event")
        payload = {key: value for key, value in event.items() if key not in ("type", "ts", "target_id")}
        return cls(
            type=kind,
            kind=kind,
            ts=float(event.get("ts") or event.get("created_at") or 0.0),
            target_id=event.get("target_id"),
            summary=summarize_event(kind, event),
            payload=payload,
        )

    @classmethod
    def from_stored(cls, row: dict[str, Any]) -> "EventOut":
        """A row from ``Repository.read_events`` (payload is already decoded)."""
        payload = row.get("payload") or {}
        merged = {"target_id": row.get("target_id"), **(payload if isinstance(payload, dict) else {})}
        merged.setdefault("ts", row.get("created_at") or 0.0)
        merged.setdefault("type", row.get("kind") or "event")
        event = EventOut.from_event(merged)
        event.kind = row.get("kind") or event.kind
        event.type = row.get("kind") or event.type
        event.payload = {
            **(payload if isinstance(payload, dict) else {"payload": payload}),
            "event_id": row.get("event_id"),
            "session_id": row.get("session_id"),
            "created_at": row.get("created_at"),
        }
        return event


def _short(value: Any, limit: int = 90) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_event(kind: str, event: dict[str, Any]) -> str:
    """A one-line, human-readable summary of an event. Never raises."""
    try:
        if kind == "ranking":
            top = event.get("top") or []
            names = ", ".join(_short(item.get("action"), 40) for item in top[:3] if isinstance(item, dict))
            return f"{len(top)} ranked action(s) from {event.get('state_id', '?')}: {names}"
        if kind == "new_state":
            state = event.get("state") or {}
            return f"new state {state.get('label') or state.get('state_id')} at {_short(state.get('url'))}"
        if kind == "action_proposed":
            return (
                f"proposed {_short(event.get('action'), 60)} "
                f"[risk {event.get('risk')}, score {event.get('score')}]"
            )
        if kind == "action_result":
            transition = event.get("transition") or {}
            return (
                f"{_short(event.get('action'), 60)} -> {event.get('status')} "
                f"(verified={bool(event.get('verified'))}, {transition.get('status', 'no transition')})"
            )
        if kind in ("action_blocked", "action_denied"):
            return f"{_short(event.get('action'), 50)} {kind.split('_')[-1]}: {_short(event.get('reason'), 70)}"
        if kind == "approval_requested":
            return (
                f"approval {event.get('request_id')} for {_short(event.get('action'), 50)} "
                f"[{event.get('risk')}]: {_short(event.get('reason'), 60)}"
            )
        if kind == "approval_resolved":
            return f"approval {event.get('request_id')} -> {event.get('decision')}"
        if kind == "constraint_learned":
            constraint = event.get("constraint") or {}
            return f"constraint {constraint.get('kind', '')} {_short(constraint.get('expression'), 70)}"
        if kind == "hypothesis":
            hypothesis = event.get("hypothesis") or {}
            return f"hypothesis [{event.get('status') or hypothesis.get('status')}] {_short(hypothesis.get('statement'), 80)}"
        if kind == "origin_blocked":
            return f"origin blocked: {_short(event.get('reason') or event.get('url'), 80)}"
        if kind == "exploration_finished":
            return (
                f"exploration finished: {_short(event.get('stop_reason'), 60)} "
                f"({event.get('actions_executed')} actions, {event.get('new_states')} new states)"
            )
        if kind == "job_started":
            return f"job {event.get('job_id')} started ({event.get('job_kind')})"
        if kind == "job_finished":
            return f"job {event.get('job_id')} {event.get('status')} in {event.get('duration_seconds')}s"
        if kind == "session_start":
            return (
                f"session started: browser {event.get('browser_version')}, "
                f"model v{event.get('model_version')}, {event.get('states_loaded')} states loaded"
            )
        if kind == "network_blocked" or kind == "origin_blocked_request":
            return f"network guard blocked {_short(event.get('url'), 80)}"
        if kind == "stop_requested":
            return f"stop requested for {event.get('target_id')}"
        keys = ", ".join(sorted(event.keys())[:6])
        return f"{kind}: {keys}"
    except Exception:  # noqa: BLE001 - a summary must never break a stream
        return kind
