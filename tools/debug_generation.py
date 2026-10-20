"""Diagnostic: what candidate actions does the agent generate for the Add-customer modal?"""

from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    from blackbox.agent.config import Settings, demo_registration
    from blackbox.agent.main import BlackBoxAgent

    settings = Settings()
    agent = BlackBoxAgent(settings, demo_registration("demo_crm", mode="AUTONOMOUS"))
    await agent.start()
    try:
        page = agent.manager.page
        await page.navigate("http://127.0.0.1:3001/#/customers")
        await page.wait_for_stable(quiet_ms=400, timeout=6)
        observation = await agent.observer.observe(page)
        elements = agent.detector.detect(observation)
        observation.interactive_elements = elements

        add = next(e for e in elements if "add customer" in e.label().lower())
        await agent.executor.execute(page, agent.generator._make_candidate_click(add) if hasattr(agent.generator, "_make_candidate_click") else _click_action(add), element=add)
        await page.wait_for_stable(quiet_ms=350, timeout=6)

        modal = await agent.observer.observe(page)
        elements = agent.detector.detect(modal)
        modal.interactive_elements = elements
        print("total interactive elements:", len(elements))
        print("\n-- editable elements as the detector sees them --")
        for element in elements:
            if element.editable:
                kind = agent.generator.synthesizer.labeler.parameter_kind(element)
                value = agent.generator.synthesizer.synthesize(element)
                print(
                    f"  {element.semantic_role.value:12s} {element.label()[:22]:24s} visible={element.visible} "
                    f"in_dialog={element.in_dialog} type={element.attributes.get('type')!r} kind={kind.value} syn={value!r}"
                )

        candidates = agent.generator.generate(modal, elements=elements)
        print(f"\n-- generated candidates: {len(candidates)} (cap {agent.generator.config.max_actions_per_state}) --")
        for action in candidates:
            label = action.target.label() if action.target else "page"
            print(f"  {action.type.value:9s} {label[:34]:36s} {str(action.parameters)[:40]}")
        return 0
    finally:
        await agent.close()


def _click_action(element):
    from blackbox.agent.exploration.action_generator import ActionGenerator

    generator = ActionGenerator()
    return generator._make(
        __import__("blackbox.agent.model.action", fromlist=["ActionType"]).ActionType.CLICK,
        element,
        {},
        [],
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
