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

    def synthesize(
        self,
        element: Element,
        *,
        variant: int = 0,
        constraints: dict[str, Any] | None = None,
    ) -> str | None:
        if element.semantic_role is ElementRole.PASSWORD_FIELD:
            # Passwords are never typed by the agent during exploration.
            return None
        constraints = constraints or {}
        rejected = set(constraints.get("rejected_values", []))

        max_attempts = 5
        for attempt in range(max_attempts):
            curr_variant = variant + attempt
            val = self._synthesize_base(element, curr_variant, constraints)
            if val is not None and val not in rejected:
                return val
            if val is None:
                return None
        return val

    def _synthesize_base(self, element: Element, variant: int, constraints: dict[str, Any]) -> str | None:
        attrs = element.attributes
        input_type = (attrs.get("type") or "").lower()
        kind = self.labeler.parameter_kind(element)

        if "min" in constraints or "max" in constraints:
            min_val = constraints.get("min")
            max_val = constraints.get("max")
            low = int(min_val) if min_val is not None else 0
            high = int(max_val) if max_val is not None else (low + 100)
            if high >= low:
                span = max(1, high - low + 1)
                return str(low + (variant % span))
            return str(low)

        if input_type == "email" or kind is ParameterKind.EMAIL:
            local = "blackbox.sample" if variant == 0 else f"blackbox.sample{variant}"
            return f"{local}@example.com"

        shaped = self._shaped_value(element, variant)
        if shaped is not None:
            base_val = shaped
        elif attrs.get("pattern"):
            base_val = self.synthesize_from_pattern(attrs["pattern"], variant) or self._text_value(element, variant)
        elif kind is ParameterKind.DATE or input_type in ("date", "datetime-local", "month", "week"):
            base_val = self._date_value(attrs, variant)
        elif input_type == "time":
            base_val = "09:30"
        elif kind is ParameterKind.NUMBER or input_type in ("number", "range"):
            base_val = self._number_value(attrs, variant)
        else:
            base_val = self._text_value(element, variant)

        if base_val is None:
            return None

        res = base_val
        prefix = constraints.get("prefix")
        if prefix and not res.startswith(prefix):
            res = f"{prefix}_{res}"

        min_len = constraints.get("min_length")
        if min_len and len(res) < min_len:
            pad = f"_{variant}" if variant else "_sample"
            while len(res) < min_len:
                res += pad

        max_len = constraints.get("max_length")
        if max_len and len(res) > max_len:
            res = res[:max_len]

        return res


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
