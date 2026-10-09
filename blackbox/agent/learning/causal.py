"""Causal attribution and effect extraction.

Given "action X happened and then the page looked like Y", these modules answer
the two questions the model needs:

* which observable effects did the action actually cause (``effects.py``);
* which observed conditions were necessary for it to work (``preconditions.py``).

``causal.py`` sits between them: it attributes a state difference to an action's
target and records the conditional context, so "click worked here but not there"
becomes a fact about preconditions rather than noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model.action import Action, EffectKind, ObservedEffect
from ..model.base import normalize_text
from ..observation.observation import Observation
from ..perception.state_fingerprint import StateDiff


@dataclass
class Attribution:
    action_id: str
    effects: list[ObservedEffect] = field(default_factory=list)
    no_effect: bool = False
    conditional: bool = False
    notes: list[str] = field(default_factory=list)

    def kinds(self) -> list[EffectKind]:
        return [effect.kind for effect in self.effects]


class CausalEngine:
    """Attributes observed changes to the action that caused them."""

    def __init__(self) -> None:
        # action signature -> outcomes seen in different states
        self.outcomes: dict[str, list[dict[str, Any]]] = {}

    def attribute(self, action: Action, diff: StateDiff, observation: Observation, *, source_state_id: str = "") -> Attribution:
        effects: list[ObservedEffect] = []
        signature = action.signature()

        if diff.navigation_changed:
            effects.append(
                ObservedEffect(
                    kind=EffectKind.NAVIGATION,
                    detail=f"{diff.url_before} -> {diff.url_after}",
                    before=diff.url_before,
                    after=diff.url_after,
                )
            )
        for change in diff.dialogs_changed:
            if change.startswith("opened"):
                effects.append(ObservedEffect(kind=EffectKind.DIALOG_OPENED, detail=change[7:], after=change[7:]))
            elif change.startswith("closed"):
                effects.append(ObservedEffect(kind=EffectKind.DIALOG_CLOSED, detail=change[7:], before=change[7:]))
        for label in diff.elements_added[:12]:
            effects.append(ObservedEffect(kind=EffectKind.ELEMENTS_ADDED, detail=label, after=label))
        for label in diff.elements_removed[:12]:
            effects.append(ObservedEffect(kind=EffectKind.ELEMENTS_REMOVED, detail=label, before=label))
        for label in diff.elements_changed[:12]:
            effects.append(
                ObservedEffect(
                    kind=EffectKind.VALUE_CHANGED if action.type.value in ("TYPE", "SELECT", "CHECK", "UNCHECK", "CLEAR") else EffectKind.ELEMENTS_CHANGED,
                    detail=label,
                    element_id=action.target.element_id if action.target else None,
                )
            )
        for change in diff.form_changes[:8]:
            effects.append(ObservedEffect(kind=EffectKind.FORM_CHANGED, detail=change))
        for text in diff.alerts_added:
            lowered = normalize_text(text)
            if any(word in lowered for word in ("required", "invalid", "must", "cannot", "please", "error", "select a")):
                effects.append(ObservedEffect(kind=EffectKind.VALIDATION_MESSAGE, message=text, detail=text[:160]))
                effects.append(ObservedEffect(kind=EffectKind.PRECONDITION_BLOCKED, message=text, detail=text[:160]))
            elif any(word in lowered for word in ("created", "saved", "updated", "deleted", "added", "submitted", "exported", "placed", "applied")):
                effects.append(ObservedEffect(kind=EffectKind.SUCCESS_MESSAGE, message=text, detail=text[:160]))
                effects.append(ObservedEffect(kind=EffectKind.DATA_CHANGED, detail=text[:160]))
            else:
                effects.append(ObservedEffect(kind=EffectKind.TEXT_CHANGED, detail=text[:160]))
        if diff.text_changes and not any(e.kind is EffectKind.TEXT_CHANGED for e in effects):
            effects.append(ObservedEffect(kind=EffectKind.TEXT_CHANGED, detail=diff.text_changes[0][:160]))
        if diff.loading_changed:
            effects.append(
                ObservedEffect(
                    kind=EffectKind.LOADING_STARTED if observation.loading_state.is_loading else EffectKind.LOADING_FINISHED,
                    detail="; ".join(observation.loading_state.indicators[:3]),
                )
            )
        if not effects:
            effects.append(ObservedEffect(kind=EffectKind.NO_EFFECT, detail="no observable change"))

        attribution = Attribution(action_id=action.action_id, effects=effects, no_effect=not diff.changed)

        outcomes = self.outcomes.setdefault(signature, [])
        outcomes.append({"state": source_state_id, "changed": diff.changed, "effect_count": len(effects)})
        distinct_states = {entry["state"] for entry in outcomes if entry["state"]}
        changed_states = {entry["state"] for entry in outcomes if entry["state"] and entry["changed"]}
        if len(distinct_states) >= 2 and len(changed_states) not in (0, len(distinct_states)):
            attribution.conditional = True
            attribution.notes.append(
                f"same action produced different outcomes in {len(distinct_states)} states: conditional behavior"
            )
        self.outcomes[signature] = outcomes[-20:]
        return attribution

    def conditional_actions(self) -> list[str]:
        return [signature for signature, entries in self.outcomes.items() if len({e["state"] for e in entries}) > 1 and len({e["changed"] for e in entries}) > 1]

    def summary(self) -> dict[str, Any]:
        return {
            "actions_observed": len(self.outcomes),
            "conditional_actions": self.conditional_actions()[:20],
        }
