"""Capture authentic, high-resolution screenshots of the real BlackBox project in action.

Produces:
1. docs/images/1_state_graph_inspector.png - Real BlackBox dashboard with interactive state graph & selected node panel
2. docs/images/2_mined_workflows.png        - Real Workflows view with parameter bindings & step actions
3. docs/images/3_target_crm_modal.png       - Real Northwind CRM app (port 3001) with customer creation modal
4. docs/images/4_target_project_board.png   - Real Project Management app (port 3003) with task boards & progress
5. docs/images/5_dashboard_overview.png     - Real BlackBox system overview with targets, fingerprints & controls
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

from blackbox.browser.cdp import CDPConnection, LaunchOptions, launch_chromium

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("capture_photos")

OUT_DIR = Path("docs/images")
WIDTH = 1600
HEIGHT = 1000
DEVICE_SCALE = 1.5  # 2400x1500 effective rendering for retina clarity


async def capture_page(
    conn: CDPConnection,
    url: str,
    output_path: Path,
    *,
    setup_script: str | None = None,
    wait_before: float = 2.5,
) -> None:
    target = await conn.send("Target.createTarget", {"url": "about:blank"})
    target_id = target["targetId"]
    attached = await conn.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    sid = attached["sessionId"]

    for domain in ("Page", "Runtime", "DOM"):
        await conn.send(f"{domain}.enable", session_id=sid)

    await conn.send(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": WIDTH,
            "height": HEIGHT,
            "deviceScaleFactor": DEVICE_SCALE,
            "mobile": False,
        },
        session_id=sid,
    )

    log.info("Navigating to %s", url)
    await conn.send("Page.navigate", {"url": url}, session_id=sid)
    await asyncio.sleep(wait_before)

    if setup_script:
        log.info("Running setup script on %s", url)
        res = await conn.send(
            "Runtime.evaluate",
            {"expression": setup_script, "awaitPromise": True, "returnByValue": True},
            session_id=sid,
        )
        val = res.get("result", {}).get("value")
        log.info("Setup script returned: %s", val)
        await asyncio.sleep(1.5)

    shot = await conn.send("Page.captureScreenshot", {"format": "png"}, session_id=sid)
    data = base64.b64decode(shot["data"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    log.info("Saved %s (%d bytes)", output_path, len(data))

    await conn.send("Target.closeTarget", {"targetId": target_id})


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    browser = launch_chromium(LaunchOptions(headless=True, window_size=(WIDTH, HEIGHT)))
    try:
        ws_url = await browser.resolve_ws_url()
        conn = CDPConnection(ws_url)
        await conn.connect()

        # 1. State Graph with selected node & detailed inspector panel
        graph_script = """
        (async () => {
            const graphTab = document.querySelector('[data-view="graph"]');
            if (graphTab) graphTab.click();
            await new Promise(r => setTimeout(r, 1200));

            // Select node representing customers table/modal to display inspection details
            const nodes = Array.from(document.querySelectorAll('g.node'));
            if (nodes.length > 2) {
                // Click a key node
                nodes[2].dispatchEvent(new MouseEvent('click', { bubbles: true }));
            }
            await new Promise(r => setTimeout(r, 1000));
            return 'graph tab selected with node clicked';
        })()
        """
        await capture_page(
            conn,
            "http://127.0.0.1:8099/",
            OUT_DIR / "1_state_graph_inspector.png",
            setup_script=graph_script,
            wait_before=3.0,
        )

        # 2. Mined Workflows view
        workflows_script = """
        (async () => {
            const tab = document.querySelector('[data-view="workflows"]');
            if (tab) tab.click();
            await new Promise(r => setTimeout(r, 1000));
            return 'workflows tab selected';
        })()
        """
        await capture_page(
            conn,
            "http://127.0.0.1:8099/",
            OUT_DIR / "2_mined_workflows.png",
            setup_script=workflows_script,
            wait_before=2.5,
        )

        # 3. Target Application: Northwind CRM with 'Add Customer' modal open
        crm_modal_script = """
        (async () => {
            const addBtn = Array.from(document.querySelectorAll('button, a'))
                .find(el => el.innerText && el.innerText.trim() === 'Add customer');
            if (addBtn) addBtn.click();
            await new Promise(r => setTimeout(r, 800));
            return 'crm add customer modal opened';
        })()
        """
        await capture_page(
            conn,
            "http://127.0.0.1:3001/#/customers",
            OUT_DIR / "3_target_crm_modal.png",
            setup_script=crm_modal_script,
            wait_before=2.0,
        )

        # 4. Target Application: Project Management board
        await capture_page(
            conn,
            "http://127.0.0.1:3003/",
            OUT_DIR / "4_target_project_board.png",
            wait_before=2.0,
        )

        # 5. Dashboard Overview (Targets, Fingerprints, Activity)
        overview_script = """
        (async () => {
            const tab = document.querySelector('[data-view="overview"]');
            if (tab) tab.click();
            await new Promise(r => setTimeout(r, 800));
            return 'overview tab selected';
        })()
        """
        await capture_page(
            conn,
            "http://127.0.0.1:8099/",
            OUT_DIR / "5_dashboard_overview.png",
            setup_script=overview_script,
            wait_before=2.5,
        )

        await conn.close()
    finally:
        browser.terminate()
    log.info("All 5 project photos captured successfully!")


if __name__ == "__main__":
    asyncio.run(main())
