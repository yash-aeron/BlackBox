"""Verified state transitions: state + action -> observed target state."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .action import Action, ObservedEffect
from .base import now_iso, stable_id


class TransitionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    STALE = "STALE"


class Transition(BaseModel):
    """A behavioral claim of the form: in state A, action X leads to state B.

    A transition is only created from a real observation, and only promoted to
    VERIFIED when the predicted effect was actually observed.  Nothing here is
    ever inferred from source code, hidden state or a private API.
    """

    model_config = ConfigDict(extra="ignore")

    transition_id: str = ""
    source_state: str = ""
    action: Action
    target_state: str = ""
    observed_effects: list[ObservedEffect] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    status: TransitionStatus = TransitionStatus.PROPOSED
    execution_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def finalize(self) -> "Transition":
        if not self.transition_id:
            self.transition_id = stable_id(
                "t", self.source_state, self.action.action_id, self.target_state
            )
        return self

    @property
    def success_rate(self) -> float:
        if self.execution_count == 0:
            return 0.0
        return self.success_count / self.execution_count

    def record_success(self, evidence_id: str | None = None) -> None:
        self.execution_count += 1
        self.success_count += 1
        self.updated_at = now_iso()
        if evidence_id and evidence_id not in self.evidence_ids:
            self.evidence_ids.append(evidence_id)
        # Repeated independent success is what earns confidence.
        self.confidence = min(0.99, 0.5 + 0.15 * min(self.success_count, 3)) if self.success_count < 3 else min(
            0.99, self.confidence + 0.02
        )
        self.status = TransitionStatus.VERIFIED if self.success_count >= 1 else self.status

    def record_failure(self, evidence_id: str | None = None) -> None:
        self.execution_count += 1
        self.failure_count += 1
        self.updated_at = now_iso()
        if evidence_id and evidence_id not in self.evidence_ids:
            self.evidence_ids.append(evidence_id)
        if self.execution_count and self.success_rate < 0.5 and self.execution_count >= 2:
            self.status = TransitionStatus.REFUTED
        self.confidence = max(0.05, self.confidence - 0.2)

    def describe(self) -> str:
        return f"{self.source_state} --{self.action.describe()}--> {self.target_state}"

    def apply_decay(self, elapsed_steps: int = 1, decay_rate: float = 0.01) -> float:
        """Decay confidence over unobserved steps to reflect behavioral staleness risk."""
        if self.status is TransitionStatus.VERIFIED:
            self.confidence = round(max(0.40, self.confidence - (elapsed_steps * decay_rate)), 4)
            if self.confidence < 0.50:
                self.status = TransitionStatus.STALE
        return round(self.confidence, 4)

    def needs_reverification(self) -> bool:
        """Check if this transition is a candidate for change-detection re-verification."""
        return self.status is TransitionStatus.STALE or (
            self.status is TransitionStatus.VERIFIED and self.confidence < 0.65
        )

