"""Task parsing: natural language in, structured goal and entities out.

Deterministic patterns run first because they are reproducible and auditable; an
LLM (when configured) may only refine the same structured fields through the
provider abstraction, and its output is validated before it is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..model.base import normalize_text, stable_id

VERB_TO_GOAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(create|add|new|register|insert)\b", re.I), "create"),
    (re.compile(r"\b(update|edit|change|modify|rename)\b", re.I), "update"),
    (re.compile(r"\b(delete|remove|erase|discard)\b", re.I), "delete"),
    (re.compile(r"\b(search|find|look ?up|locate)\b", re.I), "search"),
    (re.compile(r"\b(filter|narrow|show only)\b", re.I), "filter"),
    (re.compile(r"\b(export|download|save as)\b", re.I), "export"),
    (re.compile(r"\b(submit|send|apply|file)\b", re.I), "submit"),
    (re.compile(r"\b(checkout|place order|buy|purchase|pay)\b", re.I), "checkout"),
    (re.compile(r"\b(complete|finish|close out)\b", re.I), "complete"),
    (re.compile(r"\b(open|view|show|go to|navigate to|inspect)\b", re.I), "view"),
    (re.compile(r"\b(log ?in|sign ?in)\b", re.I), "authenticate"),
)

ENTITY_NOUNS = (
    "customer",
    "lead",
    "contact",
    "deal",
    "record",
    "entry",
    "product",
    "order",
    "task",
    "project",
    "report",
    "invoice",
    "user",
    "account",
    "application",
    "booking",
    "ticket",
    "item",
    "member",
    "coupon",
    "document",
    "setting",
    "profile",
    "cart",
    "form",
)

NAMED = re.compile(r"(?:named|called|with (?:the )?name|name (?:is|of))\s+[\"']?([A-Za-z][\w'\-]*(?:\s+[A-Z][\w'\-]*)*)[\"']?", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
QUOTED = re.compile(r"[\"']([^\"']{2,80})[\"']")
WITH_FIELD = re.compile(r"\bwith\s+(?:the\s+)?([a-z][a-z ]{2,20}?)\s+(?:set to|of|=|:)?\s*[\"']?([^\"',.;]{1,60})[\"']?(?=[,.;]|$)", re.I)
NUMBER_FIELD = re.compile(r"\b(price|amount|quantity|qty|revenue|income|budget|count|number)\b[^\d\-]{0,12}(-?\d+(?:\.\d+)?)", re.I)
DATE_VALUE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
RELATIVE_DATE = re.compile(r"\b(today|yesterday|tomorrow|last (?:month|week|year)|this (?:month|week|year))\b", re.I)
PATTERN_VALUE = re.compile(r"\b([A-Z]{3}-\d{4})\b")

CONSTRAINT_HINTS = re.compile(
    r"\b(without|before|after|only if|unless|but do not|don't|do not)\b", re.I
)


@dataclass
class TaskSpec:
    raw: str
    goal: str = ""
    entities: dict[str, str] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    expected_end_state: str = ""
    entity_noun: str = ""
    verb: str = ""
    task_id: str = ""
    parse_source: str = "deterministic"
    # Machine-checkable success conditions supplied by the caller, used to aim
    # planning at the real goal rather than at keyword overlap.
    predicates: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.task_id:
            self.task_id = stable_id("task", normalize_text(self.raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "entities": self.entities,
            "constraints": self.constraints,
            "expected_end_state": self.expected_end_state,
            "source_text": self.raw,
            "parse_source": self.parse_source,
            "predicates": self.predicates,
        }


class TaskParser:
    """Turns "Create a customer named Yash with email yash@example.com" into data."""

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    def parse(self, text: str) -> TaskSpec:
        task = TaskSpec(raw=text)
        normalized = text.strip()

        for pattern, verb in VERB_TO_GOAL:
            if pattern.search(normalized):
                task.verb = verb
                break
        lower = normalize_text(normalized)
        noun = next((candidate for candidate in ENTITY_NOUNS if candidate in lower), "")
        task.entity_noun = noun
        task.goal = f"{task.verb}_{noun}" if task.verb and noun else (task.verb or noun or "perform_task")

        entities = self._extract_entities(normalized)
        task.entities = entities
        task.constraints = [
            match.group(0).strip()
            for match in CONSTRAINT_HINTS.finditer(normalized)
        ]
        task.expected_end_state = self._expected_end_state(task)
        return task

    def _extract_entities(self, text: str) -> dict[str, str]:
        entities: dict[str, str] = {}
        for match in EMAIL.finditer(text):
            entities.setdefault("email", match.group(0))
        named = NAMED.search(text)
        if named:
            entities.setdefault("name", self._trim_person_name(named.group(1)))
        for match in PATTERN_VALUE.finditer(text):
            entities.setdefault("reference_code", match.group(1))
        for match in DATE_VALUE.finditer(text):
            entities.setdefault("date", match.group(1))
        relative = RELATIVE_DATE.search(text)
        if relative and "date" not in entities:
            entities["date"] = relative.group(1).lower()
        for match in NUMBER_FIELD.finditer(text):
            entities.setdefault(match.group(1).lower(), match.group(2))
        for match in WITH_FIELD.finditer(text):
            key = normalize_text(match.group(1)).replace(" ", "_")
            value = match.group(2).strip()
            if key and value and key not in ("the",):
                entities.setdefault(key, value)
        for match in QUOTED.finditer(text):
            value = match.group(1).strip()
            if value and value not in entities.values() and not EMAIL.fullmatch(value):
                # A quoted value with no field name is most likely the record name.
                entities.setdefault("name" if "name" not in entities else f"value_{len(entities)}", value)
        return entities

    @staticmethod
    def _trim_person_name(raw: str) -> str:
        """Keep the capitalised part of a captured name.

        The capturing pattern is case-insensitive so it can find "named"/"called"
        in any casing, which means it would otherwise swallow following lowercase
        words ("Yash with email yash@example.com").  Stop at the first lowercase
        word after the first token, and drop trailing connectives.
        """
        words = [word for word in raw.strip().split() if word]
        if not words:
            return ""
        kept = [words[0]]
        for word in words[1:]:
            if word[:1].isupper():
                kept.append(word)
            else:
                break
        while kept and normalize_text(kept[-1]) in ("with", "and", "of", "the"):
            kept.pop()
        return " ".join(kept)

    def _expected_end_state(self, task: TaskSpec) -> str:
        if task.verb == "create" and task.entity_noun:
            return f"{task.entity_noun}_created"
        if task.verb == "update" and task.entity_noun:
            return f"{task.entity_noun}_updated"
        if task.verb == "delete" and task.entity_noun:
            return f"{task.entity_noun}_deleted"
        if task.verb == "export":
            return "export_completed"
        if task.verb == "search":
            return "results_filtered"
        if task.verb == "checkout":
            return "order_placed"
        if task.verb == "submit":
            return "form_submitted"
        return f"{task.verb or 'task'}_done"

    def bind_parameters(self, task: TaskSpec, parameter_names: list[str]) -> dict[str, str]:
        """Map task entities onto a workflow's parameter names."""
        bound: dict[str, str] = {}
        used: set[str] = set()
        for name in parameter_names:
            key = normalize_text(name).replace(" ", "_")
            if key in task.entities:
                bound[name] = task.entities[key]
                used.add(key)
                continue
            for entity_key, value in task.entities.items():
                if entity_key in used:
                    continue
                if entity_key in key or key in entity_key:
                    bound[name] = value
                    used.add(entity_key)
                    break
            else:
                if "email" in key and "email" in task.entities:
                    bound[name] = task.entities["email"]
                elif "name" in key and "name" in task.entities:
                    bound[name] = task.entities["name"]
                elif "date" in key and "date" in task.entities:
                    bound[name] = task.entities["date"]
                elif "number" in key and task.entities:
                    numeric = next((v for k, v in task.entities.items() if re.fullmatch(r"-?\d+(\.\d+)?", v)), "")
                    if numeric:
                        bound[name] = numeric
        return bound
