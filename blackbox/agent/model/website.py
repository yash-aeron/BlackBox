"""Website identity, model versioning and the exportable behavioral model.

A URL does not identify a version of a website.  The model therefore records
what was observed (title, version indicators, hashes) alongside the URL, and
keeps the previous model as historical evidence rather than overwriting it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import SCHEMA_VERSION, content_hash, now_iso
from .constraint import Constraint
from .evidence import Evidence
from .page import Page
from .state import WebsiteState
from .transition import Transition
from .workflow import Workflow


class WebsiteIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_id: str = ""
    base_url: str = ""
    allowed_origins: list[str] = Field(default_factory=list)
    root_title: str = ""
    observed_version_hints: list[str] = Field(default_factory=list)
    identity_hash: str = ""

    def finalize(self) -> "WebsiteIdentity":
        self.identity_hash = content_hash(
            self.base_url, self.root_title, sorted(self.observed_version_hints)
        )[:16]
        return self


class ModelVersion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    schema_version: int = SCHEMA_VERSION
    created_at: str = Field(default_factory=now_iso)
    session_id: str | None = None
    browser_version: str = ""
    prompt_version: str = "v1"
    parent_version: int | None = None
    notes: list[str] = Field(default_factory=list)


class HypothesisRecord(BaseModel):
    """Serialized hypothesis (the live logic lives in agent/learning)."""

    model_config = ConfigDict(extra="ignore")

    hypothesis_id: str
    statement: str
    status: str = "PROPOSED"
    confidence: float = 0.4
    supporting_experiments: list[str] = Field(default_factory=list)
    contradicting_experiments: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    details: dict[str, Any] = Field(default_factory=dict)


class BehavioralModel(BaseModel):
    """The central product: everything BlackBox has verified about a website."""

    model_config = ConfigDict(extra="ignore")

    website: WebsiteIdentity = Field(default_factory=WebsiteIdentity)
    model_version: ModelVersion = Field(default_factory=ModelVersion)
    states: list[WebsiteState] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    workflows: list[Workflow] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    hypotheses: list[HypothesisRecord] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    experiments: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "BehavioralModel":
        return cls.model_validate(payload)

    def summary(self) -> dict[str, Any]:
        verified = [t for t in self.transitions if t.status.value == "VERIFIED"]
        return {
            "target_id": self.website.target_id,
            "base_url": self.website.base_url,
            "model_version": self.model_version.version,
            "states": len(self.states),
            "transitions": len(self.transitions),
            "verified_transitions": len(verified),
            "workflows": len(self.workflows),
            "constraints": len(self.constraints),
            "hypotheses": len(self.hypotheses),
            "pages": len(self.pages),
            "evidence": len(self.evidence),
            "experiments": len(self.experiments),
        }
