"""Shared fixtures for the BlackBox test suite.

Anything that launches Chromium is marked ``browser``; anything that drives the
bundled demo sites is marked ``e2e`` and skips cleanly when the demo servers are
not running.  Nothing here writes to the real learned model in
``artifacts/blackbox.db``.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

from blackbox.agent.config import Settings
from blackbox.agent.model.action import Action, ActionType, EffectKind, RiskLevel, TargetSpec
from blackbox.agent.model.element import BoundingBox, Element, ElementRole, ValueState
from blackbox.agent.observation.observation import (
    BrowserState,
    DialogSummary,
    LoadingState,
    Observation,
    Viewport,
)
from blackbox.agent.perception.semantic_labeler import SemanticLabeler

DEMO_PORTS = {"demo_crm": 3001, "demo_ecommerce": 3002, "demo_project_manager": 3003, "demo_forms": 3004}


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at a throwaway database and artifacts directory."""
    return Settings(
        database_dsn=f"sqlite:///{tmp_path / 'test.db'}",
        artifacts_dir=tmp_path / "artifacts",
        headless=True,
        capture_screenshots=False,
        seed=7,
        unattended=True,
    )


@pytest.fixture
def labeler() -> SemanticLabeler:
    return SemanticLabeler()


def element(
    name: str,
    role: ElementRole = ElementRole.BUTTON,
    *,
    tag: str = "button",
    enabled: bool = True,
    visible: bool = True,
    editable: bool = False,
    attributes: dict[str, str] | None = None,
    value: str | None = None,
    checked: bool | None = None,
    box: tuple[float, float, float, float] | None = (10.0, 20.0, 100.0, 24.0),
    in_dialog: bool = False,
    section: str = "",
) -> Element:
    """Build an Element the way the observation pipeline would."""
    return Element(
        semantic_role=role,
        visible_text=name,
        accessible_name=name,
        tag=tag,
        attributes=attributes or {},
        bounding_box=BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3]) if box else None,
        enabled=enabled,
        visible=visible,
        editable=editable,
        value_state=ValueState(value=value, checked=checked),
        in_dialog=in_dialog,
        section=section,
    ).finalize()


def observation(
    url: str = "http://127.0.0.1:3001/#/customers",
    title: str = "Northwind CRM",
    text: str = "Customers Name Email Company Status Revenue Add customer",
    elements: list[Element] | None = None,
    *,
    dialogs: list[DialogSummary] | None = None,
    alerts: list[dict] | None = None,
    loading: bool = False,
    screenshot_hash: str = "0123456789abcdef",
    pagination: dict | None = None,
    ax_signature: list[str] | None = None,
    forms: list | None = None,
) -> Observation:
    """Build a synthetic multi-channel observation."""
    built = elements if elements is not None else [element("Add customer"), element("Customers", ElementRole.LINK, tag="a")]
    observation = Observation(
        url=url,
        title=title,
        viewport=Viewport(width=1440, height=900),
        screenshot_reference=f"/tmp/{screenshot_hash}.png",
        screenshot_hash=screenshot_hash,
        visible_text=text,
        interactive_elements=built,
        accessibility_tree={
            "nodes": [],
            "interactive_signature": ax_signature or [f"{e.semantic_role.value.lower()}:{e.label().lower()}" for e in built],
            "structure_signature": ["rootwebarea", "heading", "button"],
            "node_count": len(ax_signature or built),
            "available": True,
        },
        dialogs=dialogs or [],
        alerts=alerts or [],
        forms=forms or [],
        browser_state=BrowserState(url=url, title=title),
        loading_state=LoadingState(is_loading=loading, indicators=["Loading…"] if loading else []),
        pagination=pagination or {"label": "", "has_next": False, "has_previous": False},
        counts={"interactive": len(built), "hidden": 0, "ax_nodes": len(built)},
    )
    return observation.finalize()


def action(
    action_type: ActionType,
    target: Element | None,
    *,
    value: str | None = None,
    option: str | None = None,
    expected: list[EffectKind] | None = None,
    risk: RiskLevel = RiskLevel.LOW,
) -> Action:
    parameters: dict[str, object] = {}
    if value is not None:
        parameters["value"] = value
    if option is not None:
        parameters["option"] = option
    built = Action(
        type=action_type,
        target=TargetSpec(
            element_id=target.element_id if target else "",
            role=target.semantic_role.value if target else "",
            name=target.label() if target else "",
            tag=target.tag if target else "",
        ),
        parameters=parameters,
        expected_effect=expected or [],
        risk=risk,
    )
    return built.finalize()


def demo_available(target: str) -> bool:
    """Is the bundled demo server for this target listening?"""
    port = DEMO_PORTS.get(target)
    if port is None:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
def demo_crm_or_skip() -> str:
    if not demo_available("demo_crm"):
        pytest.skip("demo CRM server is not running on 127.0.0.1:3001")
    return "demo_crm"


@pytest.fixture
def demo_forms_or_skip() -> str:
    if not demo_available("demo_forms"):
        pytest.skip("demo forms server is not running on 127.0.0.1:3004")
    return "demo_forms"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
