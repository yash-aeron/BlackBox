"""Learned constraints: preconditions, postconditions and validation rules.

A constraint is only recorded when the browser actually showed us something:
a validation message, a disabled control, a blocked export.  Each one keeps the
evidence that produced it so a claim can be audited or retracted.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .base import now_iso, stable_id


class ConstraintKind(str, Enum):
    PRECONDITION = "PRECONDITION"
    POSTCONDITION = "POSTCONDITION"
    VALIDATION = "VALIDATION"
    INVARIANT = "INVARIANT"


class ConstraintScope(str, Enum):
    ACTION = "ACTION"
    ELEMENT = "ELEMENT"
    STATE = "STATE"
    WORKFLOW = "WORKFLOW"


class ConstraintStatus(str, Enum):
    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    STALE = "STALE"


class Constraint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    constraint_id: str = ""
    kind: ConstraintKind = ConstraintKind.PRECONDITION
    scope: ConstraintScope = ConstraintScope.ACTION
    subject: str = ""
    expression: str = ""
    condition: dict = Field(default_factory=dict)
    message: str | None = None
    status: ConstraintStatus = ConstraintStatus.PROPOSED
    confidence: float = 0.4
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def finalize(self) -> "Constraint":
        if not self.constraint_id:
            self.constraint_id = stable_id("c", self.kind.value, self.subject, self.expression)
        return self

    def support(self, evidence_id: str, *, verified: bool = False) -> None:
        if evidence_id not in self.supporting_evidence:
            self.supporting_evidence.append(evidence_id)
        self.confidence = min(0.99, self.confidence + (0.3 if verified else 0.15))
        if verified:
            self.status = ConstraintStatus.VERIFIED
        elif self.status is ConstraintStatus.PROPOSED:
            self.status = ConstraintStatus.SUPPORTED
        self.updated_at = now_iso()

    def contradict(self, evidence_id: str) -> None:
        if evidence_id not in self.contradicting_evidence:
            self.contradicting_evidence.append(evidence_id)
        self.confidence = max(0.05, self.confidence - 0.3)
        if self.confidence < 0.25:
            self.status = ConstraintStatus.REFUTED
        self.updated_at = now_iso()

    def describe(self) -> str:
        suffix = f" ({self.message})" if self.message else ""
        return f"{self.kind.value} {self.subject}: {self.expression}{suffix}"
