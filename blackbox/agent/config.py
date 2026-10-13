"""Configuration, loaded from the environment with safe defaults.

Nothing here is secret-bearing by default: the LLM provider is optional, the
database defaults to a local SQLite file so a clone runs with zero
infrastructure, and credentials are read from the environment only if the
operator opts in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """Runtime settings for one BlackBox instance."""

    database_dsn: str = field(default_factory=lambda: os.environ.get("BLACKBOX_DB", "sqlite:///artifacts/blackbox.db"))
    artifacts_dir: Path = field(default_factory=lambda: Path(os.environ.get("BLACKBOX_ARTIFACTS", "artifacts")))
    headless: bool = field(default_factory=lambda: _flag("BLACKBOX_HEADLESS", True))
    recording: bool = field(default_factory=lambda: _flag("BLACKBOX_RECORDING", False))
    viewport_width: int = field(default_factory=lambda: int(os.environ.get("BLACKBOX_VIEWPORT_WIDTH", "1440")))
    viewport_height: int = field(default_factory=lambda: int(os.environ.get("BLACKBOX_VIEWPORT_HEIGHT", "900")))
    seed: int = field(default_factory=lambda: int(os.environ.get("BLACKBOX_SEED", "7")))
    capture_screenshots: bool = field(default_factory=lambda: _flag("BLACKBOX_SCREENSHOTS", True))
    llm_provider: str = field(default_factory=lambda: os.environ.get("BLACKBOX_LLM_PROVIDER", "none"))
    llm_model: str = field(default_factory=lambda: os.environ.get("BLACKBOX_LLM_MODEL", "gpt-4o-mini"))
    llm_base_url: str | None = field(default_factory=lambda: os.environ.get("BLACKBOX_LLM_BASE_URL"))
    llm_api_key: str | None = field(default_factory=lambda: os.environ.get("BLACKBOX_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    llm_reasoning_frequency: str = field(default_factory=lambda: os.environ.get("BLACKBOX_LLM_FREQUENCY", "low"))
    llm_cache: bool = field(default_factory=lambda: _flag("BLACKBOX_LLM_CACHE", True))
    chromium_executable: str | None = field(default_factory=lambda: os.environ.get("BLACKBOX_CHROMIUM"))
    approval_timeout_seconds: float = field(default_factory=lambda: float(os.environ.get("BLACKBOX_APPROVAL_TIMEOUT", "120")))
    unattended: bool = field(default_factory=lambda: _flag("BLACKBOX_UNATTENDED", True))

    def ensure_dirs(self) -> None:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        (self.artifacts_dir / "screenshots").mkdir(parents=True, exist_ok=True)
        (self.artifacts_dir / "downloads").mkdir(parents=True, exist_ok=True)
        (self.artifacts_dir / "fixtures").mkdir(parents=True, exist_ok=True)
        (self.artifacts_dir / "models").mkdir(parents=True, exist_ok=True)
        (self.artifacts_dir / "benchmarks").mkdir(parents=True, exist_ok=True)

    def describe(self) -> dict[str, object]:
        return {
            "database_dsn": self.database_dsn,
            "artifacts_dir": str(self.artifacts_dir),
            "headless": self.headless,
            "recording": self.recording,
            "seed": self.seed,
            "capture_screenshots": self.capture_screenshots,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_reasoning_frequency": self.llm_reasoning_frequency,
            "unattended": self.unattended,
        }


DEMO_APPS: dict[str, dict[str, object]] = {
    "demo_crm": {
        "base_url": "http://127.0.0.1:3001",
        "path": "blackbox/demo/crm",
        "port": 3001,
        "description": "CRM with customer table, search, filters, pagination, add/edit validation and delete confirmation",
    },
    "demo_ecommerce": {
        "base_url": "http://127.0.0.1:3002",
        "path": "blackbox/demo/ecommerce",
        "port": 3002,
        "description": "Storefront with product filters, cart quantities, coupons and a simulated checkout",
    },
    "demo_project_manager": {
        "base_url": "http://127.0.0.1:3003",
        "path": "blackbox/demo/project_manager",
        "port": 3003,
        "description": "Project and task manager with dependencies, inline status changes and filterable task lists",
    },
    "demo_forms": {
        "base_url": "http://127.0.0.1:3004",
        "path": "blackbox/demo/forms",
        "port": 3004,
        "description": "Four-step wizard with conditional fields, validation and a confirmation step",
    },
}


def demo_registration(target_id: str, *, mode: str = "AUTONOMOUS", **overrides: object):
    """Build a TargetRegistration for one of the bundled demo applications."""
    from .storage.targets import TargetRegistration

    spec = DEMO_APPS[target_id]
    base_url = str(overrides.pop("base_url", spec["base_url"]))
    payload = {
        "target_id": target_id,
        "base_url": base_url,
        "allowed_origins": [base_url],
        "max_steps": int(overrides.pop("max_steps", 300)),
        "max_duration_seconds": float(overrides.pop("max_duration_seconds", 600)),
        "mode": mode,
        "authorized_by": "local research operator",
        "allow_medium_actions": True,
        "notes": f"bundled demo target: {spec['description']}",
    }
    payload.update(overrides)
    return TargetRegistration(**payload)
