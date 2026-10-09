"""Pages: clusters of states that a human would call "the same screen"."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .base import now_iso, stable_id


class Page(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_id: str = ""
    name: str = ""
    url_pattern: str = ""
    section: str = ""
    parent_page_id: str | None = None
    state_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    first_seen: str = Field(default_factory=now_iso)

    def finalize(self) -> "Page":
        if not self.page_id:
            self.page_id = stable_id("p", self.url_pattern, self.name)
        return self

    def describe(self) -> str:
        return f"{self.name} ({self.url_pattern}) states={len(self.state_ids)}"
