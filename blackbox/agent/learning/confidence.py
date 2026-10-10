"""Confidence: how much a learned claim deserves to be trusted.

Confidence is a function of independent evidence, not of how plausible a claim
sounds.  One success is enough to act on but not enough to rely on; repeated
success raises confidence, failure lowers it, and confidence decays when a model
has not been re-verified for a while, because websites change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from ..model.base import utcnow


@dataclass
class ConfidenceModel:
    base: float = 0.35
    success_gain: float = 0.18
    failure_penalty: float = 0.25
    verified_bonus: float = 0.15
    minimum: float = 0.02
    maximum: float = 0.99
    half_life_days: float = 21.0
    actionable_threshold: float = 0.5
    trust_threshold: float = 0.75

    def from_counts(self, success: int, failure: int, *, verified: bool = False, age_days: float = 0.0) -> float:
        score = self.base + self.success_gain * min(success, 4) - self.failure_penalty * min(failure, 4)
        if verified:
            score += self.verified_bonus
        if success == 0 and failure > 0:
            score = min(score, 0.25)
        return self._clamp(self.decay(score, age_days))

    def decay(self, confidence: float, age_days: float) -> float:
        if age_days <= 0 or self.half_life_days <= 0:
            return confidence
        factor = math.pow(0.5, age_days / self.half_life_days)
        # Decay pulls confidence toward uncertainty, not toward zero.
        return 0.5 + (confidence - 0.5) * factor

    def from_evidence(self, evidence_count: int, *, contradictions: int = 0, kind_weight: float = 0.15) -> float:
        score = self.base + kind_weight * min(evidence_count, 5) - self.failure_penalty * contradictions
        return self._clamp(score)

    def _clamp(self, value: float) -> float:
        return round(max(self.minimum, min(self.maximum, value)), 4)

    # -- policy ------------------------------------------------------------
    def is_actionable(self, confidence: float) -> bool:
        return confidence >= self.actionable_threshold

    def is_trusted(self, confidence: float) -> bool:
        return confidence >= self.trust_threshold

    def requires_experiment(self, confidence: float) -> bool:
        return confidence < self.actionable_threshold

    def explain(self, success: int, failure: int, *, verified: bool = False, age_days: float = 0.0) -> dict[str, object]:
        return {
            "success": success,
            "failure": failure,
            "verified": verified,
            "age_days": round(age_days, 2),
            "confidence": self.from_counts(success, failure, verified=verified, age_days=age_days),
            "actionable": self.is_actionable(self.from_counts(success, failure, verified=verified, age_days=age_days)),
        }


def age_days(timestamp: str | None) -> float:
    if not timestamp:
        return 0.0
    try:
        then = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, (utcnow() - then).total_seconds() / 86400.0)
