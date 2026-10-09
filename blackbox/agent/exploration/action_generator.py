"""Action generation and value synthesis.

Candidates come from what is observable on the page: every visible, actionable
control yields the actions a user could take on it.  Values typed into fields are
synthesized deterministically - from the field's own semantics (email, date,
number), from its ``pattern``/``min``/``max`` attributes, and from a fixed demo
vocabulary - so runs are reproducible and nothing about the target site is
hard-coded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..model.action import Action, ActionType, EffectKind, RiskLevel, TargetSpec
from ..model.base import normalize_text
from ..model.element import Element, ElementRole
from ..model.workflow import ParameterKind
from ..observation.observation import Observation
from ..perception.semantic_labeler import SemanticIntent, SemanticLabeler

PATTERN_TOKEN = re.compile(r"(\[[^\]]+\]|\\d|\\w|\\s|\{[0-9]+(?:,[0-9]*)?\}|[A-Za-z0-9\-@._+ ])")

DEFAULT_TEXT = "BlackBox Sample"


@dataclass
class SynthesisContext:
    """Everything the synthesizer may look at when inventing a value."""

    today: date = field(default_factory=date.today)
    text_pool: dict[str, str] = field(
        default_factory=lambda: {
            "name": "BlackBox Sample",
            "full name": "BlackBox Sample",
            "first name": "Sample",
            "last name": "Tester",
            "company": "BlackBox Labs",
            "employer": "BlackBox Labs",
            "city": "Springfield",
            "address": "12 Example Street",
            "zip": "12345",
            "postal": "12345",
            "phone": "5551234567",
            "title": "BlackBox sample task",
            "project": "BlackBox sample project",
            "search": "sample",
            "query": "sample",
            "description": "Sample description created during authorized exploration.",
            "title/name": DEFAULT_TEXT,
        }
    )


class ValueSynthesizer:
    """Produces deterministic, plausible values for observed input fields."""

    def __init__(self, context: SynthesisContext | None = None) -> None:
        self.context = context or SynthesisContext()
        self.labeler = SemanticLabeler()

    def synthesize(self, element: Element, *, variant: int = 0) -> str | None:
        if element.semantic_role is ElementRole.PASSWORD_FIELD:
            # Passwords are never typed by the agent during exploration.
            return None
        attrs = element.attributes
        input_type = (attrs.get("type") or "").lower()
        kind = self.labeler.parameter_kind(element)

        if input_type == "email" or kind is ParameterKind.EMAIL:
            local = "blackbox.sample" if variant == 0 else f"blackbox.sample{variant}"
            return f"{local}@example.com"

        # Domain-shaped fields first: these have hard format requirements that a
        # generic number or word would violate.
        shaped = self._shaped_value(element, variant)
        if shaped is not None:
            return shaped

        # An explicit `pattern` is the most specific constraint a field can state.
        if attrs.get("pattern"):
            synthesized = self.synthesize_from_pattern(attrs["pattern"], variant)
            if synthesized:
                return synthesized

        if kind is ParameterKind.DATE or input_type in ("date", "datetime-local", "month", "week"):
            return self._date_value(attrs, variant)
        if input_type == "time":
            return "09:30"
        if kind is ParameterKind.NUMBER or input_type in ("number", "range"):
            return self._number_value(attrs, variant)
        return self._text_value(element, variant)

    def _shaped_value(self, element: Element, variant: int) -> str | None:
        """Values for fields whose format is implied by their meaning."""
        attrs = element.attributes
        probe = normalize_text(f"{element.label()} {attrs.get('name', '')} {attrs.get('placeholder', '')}")
        input_type = (attrs.get("type") or "").lower()

        if "phone" in probe or "mobile" in probe or input_type == "tel":
            return "5551234567" if variant == 0 else "5559876543"
        if any(word in probe for word in ("zip", "postal")) and "code" not in probe.replace("zip code", ""):
            return "12345" if variant == 0 else "94107"
        if "zip" in probe or "postal" in probe:
            return "12345" if variant == 0 else "94107"
        if any(word in probe for word in ("card number", "credit card", "card")):
            return "4111111111111111" if variant == 0 else "4242424242424242"
        if any(word in probe for word in ("cvc", "cvv", "security code")):
            return "123"
        if "expiry" in probe or "expires" in probe:
            return "12/30"
        if any(word in probe for word in ("reference code", "reference")):
            return "ABC-1234" if variant == 0 else "XYZ-5678"
        min_length = attrs.get("minlength")
        if min_length:
            try:
                needed = int(min_length)
            except ValueError:
                needed = 0
            if needed > len(self._text_value(element, variant)):
                return (self._text_value(element, variant) + " " + "BlackBox")[:needed]
        return None

    def _date_value(self, attrs: dict[str, str], variant: int) -> str:
        today = self.context.today
        minimum = attrs.get("min") or ""
        maximum = attrs.get("max") or ""
        candidate = today + timedelta(days=variant)
        try:
            if minimum and len(minimum) == 10:
                minimum_date = date.fromisoformat(minimum)
                if candidate < minimum_date:
                    candidate = minimum_date
            if maximum and len(maximum) == 10:
                maximum_date = date.fromisoformat(maximum)
                if candidate > maximum_date:
                    candidate = maximum_date
        except ValueError:
            pass
        return candidate.isoformat()

    def _number_value(self, attrs: dict[str, str], variant: int) -> str:
        value = 42 + variant
        try:
            if attrs.get("min") not in (None, ""):
                value = max(value, int(float(attrs["min"])))
            if attrs.get("max") not in (None, ""):
                value = min(value, int(float(attrs["max"])))
            if attrs.get("step") and float(attrs["step"]) >= 1:
                step = int(float(attrs["step"]))
                value = value - (value % step) if step else value
        except (TypeError, ValueError):
            pass
        return str(value)

    def _text_value(self, element: Element, variant: int) -> str:
        probe = normalize_text(f"{element.label()} {element.attributes.get('name', '')}")
        for key, value in self.context.text_pool.items():
            if key in probe:
                return value if variant == 0 else f"{value} {variant + 1}"
        maxlength = element.attributes.get("maxlength")
        candidate = DEFAULT_TEXT if variant == 0 else f"{DEFAULT_TEXT} {variant + 1}"
        if maxlength:
            try:
                candidate = candidate[: int(maxlength)]
            except ValueError:
                pass
        return candidate

    def synthesize_from_pattern(self, pattern: str, variant: int = 0) -> str | None:
        """Build a string that matches a simple HTML ``pattern`` attribute."""
        pattern = pattern.strip()
        if pattern.startswith("^"):
            pattern = pattern[1:]
        if pattern.endswith("$"):
            pattern = pattern[:-1]
        if "|" in pattern or "(" in pattern or "*" in pattern or "+" in pattern or "?" in pattern:
            return None  # too expressive to synthesize reliably
        out: list[str] = []
        index = 0
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        while index < len(pattern):
            char = pattern[index]
            if char == "\\" and index + 1 < len(pattern):
                nxt = pattern[index + 1]
                repeat = re.match(r"\{(\d+)(?:,(\d+))?\}", pattern[index + 2 :])
                if repeat:
                    count = int(repeat.group(1))
                    index = index + 2 + repeat.end()
                else:
                    count = 1
                    index += 2
                if nxt == "d":
                    out.append(self._digits(count, variant))
                elif nxt == "w":
                    out.append("A" * count)
                elif nxt == "s":
                    out.append(" " * count)
                else:
                    out.append(nxt * count)
                continue
            if char == "[":
                end = pattern.find("]", index)
                if end == -1:
                    return None
                char_class = pattern[index + 1 : end]
                count = 1
                repeat = re.match(r"\{(\d+)(?:,(\d+))?\}", pattern[end + 1 :])
                if repeat:
                    count = int(repeat.group(1))
                    index = end + 1 + repeat.end()
                else:
                    index = end + 1
                sample = self._sample_class(char_class, count, variant)
                if sample is None:
                    return None
                out.append(sample)
                continue
            if char == "{":
                return None
            out.append(char)
            index += 1
        candidate = "".join(out)
        try:
            if re.fullmatch(pattern, candidate):
                return candidate
        except re.error:
            return None
        return candidate

    @staticmethod
    def _digits(count: int, variant: int) -> str:
        """`count` digits, deterministic per variant."""
        return "".join(str((variant + 1 + offset) % 10) for offset in range(max(1, count)))

    def _sample_class(self, char_class: str, count: int, variant: int) -> str | None:
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        digits = "0123456789"
        negated = char_class.startswith("^")
        content = char_class[1:] if negated else char_class
        ranges: list[tuple[str, str]] = []
        literals: list[str] = []
        i = 0
        while i < len(content):
            if i + 2 < len(content) and content[i + 1] == "-":
                ranges.append((content[i], content[i + 2]))
                i += 3
                continue
            literals.append(content[i])
            i += 1
        if negated:
            return "A" * count if "A" not in content else "1" * count
        alphabet = ""
        for start, end in ranges:
            if start.isalpha() and end.isalpha():
                lo, hi = ord(start), ord(end)
                alphabet += "".join(chr(c) for c in range(min(lo, hi), max(lo, hi) + 1))
            elif start.isdigit() and end.isdigit():
                alphabet += digits
        alphabet += "".join(literals)
        if "A-Za-z" in content:
            alphabet += letters + letters.lower()
        if not alphabet:
            return None
        if alphabet.isdigit():
            return "".join(digits[(variant + i) % 10] for i in range(count))
        if any(c.isdigit() for c in alphabet) and not any(c.isalpha() for c in alphabet):
            return "".join(digits[(variant + i) % 10] for i in range(count))
        return "".join(letters[(variant + i) % 26] for i in range(count))


@dataclass
class GenerationConfig:
    max_actions_per_state: int = 48
    max_select_options: int = 4
    max_text_variants: int = 2
    include_scroll: bool = True
    include_back: bool = True


@dataclass
class ScoredCandidate:
    action: Action
    element_id: str = ""
    expected: list[EffectKind] = field(default_factory=list)


class ActionGenerator:
    """Proposes the actions a user could take on the observed page."""

    def __init__(self, config: GenerationConfig | None = None, synthesizer: ValueSynthesizer | None = None) -> None:
        self.config = config or GenerationConfig()
        self.synthesizer = synthesizer or ValueSynthesizer()
        self.labeler = SemanticLabeler()

    def generate(
        self,
        observation: Observation,
        *,
        elements: list[Element] | None = None,
        state_id: str = "",
    ) -> list[Action]:
        elements = elements if elements is not None else observation.interactive_elements
        actions: list[Action] = []
        seen: set[str] = set()

        for element in elements:
            if not element.visible:
                continue
            for action in self._for_element(element, observation):
                if action.signature() in seen:
                    continue
                seen.add(action.signature())
                actions.append(action)
                if len(actions) >= self.config.max_actions_per_state:
                    return actions

        if self.config.include_scroll and observation.counts.get("document_height", 0) and (
            observation.browser_state.document_height > observation.viewport.height * 1.5
        ):
            actions.append(self._page_action(ActionType.SCROLL, {"delta_y": 600}, [EffectKind.TEXT_CHANGED]))
        if self.config.include_back and observation.browser_state.can_go_back:
            actions.append(self._page_action(ActionType.NAVIGATE_BACK, {}, [EffectKind.NAVIGATION]))
        return actions

    # -- per element -------------------------------------------------------
    def _for_element(self, element: Element, observation: Observation) -> list[Action]:
        role = element.semantic_role
        label = element.label()
        intent = self.labeler.label_element(element).intent
        actions: list[Action] = []

        if role in (ElementRole.TEXT_FIELD, ElementRole.SEARCH, ElementRole.PASSWORD_FIELD):
            for variant in range(self.config.max_text_variants):
                value = self.synthesizer.synthesize(element, variant=variant)
                if value is None:
                    continue
                if variant == 0 and (element.value_state.value or "").strip():
                    continue  # already filled with a synthesized value
                actions.append(
                    self._make(
                        ActionType.TYPE,
                        element,
                        {"value": value},
                        [EffectKind.VALUE_CHANGED, EffectKind.FORM_CHANGED],
                        rationale=f"fill {label or role.value} with a synthesized value",
                    )
                )
            if (element.value_state.value or "").strip():
                actions.append(self._make(ActionType.CLEAR, element, {}, [EffectKind.VALUE_CHANGED]))
            return actions

        if role is ElementRole.SELECT:
            options = [option for option in element.attributes.get("__options", "").split(",") if option]
            for option in options[: self.config.max_select_options]:
                if normalize_text(option) == normalize_text(element.value_state.value or ""):
                    continue
                actions.append(
                    self._make(
                        ActionType.SELECT,
                        element,
                        {"option": option},
                        [EffectKind.VALUE_CHANGED, EffectKind.ELEMENTS_CHANGED],
                        rationale=f"select {option!r} in {label}",
                    )
                )
            return actions

        if role is ElementRole.CHECKBOX:
            desired = not bool(element.value_state.checked)
            actions.append(
                self._make(
                    ActionType.CHECK if desired else ActionType.UNCHECK,
                    element,
                    {},
                    [EffectKind.VALUE_CHANGED, EffectKind.ELEMENTS_CHANGED],
                    rationale=f"{'check' if desired else 'uncheck'} {label}",
                )
            )
            return actions

        if role is ElementRole.RADIO:
            if element.value_state.checked:
                return actions
            actions.append(
                self._make(ActionType.CHECK, element, {}, [EffectKind.VALUE_CHANGED], rationale=f"select radio {label}")
            )
            return actions

        if role is ElementRole.UPLOAD:
            actions.append(
                self._make(
                    ActionType.UPLOAD,
                    element,
                    {},
                    [EffectKind.VALUE_CHANGED, EffectKind.SUCCESS_MESSAGE],
                    rationale="upload a generated fixture file",
                )
            )
            return actions

        if role in (ElementRole.BUTTON, ElementRole.LINK, ElementRole.TAB, ElementRole.MENU_ITEM, ElementRole.PAGINATION):
            if not element.enabled:
                # A disabled control is still interesting: it tells the explorer a
                # precondition exists, which the hypothesis engine turns into a test.
                return actions
            expected = self._expected_for(element, intent, role)
            if intent in (SemanticIntent.DOWNLOAD, SemanticIntent.EXPORT):
                actions.append(
                    self._make(ActionType.DOWNLOAD, element, {}, expected, rationale=f"trigger download via {label}")
                )
            actions.append(self._make(ActionType.CLICK, element, {}, expected, rationale=f"click {label}"))
            if role is ElementRole.PAGINATION:
                actions[-1].parameters["pagination"] = True
            return actions

        if role is ElementRole.OPTION:
            if element.value_state.checked:
                return actions
            actions.append(self._make(ActionType.CLICK, element, {}, [EffectKind.VALUE_CHANGED]))
            return actions

        if element.tag in ("div", "span", "li") and element.attributes.get("onclick"):
            actions.append(self._make(ActionType.CLICK, element, {}, [EffectKind.ELEMENTS_CHANGED]))
        return actions

    def _expected_for(self, element: Element, intent: SemanticIntent, role: ElementRole) -> list[EffectKind]:
        if role is ElementRole.LINK:
            return [EffectKind.NAVIGATION, EffectKind.URL_CHANGE]
        if intent in (SemanticIntent.CREATE, SemanticIntent.UPDATE, SemanticIntent.SUBMIT):
            return [EffectKind.SUCCESS_MESSAGE, EffectKind.ELEMENTS_ADDED, EffectKind.DIALOG_CLOSED, EffectKind.VALIDATION_MESSAGE]
        if intent is SemanticIntent.DELETE:
            return [EffectKind.ELEMENTS_REMOVED, EffectKind.DIALOG_OPENED, EffectKind.SUCCESS_MESSAGE]
        if intent in (SemanticIntent.SEARCH, SemanticIntent.FILTER, SemanticIntent.SORT):
            return [EffectKind.ELEMENTS_CHANGED, EffectKind.TEXT_CHANGED]
        if intent is SemanticIntent.EXPORT or intent is SemanticIntent.DOWNLOAD:
            return [EffectKind.SUCCESS_MESSAGE, EffectKind.ELEMENTS_CHANGED]
        if intent in (SemanticIntent.OPEN, SemanticIntent.NAVIGATE, SemanticIntent.PAGINATE):
            return [EffectKind.DIALOG_OPENED, EffectKind.NAVIGATION, EffectKind.ELEMENTS_CHANGED]
        if intent is SemanticIntent.CANCEL or intent is SemanticIntent.CLOSE:
            return [EffectKind.DIALOG_CLOSED, EffectKind.ELEMENTS_REMOVED]
        return [
            EffectKind.DIALOG_OPENED,
            EffectKind.NAVIGATION,
            EffectKind.ELEMENTS_ADDED,
            EffectKind.SUCCESS_MESSAGE,
            EffectKind.VALIDATION_MESSAGE,
            EffectKind.TEXT_CHANGED,
        ]

    def _make(
        self,
        action_type: ActionType,
        element: Element,
        parameters: dict[str, Any],
        expected: list[EffectKind],
        *,
        rationale: str = "",
    ) -> Action:
        target = TargetSpec(
            element_id=element.element_id,
            role=element.semantic_role.value,
            name=element.label()[:160],
            tag=element.tag,
            locators=[
                {
                    "strategy": candidate.strategy.value,
                    "selector": candidate.selector,
                    "confidence": candidate.confidence,
                    "params": candidate.params,
                }
                for candidate in element.locator_candidates
            ],
        )
        action = Action(
            type=action_type,
            target=target,
            parameters=parameters,
            expected_effect=expected,
            confidence=element.confidence,
            origin="generator",
            rationale=rationale,
        )
        return action.finalize()

    def _page_action(self, action_type: ActionType, parameters: dict[str, Any], expected: list[EffectKind]) -> Action:
        action = Action(
            type=action_type,
            target=None,
            parameters=parameters,
            expected_effect=expected,
            confidence=0.4,
            origin="generator",
            rationale="page-level action",
        )
        return action.finalize()

    # -- hypothesis-driven candidates --------------------------------------
    def for_hypothesis(self, hypothesis, observation: Observation, elements: list[Element]) -> list[Action]:
        """Turn a requirement hypothesis into the experiment that would test it."""
        prediction = hypothesis.prediction or {}
        field_id = prediction.get("field_id")
        if not field_id:
            return []
        element = next((e for e in elements if e.element_id == field_id), None)
        if element is None:
            return []
        value = self.synthesizer.synthesize(element)
        if value is None:
            return []
        action = self._make(
            ActionType.TYPE,
            element,
            {"value": value},
            [EffectKind.VALUE_CHANGED],
            rationale=f"test hypothesis {hypothesis.hypothesis_id}: {hypothesis.statement[:80]}",
        )
        action.origin = "hypothesis"
        return [action]
