"""Action representation, risk levels and observed effects."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import normalize_text, stable_id


class ActionType(str, Enum):
    CLICK = "CLICK"
    TYPE = "TYPE"
    CLEAR = "CLEAR"
    SELECT = "SELECT"
    CHECK = "CHECK"
    UNCHECK = "UNCHECK"
    SUBMIT = "SUBMIT"
    PRESS_KEY = "PRESS_KEY"
    HOTKEY = "HOTKEY"
    SCROLL = "SCROLL"
    OPEN_MENU = "OPEN_MENU"
    CLOSE_DIALOG = "CLOSE_DIALOG"
    NAVIGATE_BACK = "NAVIGATE_BACK"
    NAVIGATE = "NAVIGATE"
    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"
    WAIT_FOR_STATE = "WAIT_FOR_STATE"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}[self.value]


class EffectKind(str, Enum):
    NAVIGATION = "NAVIGATION"
    URL_CHANGE = "URL_CHANGE"
    DIALOG_OPENED = "DIALOG_OPENED"
    DIALOG_CLOSED = "DIALOG_CLOSED"
    ELEMENTS_ADDED = "ELEMENTS_ADDED"
    ELEMENTS_REMOVED = "ELEMENTS_REMOVED"
    ELEMENTS_CHANGED = "ELEMENTS_CHANGED"
    TEXT_CHANGED = "TEXT_CHANGED"
    FORM_CHANGED = "FORM_CHANGED"
    VALUE_CHANGED = "VALUE_CHANGED"
    VALIDATION_MESSAGE = "VALIDATION_MESSAGE"
    SUCCESS_MESSAGE = "SUCCESS_MESSAGE"
    LOADING_STARTED = "LOADING_STARTED"
    LOADING_FINISHED = "LOADING_FINISHED"
    DATA_CHANGED = "DATA_CHANGED"
    PRECONDITION_BLOCKED = "PRECONDITION_BLOCKED"
    NO_EFFECT = "NO_EFFECT"
    ERROR = "ERROR"


class TargetSpec(BaseModel):
    """How to re-find the element an action was learned against."""

    model_config = ConfigDict(extra="ignore")

    element_id: str = ""
    role: str = ""
    name: str = ""
    tag: str = ""
    locators: list[dict[str, Any]] = Field(default_factory=list)

    def label(self) -> str:
        return self.name or self.role or self.tag or self.element_id


class Action(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action_id: str = ""
    type: ActionType = ActionType.CLICK
    target: TargetSpec | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_effect: list[EffectKind] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW
    confidence: float = 0.5
    # Provenance: deterministic generator, hypothesis test, planner or LLM proposal.
    origin: str = "generator"
    rationale: str = ""

    def finalize(self) -> "Action":
        if not self.action_id:
            self.action_id = stable_id(
                "a",
                self.type.value,
                self.target.element_id if self.target else "",
                self.target.label() if self.target else "",
                sorted(self.parameters.items()),
            )
        return self

    def signature(self) -> str:
        target = self.target.label() if self.target else ""
        value = self.parameters.get("value") or self.parameters.get("option") or ""
        return normalize_text(f"{self.type.value} {target} {value}")

    def describe(self) -> str:
        target = self.target.label() if self.target else "page"
        detail = ""
        if self.type is ActionType.TYPE:
            detail = f" = {self.parameters.get('value', '')!r}"
        elif self.type in (ActionType.SELECT,):
            detail = f" -> {self.parameters.get('option', '')!r}"
        elif self.type is ActionType.PRESS_KEY:
            detail = f" {self.parameters.get('key', '')}"
        elif self.type is ActionType.NAVIGATE:
            detail = f" {self.parameters.get('url', '')}"
        return f"{self.type.value} {target}{detail}".strip()


class ObservedEffect(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: EffectKind
    detail: str = ""
    before: str | None = None
    after: str | None = None
    element_id: str | None = None
    message: str | None = None


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Action
    status: str = "OK"  # OK | BLOCKED | FAILED | TIMEOUT
    locator_used: str | None = None
    attempts: int = 1
    duration_ms: float = 0.0
    error: str | None = None
    blocked_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "OK"
