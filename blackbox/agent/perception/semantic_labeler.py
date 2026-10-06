"""Semantic labeling: what does this control mean to a user?

Deterministic rules first (verbs, nouns, field-name heuristics), because they are
reproducible and free.  An LLM may refine ambiguous cases, but only through the
provider abstraction and only into the same structured fields - the labeler
never hands control to the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..model.element import Element, ElementRole
from ..model.workflow import ParameterKind
from ..observation.observation import Observation


class SemanticIntent(str, Enum):
    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    SEARCH = "SEARCH"
    FILTER = "FILTER"
    SORT = "SORT"
    NAVIGATE = "NAVIGATE"
    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"
    CONFIRM = "CONFIRM"
    EXPORT = "EXPORT"
    DOWNLOAD = "DOWNLOAD"
    UPLOAD = "UPLOAD"
    PAY = "PAY"
    SEND = "SEND"
    PUBLISH = "PUBLISH"
    AUTHENTICATE = "AUTHENTICATE"
    PAGINATE = "PAGINATE"
    TOGGLE = "TOGGLE"
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    UNKNOWN = "UNKNOWN"


INTENT_PATTERNS: tuple[tuple[SemanticIntent, re.Pattern[str]], ...] = (
    (SemanticIntent.DELETE, re.compile(r"\b(delete|remove|erase|discard|destroy|unlink|revoke)\b", re.I)),
    (SemanticIntent.PAY, re.compile(r"\b(pay|purchase|buy|checkout|place order|subscribe|billing)\b", re.I)),
    (SemanticIntent.SEND, re.compile(r"\b(send|email|notify|invite|share|message)\b", re.I)),
    (SemanticIntent.PUBLISH, re.compile(r"\b(publish|post|deploy|release|go live)\b", re.I)),
    (SemanticIntent.EXPORT, re.compile(r"\b(export|download|save as|csv|pdf|xlsx)\b", re.I)),
    (SemanticIntent.UPLOAD, re.compile(r"\b(upload|attach|choose file|browse)\b", re.I)),
    (SemanticIntent.AUTHENTICATE, re.compile(r"\b(log ?in|sign ?in|log ?out|sign ?out|register|sign ?up)\b", re.I)),
    (SemanticIntent.SEARCH, re.compile(r"\b(search|find|lookup|query|filter by)\b", re.I)),
    (SemanticIntent.FILTER, re.compile(r"\b(filter|status|category|sort by|apply)\b", re.I)),
    (SemanticIntent.SORT, re.compile(r"\b(sort|order by|ascending|descending)\b", re.I)),
    (SemanticIntent.CREATE, re.compile(r"\b(add|create|new|insert|register)\b", re.I)),
    (SemanticIntent.UPDATE, re.compile(r"\b(edit|update|save|apply|rename|modify|change)\b", re.I)),
    (SemanticIntent.CONFIRM, re.compile(r"\b(confirm|yes|ok|proceed|continue|agree)\b", re.I)),
    (SemanticIntent.CANCEL, re.compile(r"\b(cancel|close|dismiss|no|back|reset)\b", re.I)),
    (SemanticIntent.PAGINATE, re.compile(r"\b(next|previous|prev|page \d+|first|last)\b", re.I)),
    (SemanticIntent.TOGGLE, re.compile(r"\b(toggle|switch|enable|disable)\b", re.I)),
    (SemanticIntent.NAVIGATE, re.compile(r"\b(dashboard|reports|settings|customers|products|home|menu)\b", re.I)),
)

DESTRUCTIVE_INTENTS = {
    SemanticIntent.DELETE,
    SemanticIntent.PAY,
    SemanticIntent.SEND,
    SemanticIntent.PUBLISH,
    SemanticIntent.AUTHENTICATE,
}

PARAMETER_HINTS: tuple[tuple[ParameterKind, re.Pattern[str]], ...] = (
    (ParameterKind.EMAIL, re.compile(r"\b(e-?mail)\b", re.I)),
    (ParameterKind.DATE, re.compile(r"\b(date|from|to|start|end|due|expiry|expires|month|day|year)\b", re.I)),
    (ParameterKind.NUMBER, re.compile(r"\b(amount|price|revenue|quantity|qty|count|number|age|zip|postal|phone|code)\b", re.I)),
    (ParameterKind.FILE, re.compile(r"\b(file|document|attachment|upload|id document)\b", re.I)),
    (ParameterKind.CHECKBOX, re.compile(r"\b(agree|confirm|accept|remember|subscribe|terms|notifications?)\b", re.I)),
)


@dataclass
class LabeledElement:
    element_id: str
    intent: SemanticIntent
    label: str
    destructive: bool
    reason: str


class SemanticLabeler:
    """Deterministic interpretation of observed controls and fields."""

    # Roles where the label names *what the field holds*, not what the page will
    # do.  "Email" as a text field means an address, not a "send email" action.
    FIELD_ROLES = {
        ElementRole.TEXT_FIELD,
        ElementRole.PASSWORD_FIELD,
        ElementRole.SEARCH,
        ElementRole.SELECT,
        ElementRole.CHECKBOX,
        ElementRole.RADIO,
        ElementRole.UPLOAD,
    }

    FIELD_INTENTS: tuple[tuple[SemanticIntent, re.Pattern[str]], ...] = (
        (SemanticIntent.SEARCH, re.compile(r"\b(search|find|filter|query)\b", re.I)),
        (SemanticIntent.FILTER, re.compile(r"\b(status|category|sort|rows per page)\b", re.I)),
    )

    def label_element(self, element: Element) -> LabeledElement:
        text = " ".join(
            filter(
                None,
                [
                    element.accessible_name,
                    element.visible_text,
                    element.attributes.get("name", ""),
                    element.attributes.get("__label", ""),
                    element.attributes.get("placeholder", ""),
                ],
            )
        )
        if element.semantic_role is ElementRole.UPLOAD:
            return LabeledElement(element.element_id, SemanticIntent.UPLOAD, text[:80], False, "file input role")
        if element.semantic_role is ElementRole.DOWNLOAD:
            return LabeledElement(element.element_id, SemanticIntent.DOWNLOAD, text[:80], False, "download semantics in label")
        if element.semantic_role in self.FIELD_ROLES:
            for intent, pattern in self.FIELD_INTENTS:
                if pattern.search(text):
                    return LabeledElement(
                        element.element_id, intent, text[:80], False, f"field label matched {pattern.pattern!r}"
                    )
            return LabeledElement(
                element.element_id,
                SemanticIntent.UNKNOWN,
                text[:80],
                False,
                "data field: its label names the value it holds, not an operation",
            )
        for intent, pattern in INTENT_PATTERNS:
            if pattern.search(text):
                return LabeledElement(
                    element.element_id,
                    intent,
                    text[:80],
                    intent in DESTRUCTIVE_INTENTS,
                    f"matched {pattern.pattern}",
                )
        return LabeledElement(element.element_id, SemanticIntent.UNKNOWN, text[:80], False, "no rule matched")

    def parameter_kind(self, element: Element) -> ParameterKind:
        probe = " ".join(
            filter(
                None,
                [
                    element.accessible_name,
                    element.attributes.get("name", ""),
                    element.attributes.get("__label", ""),
                    element.attributes.get("type", ""),
                ],
            )
        )
        if element.semantic_role is ElementRole.SELECT:
            return ParameterKind.SELECT
        if element.semantic_role is ElementRole.CHECKBOX:
            return ParameterKind.CHECKBOX
        if element.attributes.get("type") == "file" or element.semantic_role is ElementRole.UPLOAD:
            return ParameterKind.FILE
        input_type = (element.attributes.get("type") or "").lower()
        if input_type == "email":
            return ParameterKind.EMAIL
        if input_type in ("date", "datetime-local", "month", "week", "time"):
            return ParameterKind.DATE
        if input_type in ("number", "range"):
            return ParameterKind.NUMBER
        for kind, pattern in PARAMETER_HINTS:
            if pattern.search(probe):
                return kind
        return ParameterKind.TEXT

    def label_observation(self, observation: Observation) -> dict[str, LabeledElement]:
        return {element.element_id: self.label_element(element) for element in observation.interactive_elements}

    def observation_summary(self, observation: Observation) -> str:
        """A compact, deterministic description used in fingerprints and prompts."""
        parts: list[str] = []
        if observation.dialogs:
            parts.append("dialog: " + "; ".join(d.title or d.text[:60] for d in observation.dialogs))
        if observation.tables:
            table = observation.tables[0]
            parts.append(f"table: {table.row_count} rows, columns {', '.join(table.headers[:6])}")
        if observation.forms:
            parts.append(f"{len(observation.forms)} form(s)")
        if observation.alerts:
            parts.append("alerts: " + " | ".join(a.get("text", "")[:60] for a in observation.alerts[:3]))
        if observation.loading_state.is_loading:
            parts.append("loading")
        if observation.pagination.get("label"):
            parts.append(str(observation.pagination["label"]))
        intents = [self.label_element(e).intent for e in observation.interactive_elements[:60]]
        notable = [i.value for i in intents if i not in (SemanticIntent.UNKNOWN, SemanticIntent.NAVIGATE)]
        if notable:
            unique = sorted(set(notable))
            parts.append("controls: " + ", ".join(unique[:8]))
        parts.append(f"{len(observation.interactive_elements)} interactive elements")
        title = observation.title or ""
        return " | ".join([p for p in [title, *parts] if p])[:500]
