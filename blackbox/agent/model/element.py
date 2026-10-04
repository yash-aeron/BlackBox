"""Interactive element representation and locator candidates.

Elements are described by *what they are to a user* (role, accessible name,
label, observable attributes) rather than by a single fragile selector.  Every
element carries an ordered list of locator candidates so that execution can
fall back when the page shifts, and so locator quality is measurable.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import normalize_text, stable_id


class ElementRole(str, Enum):
    BUTTON = "BUTTON"
    LINK = "LINK"
    TEXT_FIELD = "TEXT_FIELD"
    PASSWORD_FIELD = "PASSWORD_FIELD"
    CHECKBOX = "CHECKBOX"
    RADIO = "RADIO"
    SELECT = "SELECT"
    OPTION = "OPTION"
    TAB = "TAB"
    MENU = "MENU"
    MENU_ITEM = "MENU_ITEM"
    TABLE = "TABLE"
    TABLE_ROW = "TABLE_ROW"
    TABLE_CELL = "TABLE_CELL"
    DIALOG = "DIALOG"
    FORM = "FORM"
    SEARCH = "SEARCH"
    PAGINATION = "PAGINATION"
    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"
    TEXT = "TEXT"
    UNKNOWN = "UNKNOWN"


class LocatorStrategy(str, Enum):
    """Ordered from most to least robust.  Coordinates are a last resort only."""

    ROLE_NAME = "ROLE_NAME"
    LABEL = "LABEL"
    PLACEHOLDER = "PLACEHOLDER"
    TEST_ID = "TEST_ID"
    ELEMENT_ID = "ELEMENT_ID"
    NAME_ATTR = "NAME_ATTR"
    SEMANTIC_ATTR = "SEMANTIC_ATTR"
    TEXT = "TEXT"
    CSS_PATH = "CSS_PATH"
    COORDINATES = "COORDINATES"


STRATEGY_BASE_CONFIDENCE: dict[LocatorStrategy, float] = {
    LocatorStrategy.ROLE_NAME: 0.95,
    LocatorStrategy.LABEL: 0.92,
    LocatorStrategy.PLACEHOLDER: 0.85,
    LocatorStrategy.TEST_ID: 0.9,
    LocatorStrategy.ELEMENT_ID: 0.88,
    LocatorStrategy.NAME_ATTR: 0.82,
    LocatorStrategy.SEMANTIC_ATTR: 0.78,
    LocatorStrategy.TEXT: 0.7,
    LocatorStrategy.CSS_PATH: 0.55,
    LocatorStrategy.COORDINATES: 0.25,
}


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


class LocatorCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strategy: LocatorStrategy
    selector: str
    confidence: float = 0.5
    evidence: str = ""
    # Some strategies need extra parameters at resolution time (e.g. role+name).
    params: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.strategy.value}({self.selector})"


class ValueState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str | None = None
    checked: bool | None = None
    selected: str | None = None
    disabled_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.value or "").strip()


class Element(BaseModel):
    """An interactive or structurally meaningful node observed on a page."""

    model_config = ConfigDict(extra="ignore")

    element_id: str = ""
    semantic_role: ElementRole = ElementRole.UNKNOWN
    visible_text: str = ""
    accessible_name: str = ""
    tag: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    bounding_box: BoundingBox | None = None
    parent_id: str | None = None
    children_ids: list[str] = Field(default_factory=list)
    enabled: bool = True
    visible: bool = True
    focused: bool = False
    editable: bool = False
    value_state: ValueState = Field(default_factory=ValueState)
    locator_candidates: list[LocatorCandidate] = Field(default_factory=list)
    confidence: float = 0.5
    source: str = "dom"
    # Page-unique handle used for in-page actions during one observation.
    snapshot_ref: str | None = None
    in_dialog: bool = False
    in_form: str | None = None
    section: str = ""

    def identity_key(self) -> str:
        """Stable key used to match this element across states."""
        return "|".join(
            [
                self.semantic_role.value,
                normalize_text(self.accessible_name or self.visible_text),
                (self.attributes.get("name") or "").lower(),
            ]
        )

    def finalize(self) -> "Element":
        if not self.element_id:
            self.element_id = stable_id("e", self.identity_key(), self.tag)
        return self

    @property
    def is_interactive(self) -> bool:
        return self.semantic_role not in (ElementRole.TEXT, ElementRole.TABLE, ElementRole.TABLE_ROW, ElementRole.TABLE_CELL, ElementRole.UNKNOWN) or self.tag in {
            "input",
            "button",
            "select",
            "textarea",
            "a",
        }

    def label(self) -> str:
        return self.accessible_name or self.visible_text or self.attributes.get("name", "") or self.tag


class FormField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    element_id: str
    name: str = ""
    label: str = ""
    role: ElementRole = ElementRole.TEXT_FIELD
    required: bool = False
    value: str = ""
    checked: bool | None = None
    options: list[str] = Field(default_factory=list)


class FormSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    form_id: str
    name: str = ""
    action_hint: str = ""
    fields: list[FormField] = Field(default_factory=list)
    submit_element_ids: list[str] = Field(default_factory=list)
    in_dialog: bool = False

    @property
    def signature(self) -> list[str]:
        """Structural signature: which fields exist. Used for state identity."""
        return sorted(f"{f.role.value}:{normalize_text(f.label or f.name)}" for f in self.fields)

    @property
    def state_signature(self) -> list[str]:
        """Value-aware signature: what the fields currently hold.

        State identity uses the structural signature so that typing does not
        invent a new application state, while change detection uses this one so
        that typing is still an observable effect.
        """
        return sorted(
            f"{normalize_text(f.label or f.name)}={f.value or ''}|checked={bool(f.checked)}" for f in self.fields
        )
