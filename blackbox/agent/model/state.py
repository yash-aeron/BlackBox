"""Website state: what BlackBox believes the application looks like right now.

A state is never identified by URL alone.  Two observations of the same URL with
different filters, dialogs, form values or selected tabs are different states,
and the multi-signal fingerprint is what tells them apart.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import now_iso, stable_id
from .element import Element, FormSummary


class StateStatus(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class PaginationState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_label: str = ""
    page_index: int | None = None
    page_count: int | None = None
    has_next: bool = False
    has_previous: bool = False

    def signature(self) -> str:
        return f"{self.page_label}|{self.has_next}|{self.has_previous}"


class DialogState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    role: str = "dialog"
    actions: list[str] = Field(default_factory=list)
    text: str = ""

    def signature(self) -> str:
        return f"{self.role}:{self.title}:{sorted(self.actions)}"


class SessionContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    authenticated: bool = False
    user_hint: str | None = None
    cookie_names: list[str] = Field(default_factory=list)
    # Session state is observable but never persisted with values.
    notes: list[str] = Field(default_factory=list)


class StateFingerprint(BaseModel):
    """Multi-signal signature of an observed state."""

    model_config = ConfigDict(extra="ignore")

    url: str = ""
    url_key: str = ""
    title: str = ""
    text_signature: str = ""
    text_shingles: list[str] = Field(default_factory=list)
    element_signature: list[str] = Field(default_factory=list)
    accessibility_signature: list[str] = Field(default_factory=list)
    form_signature: list[str] = Field(default_factory=list)
    selected_controls: dict[str, str] = Field(default_factory=dict)
    dialog_signature: list[str] = Field(default_factory=list)
    pagination_signature: str = ""
    screenshot_hash: str = ""
    semantic_summary: str = ""

    def canonical(self) -> dict[str, Any]:
        return {
            "url_key": self.url_key,
            "title": self.title,
            "text": self.text_signature,
            "elements": sorted(self.element_signature),
            "a11y": self.accessibility_signature,
            "forms": sorted(self.form_signature),
            "controls": sorted(self.selected_controls.items()),
            "dialogs": sorted(self.dialog_signature),
            "pagination": self.pagination_signature,
            "screenshot": self.screenshot_hash,
        }


class StateSimilarity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total: float = 0.0
    same_url: bool = False
    signals: dict[str, float] = Field(default_factory=dict)
    verdict: str = "DIFFERENT"  # SAME | SAME_PAGE | DIFFERENT
    notes: list[str] = Field(default_factory=list)


class WebsiteState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    state_id: str = ""
    fingerprint: StateFingerprint = Field(default_factory=StateFingerprint)
    page_identity: str = ""
    page_id: str = ""
    url: str = ""
    title: str = ""
    visible_text: str = ""
    visible_elements: list[Element] = Field(default_factory=list)
    forms: list[FormSummary] = Field(default_factory=list)
    dialogs: list[DialogState] = Field(default_factory=list)
    pagination: PaginationState = Field(default_factory=PaginationState)
    session: SessionContext = Field(default_factory=SessionContext)
    semantic_summary: str = ""
    confidence: float = 0.5
    status: StateStatus = StateStatus.CURRENT
    observation_id: str = ""
    screenshot_ref: str | None = None
    first_seen: str = Field(default_factory=now_iso)
    last_seen: str = Field(default_factory=now_iso)
    visit_count: int = 0

    def finalize(self) -> "WebsiteState":
        if not self.state_id:
            self.state_id = stable_id("s", self.fingerprint.canonical())
        return self

    def element_by_id(self, element_id: str) -> Element | None:
        for element in self.visible_elements:
            if element.element_id == element_id:
                return element
        return None

    def interactive_elements(self) -> list[Element]:
        return [e for e in self.visible_elements if e.is_interactive and e.visible]

    def label(self) -> str:
        return self.title or self.fingerprint.url_key or self.state_id

    def summary_line(self) -> str:
        return f"{self.state_id} {self.label()} ({len(self.visible_elements)} elements)"
