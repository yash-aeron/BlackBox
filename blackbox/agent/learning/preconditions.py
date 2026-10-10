"""Precondition learning: what must be true before an action works.

The strongest evidence comes from failure.  When a click on "Export report" is
answered with "Select a date range before exporting.", that message *is* the
precondition statement - BlackBox records it as a constraint, attributes it to
the action, and later verifies it by satisfying the condition and re-trying.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..model.action import Action, EffectKind, ObservedEffect
from ..model.base import normalize_text
from ..model.constraint import Constraint, ConstraintKind, ConstraintScope
from ..model.element import Element, ElementRole
from ..observation.observation import Observation
from .causal import Attribution

REQUIRED = re.compile(r"([A-Za-z][A-Za-z ]{1,40}?)\s+(?:is|are)\s+required", re.IGNORECASE)
MIN_LENGTH = re.compile(r"([A-Za-z][A-Za-z0-9 _-]{1,40}?)\s+must\s+(?:be\s+at\s+least|have\s+at\s+least|contain\s+at\s+least)\s+(\d+)\s+characters?", re.IGNORECASE)
MAX_LENGTH = re.compile(r"([A-Za-z][A-Za-z0-9 _-]{1,40}?)\s+must\s+(?:be\s+at\s+most|have\s+at\s+most|not\s+exceed|be\s+no\s+more\s+than)\s+(\d+)\s+characters?", re.IGNORECASE)
PREFIX = re.compile(r"([A-Za-z][A-Za-z0-9 _-]{1,40}?)\s+must\s+start\s+with\s+([A-Za-z0-9_-]+)", re.IGNORECASE)
RANGE = re.compile(r"([A-Za-z][A-Za-z0-9 _-]{1,40}?)\s+must\s+be\s+between\s+(\d+)\s+and\s+(\d+)", re.IGNORECASE)
CONTAINS = re.compile(r"([A-Za-z][A-Za-z0-9 _-]{1,40}?)\s+must\s+contain\s+([A-Za-z0-9@._-]+)", re.IGNORECASE)
MUST_BE = re.compile(r"([A-Za-z][A-Za-z ]{1,40}?)\s+must\s+([a-z ]{2,40})", re.IGNORECASE)
SELECT_FIRST = re.compile(r"select\s+(?:a|an|the)?\s*([A-Za-z][A-Za-z ]{1,40}?)\s+(?:before|first|to)", re.IGNORECASE)
BEFORE_VERB = re.compile(r"([a-z ]{3,40}?)\s+before\s+(?:you\s+)?([a-z ]{3,30})", re.IGNORECASE)
BLOCKED_BY = re.compile(r"blocked by ([A-Za-z][A-Za-z ]{1,40})", re.IGNORECASE)
MATCH_FORMAT = re.compile(r"must (?:use|be|contain|match)\s+(?:the\s+)?(?:format\s+)?([A-Za-z0-9@\-\\\[\]\{\}\^\+\*\|\(\)\?\.]{2,40})", re.IGNORECASE)


@dataclass
class LearnedCondition:
    expression: str
    condition: dict[str, Any]
    message: str
    confidence: float
    field_ids: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"{self.expression}  (from: {self.message[:80]})"


class PreconditionLearner:
    """Derives constraints from observed blocking messages and disabled controls."""

    def _field_candidates(self, observation: Observation, name: str) -> list[Element]:
        wanted = normalize_text(name)
        wanted_words = {w for w in wanted.split() if len(w) > 2}
        matches: list[Element] = []
        for element in observation.interactive_elements:
            label = normalize_text(element.label())
            if not label:
                continue
            if wanted in label or label in wanted:
                matches.append(element)
            elif wanted_words and wanted_words & set(label.split()):
                matches.append(element)
        return matches

    def from_blocking_message(
        self, action: Action, message: str, observation: Observation
    ) -> list[LearnedCondition]:
        conditions: list[LearnedCondition] = []
        text = message.strip()
        if not text:
            return conditions

        required = REQUIRED.search(text)
        if required:
            field_name = required.group(1).strip()
            fields = self._field_candidates(observation, field_name)
            conditions.append(
                LearnedCondition(
                    expression=f"{normalize_text(field_name).replace(' ', '_')} != empty",
                    condition={
                        "field": field_name,
                        "op": "!=",
                        "value": "",
                        "field_ids": [f.element_id for f in fields],
                    },
                    message=text,
                    confidence=0.6 if fields else 0.45,
                    field_ids=[f.element_id for f in fields],
                )
            )
            return conditions

        select_first = SELECT_FIRST.search(text)
        if select_first:
            field_name = select_first.group(1).strip()
            fields = self._field_candidates(observation, field_name) or [
                e for e in observation.interactive_elements if e.semantic_role.value in ("SELECT", "TEXT_FIELD")
            ]
            conditions.append(
                LearnedCondition(
                    expression=f"{normalize_text(field_name).replace(' ', '_')} != null",
                    condition={
                        "field": field_name,
                        "op": "!=",
                        "value": "",
                        "field_ids": [f.element_id for f in fields[:6]],
                    },
                    message=text,
                    confidence=0.6 if fields else 0.45,
                    field_ids=[f.element_id for f in fields[:6]],
                )
            )
            return conditions

        blocked = BLOCKED_BY.search(text)
        if blocked:
            conditions.append(
                LearnedCondition(
                    expression=f"dependency({normalize_text(blocked.group(1)).replace(' ', '_')}) == complete",
                    condition={"dependency": blocked.group(1), "op": "==", "value": "complete"},
                    message=text,
                    confidence=0.65,
                )
            )
            return conditions

        min_len = MIN_LENGTH.search(text)
        if min_len:
            field_name = min_len.group(1).strip()
            n = int(min_len.group(2))
            fields = self._field_candidates(observation, field_name)
            conditions.append(
                LearnedCondition(
                    expression=f"len({normalize_text(field_name).replace(' ', '_')}) >= {n}",
                    condition={
                        "field": field_name,
                        "op": ">=",
                        "min_length": n,
                        "field_ids": [f.element_id for f in fields],
                    },
                    message=text,
                    confidence=0.7 if fields else 0.55,
                    field_ids=[f.element_id for f in fields],
                )
            )
            return conditions

        max_len = MAX_LENGTH.search(text)
        if max_len:
            field_name = max_len.group(1).strip()
            n = int(max_len.group(2))
            fields = self._field_candidates(observation, field_name)
            conditions.append(
                LearnedCondition(
                    expression=f"len({normalize_text(field_name).replace(' ', '_')}) <= {n}",
                    condition={
                        "field": field_name,
                        "op": "<=",
                        "max_length": n,
                        "field_ids": [f.element_id for f in fields],
                    },
                    message=text,
                    confidence=0.7 if fields else 0.55,
                    field_ids=[f.element_id for f in fields],
                )
            )
            return conditions

        prefix_match = PREFIX.search(text)
        if prefix_match:
            field_name = prefix_match.group(1).strip()
            prefix_val = prefix_match.group(2).strip()
            fields = self._field_candidates(observation, field_name)
            conditions.append(
                LearnedCondition(
                    expression=f"{normalize_text(field_name).replace(' ', '_')}.startswith({prefix_val})",
                    condition={
                        "field": field_name,
                        "op": "startswith",
                        "prefix": prefix_val,
                        "field_ids": [f.element_id for f in fields],
                    },
                    message=text,
                    confidence=0.7 if fields else 0.55,
                    field_ids=[f.element_id for f in fields],
                )
            )
            return conditions

        range_match = RANGE.search(text)
        if range_match:
            field_name = range_match.group(1).strip()
            min_val = int(range_match.group(2))
            max_val = int(range_match.group(3))
            fields = self._field_candidates(observation, field_name)
            conditions.append(
                LearnedCondition(
                    expression=f"{min_val} <= {normalize_text(field_name).replace(' ', '_')} <= {max_val}",
                    condition={
                        "field": field_name,
                        "op": "between",
                        "min": min_val,
                        "max": max_val,
                        "field_ids": [f.element_id for f in fields],
                    },
                    message=text,
                    confidence=0.7 if fields else 0.55,
                    field_ids=[f.element_id for f in fields],
                )
            )
            return conditions

        contains_match = CONTAINS.search(text)
        if contains_match:
            field_name = contains_match.group(1).strip()
            substr = contains_match.group(2).strip()
            fields = self._field_candidates(observation, field_name)
            conditions.append(
                LearnedCondition(
                    expression=f"{substr} in {normalize_text(field_name).replace(' ', '_')}",
                    condition={
                        "field": field_name,
                        "op": "contains",
                        "contains": substr,
                        "field_ids": [f.element_id for f in fields],
                    },
                    message=text,
                    confidence=0.7 if fields else 0.55,
                    field_ids=[f.element_id for f in fields],
                )
            )
            return conditions

        must = MUST_BE.search(text) or MATCH_FORMAT.search(text)
        if must:
            detail = must.group(1).strip() if must.re is MUST_BE else must.group(0).strip()
            conditions.append(
                LearnedCondition(
                    expression=f"format({normalize_text(action.target.label() if action.target else 'input')}) matches {normalize_text(detail)[:40]}",
                    condition={"format": detail, "op": "matches", "value": detail},
                    message=text,
                    confidence=0.5,
                )
            )
            return conditions

        before = BEFORE_VERB.search(text)
        if before:
            conditions.append(
                LearnedCondition(
                    expression=f"{normalize_text(before.group(1)).replace(' ', '_')} completed first",
                    condition={"prerequisite_action": before.group(1), "op": "==", "value": "done"},
                    message=text,
                    confidence=0.45,
                )
            )
            return conditions

        # Unparsed but still real evidence: keep it as an opaque precondition.
        conditions.append(
            LearnedCondition(
                expression=f"unspecified_precondition({normalize_text(action.describe())[:60]})",
                condition={"raw_message": text[:200]},
                message=text,
                confidence=0.35,
            )
        )
        return conditions

    def from_disabled_control(self, control: Element, observation: Observation) -> list[LearnedCondition]:
        """A disabled control with empty neighbours suggests a requirement."""
        empty_names: list[str] = []
        empty_ids: list[str] = []
        for element in observation.interactive_elements:
            if element.editable or element.semantic_role.value in ("SELECT", "CHECKBOX"):
                value = (element.value_state.value or "").strip()
                if not value and element.value_state.checked is not True:
                    empty_names.append(element.label())
                    empty_ids.append(element.element_id)
        if not empty_names:
            return []
        condition = LearnedCondition(
            expression=f"enabled({normalize_text(control.label())}) requires any of {[n for n in empty_names[:4]]}",
            condition={"enables": control.element_id, "requires_any": empty_ids[:6]},
            message="; ".join(f"{name} is empty" for name in empty_names[:4]),
            confidence=0.3,
            field_ids=empty_ids[:6],
        )
        return [condition]

    def to_constraint(self, action: Action, learned: LearnedCondition, state_id: str | None = None) -> Constraint:
        condition = dict(learned.condition)
        if state_id:
            condition["state_id"] = state_id
        constraint = Constraint(
            kind=ConstraintKind.PRECONDITION,
            scope=ConstraintScope.ACTION,
            subject=normalize_text(action.describe())[:120],
            expression=learned.expression,
            condition=condition,
            message=learned.message[:300],
            confidence=learned.confidence,
        )
        return constraint.finalize()

    def satisfies(self, constraint: Constraint, observation: Observation) -> tuple[bool, str]:
        """Check a learned precondition against the current observation."""
        condition = constraint.condition or {}
        field_ids = condition.get("field_ids") or []
        if field_ids:
            for element in observation.interactive_elements:
                if element.element_id in field_ids or element.snapshot_ref in field_ids:
                    if (element.value_state.value or "").strip() or element.value_state.checked:
                        return True, f"{element.label()} is filled"
            return False, f"{constraint.expression} not satisfied (field(s) empty)"
        if condition.get("requires_any"):
            for element in observation.interactive_elements:
                if element.element_id in condition["requires_any"] and (
                    (element.value_state.value or "").strip() or element.value_state.checked
                ):
                    return True, f"{element.label()} is filled"
            return False, "no enabling field is filled"
        if condition.get("dependency"):
            wanted = normalize_text(str(condition["dependency"]))
            for table in observation.tables:
                for row in table.rows:
                    joined = normalize_text(" ".join(row))
                    if wanted and wanted in joined and "completed" in joined:
                        return True, f"dependency {condition['dependency']} is complete"
            return False, f"dependency {condition['dependency']} is not complete"
        if condition.get("format"):
            return True, "format constraints cannot be checked without a value"
        if condition.get("raw_message"):
            return False, "unparsed precondition cannot be verified automatically"
        return True, "no verifiable condition"

    def from_effects(
        self, action: Action, effects: list[ObservedEffect], observation: Observation, state_id: str | None = None
    ) -> list[Constraint]:
        constraints: list[Constraint] = []
        for effect in effects:
            if effect.kind in (EffectKind.VALIDATION_MESSAGE, EffectKind.PRECONDITION_BLOCKED) and effect.message:
                for learned in self.from_blocking_message(action, effect.message, observation):
                    constraints.append(self.to_constraint(action, learned, state_id))
        return constraints
