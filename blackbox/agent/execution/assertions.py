"""Verification: did the action do what the model said it would?

Two kinds of check live here:

* effect verification - comparing an action's expected effects against the
  observed state difference, which is what promotes a transition to VERIFIED;
* end-state predicates - the machine-checkable success conditions a task is
  scored against (a row exists, a confirmation is visible, a URL is reached).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from ..model.action import Action, EffectKind
from ..model.base import normalize_text
from ..model.state import WebsiteState
from ..observation.observation import Observation
from ..perception.state_fingerprint import StateDiff

VALIDATION_PATTERNS = re.compile(
    r"\b(required|must|cannot|can't|invalid|please|enter a|select a|is not allowed|too short|too long|missing|error)\b",
    re.IGNORECASE,
)
SUCCESS_PATTERNS = re.compile(
    r"\b(created|saved|updated|deleted|added|removed|submitted|exported|applied|placed|success|complete|sent|confirmed)\b",
    re.IGNORECASE,
)
PRECONDITION_PATTERNS = re.compile(
    r"\b(before|first|requires|needs|blocked|disabled|not available|must be|save the)\b",
    re.IGNORECASE,
)


class PredicateKind(str, Enum):
    TEXT_PRESENT = "TEXT_PRESENT"
    TEXT_ABSENT = "TEXT_ABSENT"
    ELEMENT_PRESENT = "ELEMENT_PRESENT"
    ELEMENT_ABSENT = "ELEMENT_ABSENT"
    URL_CONTAINS = "URL_CONTAINS"
    ALERT_CONTAINS = "ALERT_CONTAINS"
    DIALOG_OPEN = "DIALOG_OPEN"
    TABLE_ROW_CONTAINS = "TABLE_ROW_CONTAINS"
    VALUE_EQUALS = "VALUE_EQUALS"
    STATE_MATCHES = "STATE_MATCHES"


@dataclass
class Predicate:
    kind: PredicateKind
    value: str = ""
    target: str = ""
    description: str = ""

    def describe(self) -> str:
        return self.description or f"{self.kind.value}({self.value or self.target})"


@dataclass
class PredicateResult:
    predicate: Predicate
    satisfied: bool
    evidence: str = ""


@dataclass
class VerificationResult:
    verified: bool
    matched: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)
    no_effect: bool = False

    def describe(self) -> str:
        parts = []
        if self.matched:
            parts.append("matched: " + ", ".join(self.matched))
        if self.missed:
            parts.append("missed: " + ", ".join(self.missed))
        if self.no_effect:
            parts.append("no observable effect")
        return "; ".join(parts) or "nothing to verify"


def _alerts_matching(observation: Observation, pattern: re.Pattern[str]) -> list[str]:
    return [str(alert.get("text", "")) for alert in observation.alerts if pattern.search(str(alert.get("text", "")))]


def verify_effects(action: Action, expected: list[EffectKind], diff: StateDiff, observation: Observation) -> VerificationResult:
    """Compare expected effects against what actually changed."""
    result = VerificationResult(verified=False)
    result.no_effect = diff.is_no_effect()

    checks: dict[EffectKind, bool] = {
        EffectKind.NAVIGATION: diff.navigation_changed,
        EffectKind.URL_CHANGE: diff.navigation_changed,
        EffectKind.DIALOG_OPENED: any(change.startswith("opened") for change in diff.dialogs_changed),
        EffectKind.DIALOG_CLOSED: any(change.startswith("closed") for change in diff.dialogs_changed),
        EffectKind.ELEMENTS_ADDED: bool(diff.elements_added),
        EffectKind.ELEMENTS_REMOVED: bool(diff.elements_removed),
        EffectKind.ELEMENTS_CHANGED: bool(diff.elements_changed),
        EffectKind.TEXT_CHANGED: bool(diff.text_changes),
        EffectKind.FORM_CHANGED: bool(diff.form_changes),
        EffectKind.VALUE_CHANGED: bool(diff.elements_changed) or bool(diff.form_changes),
        EffectKind.VALIDATION_MESSAGE: bool(_alerts_matching(observation, VALIDATION_PATTERNS) or diff.alerts_added),
        EffectKind.SUCCESS_MESSAGE: bool(_alerts_matching(observation, SUCCESS_PATTERNS)),
        EffectKind.LOADING_STARTED: diff.loading_changed and observation.loading_state.is_loading,
        EffectKind.LOADING_FINISHED: diff.loading_changed and not observation.loading_state.is_loading,
        EffectKind.DATA_CHANGED: bool(diff.elements_added or diff.elements_removed or diff.text_changes),
        EffectKind.PRECONDITION_BLOCKED: bool(_alerts_matching(observation, PRECONDITION_PATTERNS)),
        EffectKind.ERROR: bool(observation.browser_state.console_errors),
        EffectKind.NO_EFFECT: diff.is_no_effect(),
    }

    if not expected:
        result.verified = bool(diff.changed)
        result.confidence = 0.5 if diff.changed else 0.3
        result.notes.append("no declared expectation; verified against any observable change")
        return result

    for effect in expected:
        if checks.get(effect, False):
            result.matched.append(effect.value)
        else:
            result.missed.append(effect.value)

    # A generator that lists several plausible outcomes is stating alternatives
    # ("this click opens a dialog, navigates, or shows a message"), so any match
    # confirms it.  A specific one- or two-effect prediction must match exactly.
    if len(expected) >= 3:
        result.verified = bool(result.matched)
        result.confidence = round(len(result.matched) / len(expected), 3)
        result.notes.append("disjunctive expectation: any listed effect confirms it")
    else:
        result.verified = not result.missed
        result.confidence = round(len(result.matched) / max(1, len(expected)), 3)
    if not result.matched and diff.changed:
        result.notes.append("action had an effect, but not the predicted one")
    return result


class AssertionEngine:
    """Evaluates task success conditions against an observation."""

    def evaluate(self, predicate: Predicate, observation: Observation, state: WebsiteState | None = None) -> PredicateResult:
        value = normalize_text(predicate.value)
        text = normalize_text(observation.visible_text)
        labels = [normalize_text(e.label()) for e in observation.interactive_elements]

        if predicate.kind is PredicateKind.TEXT_PRESENT:
            hit = value in text or any(value in label for label in labels)
            return PredicateResult(predicate, hit, f"searched {len(text)} chars of visible text")
        if predicate.kind is PredicateKind.TEXT_ABSENT:
            hit = value not in text
            return PredicateResult(predicate, hit, "text absent as required")
        if predicate.kind is PredicateKind.ELEMENT_PRESENT:
            target = normalize_text(predicate.target or predicate.value)
            hit = any(target in label for label in labels)
            return PredicateResult(predicate, hit, f"{len(labels)} interactive elements inspected")
        if predicate.kind is PredicateKind.ELEMENT_ABSENT:
            target = normalize_text(predicate.target or predicate.value)
            hit = not any(target in label for label in labels)
            return PredicateResult(predicate, hit, "element absent as required")
        if predicate.kind is PredicateKind.URL_CONTAINS:
            hit = predicate.value.lower() in observation.url.lower()
            return PredicateResult(predicate, hit, observation.url)
        if predicate.kind is PredicateKind.ALERT_CONTAINS:
            hit = any(value in normalize_text(str(a.get("text", ""))) for a in observation.alerts)
            return PredicateResult(predicate, hit, "; ".join(str(a.get("text", ""))[:80] for a in observation.alerts))
        if predicate.kind is PredicateKind.DIALOG_OPEN:
            hit = bool(observation.dialogs)
            return PredicateResult(predicate, hit, "; ".join(d.title for d in observation.dialogs))
        if predicate.kind is PredicateKind.TABLE_ROW_CONTAINS:
            for table in observation.tables:
                for row in table.rows:
                    if any(value in normalize_text(cell) for cell in row):
                        return PredicateResult(predicate, True, "row: " + " | ".join(row[:6]))
            return PredicateResult(predicate, False, f"{len(observation.tables)} table(s) inspected")
        if predicate.kind is PredicateKind.VALUE_EQUALS:
            target = normalize_text(predicate.target)
            for element in observation.interactive_elements:
                if target and target in normalize_text(element.label()):
                    actual = normalize_text(element.value_state.value or "")
                    return PredicateResult(predicate, actual == value, f"{element.label()}={actual!r}")
            return PredicateResult(predicate, False, "target field not found")
        if predicate.kind is PredicateKind.STATE_MATCHES:
            hit = state is not None and value in normalize_text(
                f"{state.title} {state.semantic_summary} {state.visible_text[:2000]}"
            )
            return PredicateResult(predicate, hit, state.state_id if state else "no state")
        return PredicateResult(predicate, False, f"unsupported predicate {predicate.kind.value}")

    def evaluate_all(
        self, predicates: list[Predicate], observation: Observation, state: WebsiteState | None = None
    ) -> tuple[bool, list[PredicateResult]]:
        results = [self.evaluate(predicate, observation, state) for predicate in predicates]
        return all(r.satisfied for r in results), results


def predicates_from_spec(specs: list[dict[str, str]]) -> list[Predicate]:
    """Build predicates from a benchmark task definition."""
    predicates: list[Predicate] = []
    for spec in specs:
        kind_raw = str(spec.get("kind", "")).upper()
        try:
            kind = PredicateKind(kind_raw)
        except ValueError:
            continue
        predicates.append(
            Predicate(
                kind=kind,
                value=str(spec.get("value", "")),
                target=str(spec.get("target", "")),
                description=str(spec.get("description", "")),
            )
        )
    return predicates
