"""Effect and postcondition learning.

Effects are the observable consequences of an action: a navigation, a dialog, a
row appearing, a success banner, a validation message.  Postconditions are the
durable, checkable form of those effects, and they are what a plan verifies
against after every step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model.action import Action, EffectKind, ObservedEffect
from ..model.base import normalize_text
from ..model.transition import Transition
from ..observation.observation import Observation
from ..perception.state_fingerprint import StateDiff
from .causal import Attribution


def postcondition_for(effect: ObservedEffect) -> str | None:
    """A stable, checkable string describing what must hold after the action."""
    if effect.kind is EffectKind.NAVIGATION:
        return f"url~{effect.after}"
    if effect.kind is EffectKind.DIALOG_OPENED:
        return f"dialog_open:{effect.detail[:60]}"
    if effect.kind is EffectKind.DIALOG_CLOSED:
        return f"dialog_closed:{effect.detail[:60]}"
    if effect.kind is EffectKind.SUCCESS_MESSAGE:
        return f"message:{effect.message or effect.detail}"
    if effect.kind is EffectKind.VALIDATION_MESSAGE:
        return f"validation:{effect.message or effect.detail}"
    if effect.kind is EffectKind.ELEMENTS_ADDED:
        return f"element_added:{effect.detail[:60]}"
    if effect.kind is EffectKind.ELEMENTS_REMOVED:
        return f"element_removed:{effect.detail[:60]}"
    if effect.kind is EffectKind.VALUE_CHANGED:
        return f"value_changed:{effect.detail[:60]}"
    if effect.kind is EffectKind.DATA_CHANGED:
        return f"data_changed:{effect.detail[:60]}"
    return None


def effect_signature(effect: ObservedEffect) -> str:
    return f"{effect.kind.value}:{(effect.message or effect.detail or '')[:120]}"


@dataclass
class EffectExtractor:
    """Turns observations into structured effects and postconditions."""

    def extract(self, action: Action, diff: StateDiff, observation: Observation, attribution: Attribution | None = None) -> list[ObservedEffect]:
        if attribution is not None:
            return attribution.effects

        effects: list[ObservedEffect] = []
        if diff.navigation_changed:
            effects.append(ObservedEffect(kind=EffectKind.NAVIGATION, before=diff.url_before, after=diff.url_after))
        if diff.is_no_effect():
            effects.append(ObservedEffect(kind=EffectKind.NO_EFFECT, detail="state unchanged"))
        for text in diff.alerts_added:
            effects.append(ObservedEffect(kind=EffectKind.SUCCESS_MESSAGE, message=text))
        return effects

    def postconditions(self, effects: list[ObservedEffect]) -> list[str]:
        conditions = [postcondition_for(effect) for effect in effects]
        return [condition for condition in conditions if condition]

    def apply(self, transition: Transition, effects: list[ObservedEffect]) -> Transition:
        seen = {effect_signature(e) for e in transition.observed_effects}
        for effect in effects:
            if effect_signature(effect) not in seen:
                transition.observed_effects.append(effect)
                seen.add(effect_signature(effect))
        for condition in self.postconditions(effects):
            if condition not in transition.postconditions:
                transition.postconditions.append(condition)
        return transition

    def success_message(self, effects: list[ObservedEffect]) -> str | None:
        for effect in effects:
            if effect.kind is EffectKind.SUCCESS_MESSAGE and effect.message:
                return effect.message
        return None

    def blocking_message(self, effects: list[ObservedEffect]) -> str | None:
        for effect in effects:
            if effect.kind in (EffectKind.VALIDATION_MESSAGE, EffectKind.PRECONDITION_BLOCKED) and effect.message:
                return effect.message
        return None

    def summary(self, effects: list[ObservedEffect]) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for effect in effects:
            by_kind[effect.kind.value] = by_kind.get(effect.kind.value, 0) + 1
        return {
            "count": len(effects),
            "by_kind": by_kind,
            "success_message": self.success_message(effects),
            "blocking_message": self.blocking_message(effects),
            "no_effect": any(e.kind is EffectKind.NO_EFFECT for e in effects),
        }


def describe_effects(effects: list[ObservedEffect]) -> str:
    parts = []
    for effect in effects[:6]:
        detail = effect.message or effect.detail or effect.after or ""
        parts.append(f"{effect.kind.value}{': ' + normalize_text(detail)[:70] if detail else ''}")
    return "; ".join(parts)
