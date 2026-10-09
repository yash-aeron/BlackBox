"""Diagnostic: does typing into a field produce an observable state change?

Run: python tools/debug_typing.py
"""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    from blackbox.agent.config import Settings, demo_registration
    from blackbox.agent.main import BlackBoxAgent
    from blackbox.agent.model.action import Action, ActionType, TargetSpec
    from blackbox.agent.perception.state_fingerprint import build_state, diff_states

    settings = Settings()
    registration = demo_registration("demo_crm", mode="AUTONOMOUS")
    agent = BlackBoxAgent(settings, registration)
    await agent.start()
    try:
        page = agent.manager.page
        await page.navigate("http://127.0.0.1:3001/#/customers")
        await page.wait_for_stable(quiet_ms=400, timeout=6)

        before = await agent.observer.observe(page)
        before.interactive_elements = agent.detector.detect(before)
        print("form fields before:", [(f.label, f.value) for f in before.forms[0].fields] if before.forms else "none")

        add = next((e for e in before.interactive_elements if "add customer" in e.label().lower()), None)
        print("add button:", add.label() if add else "NOT FOUND")
        if add is None:
            return 1

        result = await agent.executor.execute(
            page,
            Action(type=ActionType.CLICK, target=TargetSpec(element_id=add.element_id, role=add.semantic_role.value, name=add.label()), expected_effect=[]).finalize(),
            element=add,
        )
        print("click add:", result.status, result.error, result.locator_used)
        await page.wait_for_stable(quiet_ms=350, timeout=6)

        modal = await agent.observer.observe(page)
        modal.interactive_elements = agent.detector.detect(modal)
        print("dialogs:", [d.title for d in modal.dialogs])
        for element in modal.interactive_elements:
            if element.in_dialog:
                print(
                    f"  in-dialog {element.semantic_role.value:12s} {element.label()[:26]:28s} "
                    f"val={element.value_state.value!r} form={element.in_form} id={element.element_id}"
                )

        name_field = next(
            (e for e in modal.interactive_elements if e.in_dialog and "name" in e.label().lower() and e.editable),
            None,
        )
        print("name field:", name_field.label() if name_field else "NOT FOUND", name_field.element_id if name_field else "")
        if name_field is None:
            return 1

        for variant in ("real-typing", "insertText"):
            observation_before = await agent.observer.observe(page)
            observation_before.interactive_elements = agent.detector.detect(observation_before)
            if variant == "real-typing":
                await page.click_point(*name_field.bounding_box.center)
                await page.type_text("BlackBox Sample")
            else:
                await page.click_point(*name_field.bounding_box.center)
                await page.insert_text("BlackBox Sample")
            await page.wait_for_stable(quiet_ms=250, timeout=5)
            after = await agent.observer.observe(page)
            after.interactive_elements = agent.detector.detect(after)
            diff = diff_states(observation_before, after)
            field_after = next((e for e in after.interactive_elements if e.element_id == name_field.element_id), None)
            print(f"\n[{variant}] value now: {field_after.value_state.value!r}")
            print(f"[{variant}] raw DOM value:", await page.evaluate(
                "(() => { const i = document.querySelector('dialog[open] input, [role=dialog] input'); return i ? i.value : null; })()"
            ))
            print(f"[{variant}] diff changed={diff.changed} elements_changed={diff.elements_changed[:3]} form_changes={diff.form_changes[:3]}")
            print(f"[{variant}] before form sig:", observation_before.forms[0].signature if observation_before.forms else None)
            print(f"[{variant}] after  form sig:", after.forms[0].signature if after.forms else None)
            print(f"[{variant}] element ids before:", [e.element_id for e in observation_before.interactive_elements if e.editable][:6])
            print(f"[{variant}] element ids after :", [e.element_id for e in after.interactive_elements if e.editable][:6])
        return 0
    finally:
        await agent.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
