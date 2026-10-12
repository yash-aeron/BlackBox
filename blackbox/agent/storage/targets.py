"""Target registration: nothing is explored without being explicitly registered.

An unregistered URL is refused.  Registration fixes the authorized origins, the
execution mode and the exploration budget, so the agent's freedom is bounded by a
human decision recorded up front rather than by good intentions at runtime.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..exploration.safety import ExecutionMode


class TargetRegistration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_id: str
    base_url: str
    allowed_origins: list[str] = Field(default_factory=list)
    max_steps: int = 500
    max_duration_seconds: float = 900.0
    mode: ExecutionMode = ExecutionMode.SUPERVISED
    notes: str = ""
    allow_medium_actions: bool = False
    allow_high_actions: bool = False
    allow_critical_actions: bool = False
    authorized_by: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @field_validator("target_id")
    @classmethod
    def _identifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("target_id must not be empty")
        if any(char in cleaned for char in " /\\"):
            raise ValueError("target_id must not contain spaces or slashes")
        return cleaned

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("base_url must be an http(s) URL")
        if not parsed.netloc:
            raise ValueError("base_url must include a host")
        return value

    def normalized_origins(self) -> list[str]:
        origins = set()
        for origin in self.allowed_origins:
            parsed = urlparse(origin if "://" in origin else f"http://{origin}")
            if parsed.scheme and parsed.hostname:
                port = f":{parsed.port}" if parsed.port else ""
                origins.add(f"{parsed.scheme}://{parsed.hostname.lower()}{port}")
        if not origins:
            parsed = urlparse(self.base_url)
            port = f":{parsed.port}" if parsed.port else ""
            origins.add(f"{parsed.scheme}://{(parsed.hostname or '').lower()}{port}")
        return sorted(origins)

    def validate_authorization(self) -> list[str]:
        """Registration is only meaningful if it names what it may touch."""
        problems: list[str] = []
        if not self.allowed_origins:
            problems.append("allowed_origins is empty; the base_url origin will be used implicitly")
        base_origin = urlparse(self.base_url)
        base = f"{base_origin.scheme}://{base_origin.netloc.lower()}"
        if self.normalized_origins() and not any(origin.startswith(base.split("://")[0]) for origin in self.normalized_origins()):
            problems.append(f"base_url origin {base} is not among allowed_origins")
        if self.mode is ExecutionMode.AUTONOMOUS and not self.authorized_by:
            problems.append("AUTONOMOUS mode without an authorizing party recorded")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class TargetRegistry:
    """In-memory registry with optional persistence, refusing unknown targets."""

    def __init__(self) -> None:
        self.targets: dict[str, TargetRegistration] = {}

    def register(self, registration: TargetRegistration) -> TargetRegistration:
        import time

        registration.created_at = registration.created_at or time.time()
        registration.updated_at = time.time()
        self.targets[registration.target_id] = registration
        return registration

    def get(self, target_id: str) -> TargetRegistration:
        target = self.targets.get(target_id)
        if target is None:
            raise KeyError(
                f"target {target_id!r} is not registered. BlackBox never explores an unregistered URL."
            )
        return target

    def list(self) -> list[TargetRegistration]:
        return sorted(self.targets.values(), key=lambda t: t.target_id)

    def remove(self, target_id: str) -> bool:
        return self.targets.pop(target_id, None) is not None
