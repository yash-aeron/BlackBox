"""Evidence records: the audit trail behind every learned claim.

Nothing in the behavioral model may assert a fact that cannot be traced to an
observation.  Evidence is what the dashboard shows when asked "why does
BlackBox believe this?".
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .action import ObservedEffect
from .base import now_iso, stable_id


class EvidenceKind(str, Enum):
    STATE_OBSERVATION = "STATE_OBSERVATION"
    ACTION_RESULT = "ACTION_RESULT"
    VALIDATION_MESSAGE = "VALIDATION_MESSAGE"
    SUCCESS_MESSAGE = "SUCCESS_MESSAGE"
    FAILURE = "FAILURE"
    BLOCKED_ACTION = "BLOCKED_ACTION"
    NETWORK_GUARD = "NETWORK_GUARD"
    HUMAN_DECISION = "HUMAN_DECISION"
    FAULT_INJECTION = "FAULT_INJECTION"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    evidence_id: str = ""
    kind: EvidenceKind = EvidenceKind.ACTION_RESULT
    experiment_id: str | None = None
    session_id: str | None = None
    step_index: int | None = None
    action_id: str | None = None
    source_state: str | None = None
    target_state: str | None = None
    transition_id: str | None = None
    effects: list[ObservedEffect] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    screenshot_refs: list[str] = Field(default_factory=list)
    message: str = ""
    notes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)

    def finalize(self) -> "Evidence":
        if not self.evidence_id:
            self.evidence_id = stable_id(
                "ev",
                self.kind.value,
                self.experiment_id,
                self.step_index,
                self.action_id,
                self.source_state,
                self.target_state,
            )
        return self

    def describe(self) -> str:
        return f"[{self.evidence_id}] {self.kind.value}: {self.message or self.action_id or ''}"
