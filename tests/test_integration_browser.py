"""Integration tests: the real browser, the real observation pipeline.

These launch Chromium and are marked ``browser``.  They assert the properties the
whole system rests on: that observation fuses multiple channels, that real input
produces an observable state change, that a transition is only verified when the
predicted effect happened, and that the origin boundary and crash recovery work.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from blackbox.agent.execution.assertions import verify_effects
from blackbox.agent.execution.executor import ExecutionConfig, Executor
from blackbox.agent.execution.locator import LocatorResolver
from blackbox.agent.model.action import Action, ActionType, EffectKind, TargetSpec
from blackbox.agent.model.graph import ApplicationGraph
from blackbox.agent.model.transition import Transition
from blackbox.agent.observation.browser import PageObserver
from blackbox.agent.observation.network_guard import NetworkGuard
from blackbox.agent.perception.element_detector import ElementDetector
from blackbox.agent.perception.state_fingerprint import build_state, diff_states
from blackbox.browser.manager import BrowserManager
from blackbox.browser.permissions import default_policy
from blackbox.browser.sandbox import OriginViolation, Sandbox

pytestmark = pytest.mark.browser

PAGE = """
<!doctype html><html><head><title>Integration Target</title></head><body>
<h1>Customers</h1>
<form id="customer-form">
<label for="name">Name</label><input id="name" name="name" required>
<label for="email">Email</label><input id="email" name="email" type="email">
<button id="save" type="button" disabled>Save customer</button>
</form>
<button id="open">Open dialog</button>
<table><thead><tr><th>Name</th></tr></thead><tbody><tr><td>Amara</td></tr></tbody></table>
<dialog id="dlg"><h2>Add customer</h2><p>Fill the form</p><button>Cancel</button></dialog>
<div role="alert" style="display:none"></div>
<script>
const name = document.getElementById('name');
const email = document.getElementById('email');
const save = document.getElementById('save');
const sync = () => { save.disabled = !(name.value && email.value); };
name.addEventListener('input', sync);
email.addEventListener('input', sync);
document.getElementById('open').addEventListener('click', () => document.getElementById('dlg').showModal());
save.addEventListener('click', () => {
  const alert = document.querySelector('[role=alert]');
  alert.style.display = 'block';
  alert.textContent = email.value.includes('@') ? 'Customer created.' : 'Enter a valid email address.';
});
</script></body></html>
"""


def data_url() -> str:
    return "data:text/html;base64," + base64.b64encode(PAGE.encode()).decode()


@pytest.fixture
async def page(tmp_path: Path):
    sandbox = Sandbox(allowed_origins=["http://127.0.0.1:3001"])
    manager = BrowserManager(
        sandbox=sandbox,
        permissions=default_policy(tmp_path / "downloads"),
        artifacts_dir=tmp_path,
        headless=True,
    )
    page = await manager.start()
    try:
        yield manager, page
    finally:
        await manager.close()


@pytest.fixture
def observer(tmp_path: Path) -> PageObserver:
    return PageObserver(tmp_path / "artifacts", capture_screenshots=True)


async def test_observation_pipeline_fuses_channels_and_detects_elements(page, observer):
    manager, live = page
    await live.navigate(data_url())
    observation = await observer.observe(live)

    assert observation.url.startswith("data:text/html")
    assert observation.title == "Integration Target"
    assert "Customers" in observation.visible_text
    assert observation.screenshot_reference and Path(observation.screenshot_reference).exists()
    assert observation.screenshot_hash
    assert observation.accessibility_tree.get("available") is True
    assert observation.channel_health["dom"] and observation.channel_health["visual"]
    assert observation.tables and observation.tables[0].row_count == 1

    elements = ElementDetector().detect(observation)
    labels = {element.label().lower(): element for element in elements}
    assert "save customer" in labels
    assert labels["save customer"].enabled is False
    assert any(element.semantic_role.value == "TEXT_FIELD" for element in elements)
    assert all(element.locator_candidates for element in elements)


async def test_typing_and_clicking_produce_verified_transitions(page, observer, tmp_path):
    manager, live = page
    await live.navigate(data_url())
    before = await observer.observe(live)
    before.interactive_elements = ElementDetector().detect(before)

    executor = Executor(
        resolver=LocatorResolver(network_guard=NetworkGuard(allowed_origins=["http://127.0.0.1:3001"])),
        config=ExecutionConfig(upload_fixtures=tmp_path / "fixtures"),
    )

    name_field = next(e for e in before.interactive_elements if e.label().lower() == "name")
    email_field = next(e for e in before.interactive_elements if e.label().lower() == "email")
    save_button = next(e for e in before.interactive_elements if e.label().lower() == "save customer")

    type_action = Action(
        type=ActionType.TYPE,
        target=TargetSpec(element_id=name_field.element_id, role="TEXT_FIELD", name="Name", tag="input"),
        parameters={"value": "Yash"},
        expected_effect=[EffectKind.VALUE_CHANGED, EffectKind.FORM_CHANGED],
    ).finalize()
    result = await executor.execute(live, type_action, element=name_field)
    assert result.ok, result.error

    after_typing = await observer.observe(live)
    after_typing.interactive_elements = ElementDetector().detect(after_typing)
    diff = diff_states(before, after_typing)
    assert diff.changed and (diff.form_changes or diff.elements_changed), "typing must be an observable change"
    typed_state = [(element.label(), element.value_state.value) for element in after_typing.interactive_elements]
    assert any(
        element.label().lower() == "name" and element.value_state.value == "Yash"
        for element in after_typing.interactive_elements
    ), f"field values after typing: {typed_state}"

    verification = verify_effects(type_action, type_action.expected_effect, diff, after_typing)
    assert verification.verified, verification.describe()

    for field, value in ((email_field, "yash@example.com"),):
        email_action = Action(
            type=ActionType.TYPE,
            target=TargetSpec(element_id=field.element_id, role="TEXT_FIELD", name="Email", tag="input"),
            parameters={"value": value},
            expected_effect=[EffectKind.VALUE_CHANGED],
        ).finalize()
        assert (await executor.execute(live, email_action, element=field)).ok

    enabled = await observer.observe(live)
    enabled.interactive_elements = ElementDetector().detect(enabled)
    refreshed_save = next(e for e in enabled.interactive_elements if e.label().lower() == "save customer")
    assert refreshed_save.enabled is True, "the save button enables once both fields are filled"

    click = Action(
        type=ActionType.CLICK,
        target=TargetSpec(element_id=refreshed_save.element_id, role="BUTTON", name=refreshed_save.label(), tag="button"),
        expected_effect=[EffectKind.SUCCESS_MESSAGE],
    ).finalize()
    assert (await executor.execute(live, click, element=refreshed_save)).ok

    final = await observer.observe(live)
    assert any("Customer created." in str(alert.get("text", "")) for alert in final.alerts)

    graph = ApplicationGraph(target_id="integration")
    source, _ = graph.upsert_state(build_state(before, semantic_summary="before"))
    target, _ = graph.upsert_state(build_state(final, semantic_summary="after"))
    transition = Transition(source_state=source.state_id, action=click, target_state=target.state_id).finalize()
    graph.add_transition(transition)
    assert graph.transitions[transition.transition_id].target_state == target.state_id


async def test_origin_boundary_is_enforced_by_the_browser_layer(page):
    manager, live = page
    await live.navigate(data_url())
    with pytest.raises(OriginViolation):
        await live.navigate("http://127.0.0.1:3999/elsewhere")
    assert manager.sandbox.blocked_log
    assert manager.sandbox.blocked_log[-1]["reason"]


async def test_dialog_and_disabled_control_are_observed(page, observer):
    manager, live = page
    await live.navigate(data_url())
    await live.evaluate("document.getElementById('open').click()")
    await live.wait_for_stable(quiet_ms=200, timeout=4)
    observation = await observer.observe(live)
    assert observation.dialogs, "an open <dialog> must be observed"
    titles = " ".join(dialog.title for dialog in observation.dialogs)
    assert "Add customer" in titles


async def test_browser_crash_is_detected_and_recoverable(page):
    manager, live = page
    await live.navigate(data_url())
    assert manager.alive()
    manager._process.kill()
    for _ in range(40):
        if not manager.alive():
            break
        await asyncio.sleep(0.1)
    assert not manager.alive()
    # The watchdog polls, so give it a moment to record the crash it detected.
    for _ in range(30):
        if manager.health.crashes >= 1:
            break
        await asyncio.sleep(0.1)
    assert manager.health.crashes >= 1
    recovered = await manager.restart()
    await recovered.navigate(data_url())
    assert (await recovered.title()) == "Integration Target"
