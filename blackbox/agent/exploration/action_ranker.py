"""Action ranking: spend the budget where the model is thinnest.

The score is expected information gain, discounted by risk, repetition and
predicted cost.  Every ranked action carries the reasons it scored the way it
did, which is what lets the dashboard answer "why did BlackBox click that?".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model.action import Action, ActionType, RiskLevel
from ..model.state import WebsiteState
from ..perception.semantic_labeler import SemanticIntent, SemanticLabeler
from .novelty import NoveltyTracker
from .safety import RiskAssessment


@dataclass
class RankWeights:
    novelty: float = 1.15
    hypothesis_test: float = 1.30
    untested_control: float = 0.55
    new_capability: float = 0.45
    known_transition_bonus: float = 0.20
    risk_penalty: float = 0.50
    repeat_penalty: float = 1.35
    no_effect_penalty: float = 0.95
    disabled_penalty: float = 0.70
    low_confidence_penalty: float = 0.35
    active_form_bonus: float = 0.85
    action_cost: float = 0.06


@dataclass
class RankedAction:
    action: Action
    score: float
    risk: RiskLevel = RiskLevel.LOW
    reasons: list[str] = field(default_factory=list)
    blocked: bool = False

    def describe(self) -> str:
        return f"{self.score:+.2f} {self.action.describe()} [{self.risk.value}] " + "; ".join(self.reasons[:3])


class ActionRanker:
    """Scores candidate actions by expected information gain."""

    def __init__(self, weights: RankWeights | None = None) -> None:
        self.weights = weights or RankWeights()
        self.labeler = SemanticLabeler()
        self.last_ranking: list[RankedAction] = []

    def rank(
        self,
        candidates: list[Action],
        *,
        state: WebsiteState,
        novelty: NoveltyTracker,
        risks: dict[str, RiskAssessment] | None = None,
        hypothesis_action_ids: set[str] | None = None,
        known_transition_count: int = 0,
        element_lookup: dict[str, Any] | None = None,
        active_form_element_ids: set[str] | None = None,
    ) -> list[RankedAction]:
        risks = risks or {}
        hypothesis_action_ids = hypothesis_action_ids or set()
        element_lookup = element_lookup or {}
        active_form_element_ids = active_form_element_ids or set()
        ranked: list[RankedAction] = []

        for action in candidates:
            score = 0.0
            reasons: list[str] = []
            assessment = risks.get(action.action_id)
            risk = assessment.risk if assessment else RiskLevel.LOW

            if action.target and action.target.element_id in active_form_element_ids:
                score += self.weights.active_form_bonus
                reasons.append("part of active form/dialog")

            novelty_score = novelty.action_novelty(state.state_id, action)
            if novelty_score > 0:
                score += novelty_score * self.weights.novelty
                reasons.append(f"novel in this state ({novelty_score:.2f})")

            if action.action_id in hypothesis_action_ids or action.origin == "hypothesis":
                score += self.weights.hypothesis_test
                reasons.append("tests an open hypothesis")

            element = element_lookup.get(action.target.element_id) if action.target else None
            if element is not None:
                intent = self.labeler.label_element(element).intent
                if intent in (SemanticIntent.CREATE, SemanticIntent.SEARCH, SemanticIntent.EXPORT, SemanticIntent.UPLOAD):
                    score += self.weights.new_capability
                    reasons.append(f"capability-bearing control ({intent.value})")
                if not element.enabled:
                    score -= self.weights.disabled_penalty
                    reasons.append("control is disabled (precondition likely unmet)")

            if novelty.is_repeat(state.state_id, action):
                score -= self.weights.repeat_penalty
                reasons.append("already repeated in this state")
            elif novelty.always_no_effect(state.state_id, action):
                score -= self.weights.no_effect_penalty
                reasons.append("previously produced no observable effect")

            score -= self.weights.risk_penalty * risk.rank
            if risk.rank:
                reasons.append(f"risk {risk.value}")

            if action.confidence < 0.4:
                score -= self.weights.low_confidence_penalty
                reasons.append(f"low locator confidence ({action.confidence:.2f})")

            # Cost-aware exploration: price action by predicted latency and risk
            action_cost_factor = 0.05
            if action.type in (ActionType.NAVIGATE, ActionType.NAVIGATE_BACK):
                action_cost_factor = 0.20
            elif action.type is ActionType.UPLOAD:
                action_cost_factor = 0.25
            elif action.type in (ActionType.SCROLL, ActionType.WAIT_FOR_STATE):
                action_cost_factor = 0.15

            if action.type in (ActionType.SCROLL, ActionType.WAIT_FOR_STATE):
                score -= 0.35
                reasons.append("low-information browser action")

            total_cost_penalty = self.weights.action_cost * (known_transition_count + action_cost_factor * 5)
            score -= total_cost_penalty
            if action_cost_factor >= 0.20:
                reasons.append(f"cost penalty ({action.type.value} runtime)")

            ranked.append(RankedAction(action=action, score=round(score, 4), risk=risk, reasons=reasons or ["baseline"]))

        ranked.sort(key=lambda item: (-item.score, item.action.signature()))
        self.last_ranking = ranked
        return ranked

    def explain(self, ranked: RankedAction) -> dict[str, Any]:
        return {
            "action": ranked.action.describe(),
            "action_type": ranked.action.type.value,
            "score": ranked.score,
            "risk": ranked.risk.value,
            "reasons": ranked.reasons,
            "rationale": ranked.action.rationale,
            "target": ranked.action.target.label() if ranked.action.target else "page",
        }

    def summary(self) -> dict[str, Any]:
        return {
            "ranked": len(self.last_ranking),
            "top": [self.explain(item) for item in self.last_ranking[:5]],
        }
