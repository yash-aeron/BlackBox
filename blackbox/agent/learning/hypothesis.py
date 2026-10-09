"""Hypothesis engine: the difference between observing and believing.

BlackBox never stores a guess as knowledge.  A hypothesis is proposed from an
observation, gains support from experiments, and only becomes VERIFIED when an
experiment produced the predicted outcome.  Contradictions are recorded too, and
a hypothesis whose world changed goes STALE rather than being deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..model.base import now_iso, stable_id


class HypothesisStatus(str, Enum):
    PROPOSED = "PROPOSED"
    SUPPORTED = "SUPPORTED"
    VERIFIED = "VERIFIED"
    REFUTED = "REFUTED"
    STALE = "STALE"


class HypothesisKind(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    ENABLING = "ENABLING"
    VALIDATION = "VALIDATION"
    NAVIGATION = "NAVIGATION"
    EFFECT = "EFFECT"
    CAPABILITY = "CAPABILITY"


@dataclass
class Hypothesis:
    statement: str
    kind: HypothesisKind = HypothesisKind.REQUIREMENT
    hypothesis_id: str = ""
    subject: str = ""
    prediction: dict[str, Any] = field(default_factory=dict)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float = 0.35
    supporting_experiments: list[str] = field(default_factory=list)
    contradicting_experiments: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.hypothesis_id:
            self.hypothesis_id = stable_id("h", self.kind.value, self.statement, self.subject)

    def support(self, experiment_id: str, evidence_id: str | None = None, *, decisive: bool = False) -> None:
        if experiment_id not in self.supporting_experiments:
            self.supporting_experiments.append(experiment_id)
        if evidence_id and evidence_id not in self.evidence:
            self.evidence.append(evidence_id)
        self.confidence = min(0.99, self.confidence + (0.35 if decisive else 0.12))
        if decisive or (self.confidence >= 0.8 and len(self.supporting_experiments) >= 2):
            self.status = HypothesisStatus.VERIFIED
        elif self.status is HypothesisStatus.PROPOSED:
            self.status = HypothesisStatus.SUPPORTED
        self.updated_at = now_iso()

    def contradict(self, experiment_id: str, evidence_id: str | None = None, *, decisive: bool = False) -> None:
        if experiment_id not in self.contradicting_experiments:
            self.contradicting_experiments.append(experiment_id)
        if evidence_id and evidence_id not in self.evidence:
            self.evidence.append(evidence_id)
        self.confidence = max(0.02, self.confidence - (0.4 if decisive else 0.15))
        if decisive or self.confidence < 0.2:
            self.status = HypothesisStatus.REFUTED
        self.updated_at = now_iso()

    def mark_stale(self, reason: str) -> None:
        self.status = HypothesisStatus.STALE
        self.details["stale_reason"] = reason
        self.updated_at = now_iso()

    def describe(self) -> str:
        return f"[{self.status.value} {self.confidence:.2f}] {self.statement}"


class HypothesisEngine:
    """Proposes hypotheses from observations and updates them from experiments."""

    def __init__(self) -> None:
        self.hypotheses: dict[str, Hypothesis] = {}

    def all(self) -> list[Hypothesis]:
        return sorted(self.hypotheses.values(), key=lambda h: (-h.confidence, h.hypothesis_id))

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        return self.hypotheses.get(hypothesis_id)

    def add(self, hypothesis: Hypothesis) -> Hypothesis:
        existing = self.hypotheses.get(hypothesis.hypothesis_id)
        if existing is not None:
            return existing
        self.hypotheses[hypothesis.hypothesis_id] = hypothesis
        return hypothesis

    # -- proposal ----------------------------------------------------------
    def propose_requirements(self, observation, elements=None) -> list[Hypothesis]:
        """A disabled control plus empty fields suggests a requirement."""
        proposed: list[Hypothesis] = []
        elements = elements if elements is not None else observation.interactive_elements
        empty_fields = [
            e
            for e in elements
            if e.visible
            and e.editable
            and e.semantic_role.value in ("TEXT_FIELD", "SEARCH", "SELECT", "CHECKBOX", "PASSWORD_FIELD")
            and not (e.value_state.value or "").strip()
            and e.value_state.checked is not True
        ]
        disabled = [e for e in elements if e.visible and not e.enabled]
        for control in disabled:
            for field in empty_fields[:4]:
                hypothesis = Hypothesis(
                    statement=(
                        f"{control.label()} may require {field.label()} to be filled before it becomes available"
                    ),
                    kind=HypothesisKind.REQUIREMENT,
                    subject=control.element_id,
                    prediction={
                        "action": "fill",
                        "field_id": field.element_id,
                        "field_label": field.label(),
                        "enables": control.element_id,
                        "enables_label": control.label(),
                    },
                    details={"state": observation.url, "observation_id": observation.observation_id},
                )
                proposed.append(self.add(hypothesis))
        return proposed

    def propose_validation(self, observation, action_label: str = "") -> list[Hypothesis]:
        """A validation message is evidence about a rule; state it as a hypothesis."""
        proposed: list[Hypothesis] = []
        import re

        for alert in observation.alerts:
            text = str(alert.get("text", "")).strip()
            if not text:
                continue
            match = re.search(r"([A-Za-z ]{2,40}?)\s+(is required|must|is not valid|cannot)", text, re.IGNORECASE)
            field = (match.group(1).strip() if match else "").lower()
            hypothesis = Hypothesis(
                statement=f"{action_label or 'submitting'} requires {field or 'additional input'}: {text[:120]}",
                kind=HypothesisKind.VALIDATION,
                subject=action_label or observation.url,
                prediction={"message": text[:200], "field": field},
                details={"observation_id": observation.observation_id, "url": observation.url},
                confidence=0.45,
            )
            proposed.append(self.add(hypothesis))
        return proposed

    def propose_navigation(self, source_url: str, target_url: str, action_label: str) -> list[Hypothesis]:
        hypothesis = Hypothesis(
            statement=f"{action_label or 'control'} navigates from {source_url} to {target_url}",
            kind=HypothesisKind.NAVIGATION,
            subject=action_label,
            prediction={"from": source_url, "to": target_url},
            confidence=0.4,
        )
        return [self.add(hypothesis)]

    # -- update ------------------------------------------------------------
    def resolve_prediction(self, hypothesis: Hypothesis, observed: dict[str, Any]) -> bool | None:
        """Compare a hypothesis prediction with an outcome. None = inconclusive."""
        prediction = hypothesis.prediction or {}
        if "enables" in prediction:
            if observed.get("element_id") == prediction["enables"]:
                return bool(observed.get("enabled"))
            return None
        if "message" in prediction and observed.get("message"):
            return str(prediction["message"]).lower() == str(observed["message"]).lower()
        if "to" in prediction and observed.get("url"):
            return str(observed["url"]).startswith(str(prediction["to"]).split("?")[0])
        return None

    def mark_stale_for_url(self, url_pattern: str, reason: str) -> list[str]:
        affected: list[str] = []
        for hypothesis in self.hypotheses.values():
            target = str((hypothesis.details or {}).get("url", ""))
            if target and url_pattern in target and hypothesis.status is not HypothesisStatus.STALE:
                hypothesis.mark_stale(reason)
                affected.append(hypothesis.hypothesis_id)
        return affected

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for hypothesis in self.hypotheses.values():
            counts[hypothesis.status.value] = counts.get(hypothesis.status.value, 0) + 1
        return {
            "total": len(self.hypotheses),
            "by_status": counts,
            "verified": [h.hypothesis_id for h in self.all() if h.status is HypothesisStatus.VERIFIED][:20],
        }
