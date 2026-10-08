"""Novelty tracking: which behavior have we already seen?

The explorer should spend its budget where the model is thin.  This module keeps
the visited-state and tried-action ledgers that ranking uses to avoid re-running
the same no-op experiment, and to notice when the agent is stuck in a loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.action import Action
from ..model.base import content_hash
from ..model.state import StateFingerprint


def fingerprint_key(fingerprint: StateFingerprint) -> str:
    return content_hash(fingerprint.canonical())[:16]


@dataclass
class ActionRecord:
    count: int = 0
    effects: int = 0
    no_effect_count: int = 0
    target_states: set[str] = field(default_factory=set)

    @property
    def was_productive(self) -> bool:
        return self.effects > 0

    @property
    def always_no_effect(self) -> bool:
        return self.count > 0 and self.effects == 0


@dataclass
class NoveltyTracker:
    """Ledger of what has been seen and tried."""

    state_visits: dict[str, int] = field(default_factory=dict)
    tried: dict[tuple[str, str], ActionRecord] = field(default_factory=dict)
    state_action_counts: dict[str, int] = field(default_factory=dict)
    transitions_seen: set[str] = field(default_factory=set)
    max_same_state_visits: int = 12
    max_repeated_action: int = 2

    # -- recording ---------------------------------------------------------
    def note_state(self, fingerprint: StateFingerprint) -> int:
        key = fingerprint_key(fingerprint)
        self.state_visits[key] = self.state_visits.get(key, 0) + 1
        return self.state_visits[key]

    def note_action(self, state_id: str, action: Action, *, target_state: str | None = None, effects: int = 0) -> None:
        key = (state_id, action.signature())
        record = self.tried.setdefault(key, ActionRecord())
        record.count += 1
        if effects > 0:
            record.effects += 1
        else:
            record.no_effect_count += 1
        if target_state:
            record.target_states.add(target_state)
        self.state_action_counts[state_id] = self.state_action_counts.get(state_id, 0) + 1

    def note_transition(self, source_state: str, action: Action, target_state: str) -> None:
        self.transitions_seen.add(content_hash(source_state, action.signature(), target_state)[:16])

    # -- queries -----------------------------------------------------------
    def action_novelty(self, state_id: str, action: Action) -> float:
        record = self.tried.get((state_id, action.signature()))
        if record is None:
            return 1.0
        remaining = max(0, self.max_repeated_action - record.count)
        return round(max(0.0, 0.35 * remaining / max(1, self.max_repeated_action)), 3)

    def is_repeat(self, state_id: str, action: Action) -> bool:
        record = self.tried.get((state_id, action.signature()))
        return bool(record and record.count >= self.max_repeated_action)

    def was_productive(self, state_id: str, action: Action) -> bool:
        record = self.tried.get((state_id, action.signature()))
        return bool(record and record.was_productive)

    def always_no_effect(self, state_id: str, action: Action) -> bool:
        record = self.tried.get((state_id, action.signature()))
        return bool(record and record.always_no_effect)

    def state_exhausted(self, state_id: str) -> bool:
        return self.state_action_counts.get(state_id, 0) >= self.max_same_state_visits

    def unvisited_ratio(self) -> float:
        if not self.state_visits:
            return 1.0
        fresh = sum(1 for count in self.state_visits.values() if count == 1)
        return round(fresh / len(self.state_visits), 3)

    def stuck(self, recent_actions: list[tuple[str, str]]) -> bool:
        """True when the last several actions all revisited known ground.

        The window is deliberately generous: a form-filling sequence legitimately
        revisits the same state several times, and cutting exploration short there
        would abandon the workflow it is in the middle of learning.
        """
        if len(recent_actions) < 10:
            return False
        last = recent_actions[-10:]
        unique = {f"{state}:{signature}" for state, signature in last}
        return len(unique) <= 2

    def summary(self) -> dict[str, object]:
        productive = sum(1 for record in self.tried.values() if record.was_productive)
        return {
            "distinct_states": len(self.state_visits),
            "total_state_visits": sum(self.state_visits.values()),
            "distinct_actions_tried": len(self.tried),
            "productive_actions": productive,
            "transitions_seen": len(self.transitions_seen),
            "unvisited_ratio": self.unvisited_ratio(),
        }
