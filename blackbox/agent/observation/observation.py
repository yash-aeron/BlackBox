"""The Observation record: everything the browser showed us at one moment."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..model.base import now_iso, stable_id
from ..model.element import Element, FormSummary


class Viewport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    width: int = 0
    height: int = 0


class LinkSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    element_id: str
    text: str = ""
    href: str = ""
    external: bool = False


class TableSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    element_id: str | None = None
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    row_count: int = 0
    rows: list[list[str]] = Field(default_factory=list)
    cell_actions: list[str] = Field(default_factory=list)


class DialogSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    role: str = "dialog"
    text: str = ""
    actions: list[str] = Field(default_factory=list)
    modal: bool = True

    def signature(self) -> str:
        return f"{self.role}:{self.title}:{sorted(self.actions)}"


class LoadingState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    is_loading: bool = False
    indicators: list[str] = Field(default_factory=list)
    aria_busy: int = 0

    def signature(self) -> str:
        return "loading" if self.is_loading else "idle"


class BrowserState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = ""
    title: str = ""
    can_go_back: bool = False
    cookie_names: list[str] = Field(default_factory=list)
    scroll_x: int = 0
    scroll_y: int = 0
    document_height: int = 0
    popups: list[dict[str, Any]] = Field(default_factory=list)
    native_dialogs: list[dict[str, Any]] = Field(default_factory=list)
    downloads: list[dict[str, Any]] = Field(default_factory=list)
    console_errors: list[dict[str, Any]] = Field(default_factory=list)


class Observation(BaseModel):
    """Multi-channel snapshot. No single channel is authoritative."""

    model_config = ConfigDict(extra="ignore")

    observation_id: str = ""
    timestamp: str = Field(default_factory=now_iso)
    url: str = ""
    title: str = ""
    viewport: Viewport = Field(default_factory=Viewport)
    screenshot_reference: str | None = None
    screenshot_hash: str = ""
    visible_text: str = ""
    interactive_elements: list[Element] = Field(default_factory=list)
    accessibility_tree: dict[str, Any] = Field(default_factory=dict)
    forms: list[FormSummary] = Field(default_factory=list)
    dialogs: list[DialogSummary] = Field(default_factory=list)
    tables: list[TableSummary] = Field(default_factory=list)
    links: list[LinkSummary] = Field(default_factory=list)
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    lists: list[dict[str, Any]] = Field(default_factory=list)
    iframes: list[dict[str, Any]] = Field(default_factory=list)
    browser_state: BrowserState = Field(default_factory=BrowserState)
    loading_state: LoadingState = Field(default_factory=LoadingState)
    pagination: dict[str, Any] = Field(default_factory=dict)
    channel_health: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, Any] = Field(default_factory=dict)
    captured_ms: float = 0.0
    deterministic: bool = True

    def finalize(self) -> "Observation":
        if not self.observation_id:
            self.observation_id = stable_id(
                "o", self.url, self.title, self.screenshot_hash, len(self.interactive_elements), self.timestamp
            )
        return self

    def element(self, element_id: str) -> Element | None:
        for element in self.interactive_elements:
            if element.element_id == element_id:
                return element
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "url": self.url,
            "title": self.title,
            "elements": len(self.interactive_elements),
            "forms": len(self.forms),
            "dialogs": len(self.dialogs),
            "alerts": len(self.alerts),
            "loading": self.loading_state.is_loading,
            "screenshot": bool(self.screenshot_reference),
        }
