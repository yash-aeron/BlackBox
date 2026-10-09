"""Smoke test for the CDP browser layer: launch, navigate, observe, screenshot, close."""

from __future__ import annotations

import asyncio
import base64
import json
import sys

from blackbox.browser.cdp import CDPConnection, LaunchOptions, launch_chromium

HTML = """
<!doctype html><html><head><title>Smoke Target</title></head>
<body>
  <h1>Customers</h1>
  <form id="f"><label for="name">Name</label><input id="name" name="name">
  <button type="submit">Save</button></form>
  <a href="/reports">Reports</a>
</body></html>
"""


async def main() -> int:
    browser = launch_chromium(LaunchOptions(headless=True))
    print(f"launched pid={browser.pid} port={browser.port}")
    connection: CDPConnection | None = None
    try:
        ws_url = await browser.resolve_ws_url()
        print(f"devtools: {ws_url[:60]}...")
        connection = CDPConnection(ws_url)
        await connection.connect()
        version = await connection.send("Browser.getVersion")
        print(f"browser: {version['product']} / protocol {version['protocolVersion']}")

        target = await connection.send("Target.createTarget", {"url": "about:blank"})
        attached = await connection.send(
            "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
        )
        session = attached["sessionId"]

        await connection.send("Page.enable", session_id=session)
        await connection.send("Runtime.enable", session_id=session)
        await connection.send("DOM.enable", session_id=session)
        await connection.send("Accessibility.enable", session_id=session)

        url = "data:text/html;base64," + base64.b64encode(HTML.encode()).decode()
        await connection.send("Page.navigate", {"url": url}, session_id=session)
        await asyncio.sleep(0.7)

        title = await connection.send(
            "Runtime.evaluate",
            {"expression": "document.title", "returnByValue": True},
            session_id=session,
        )
        print("title:", title["result"]["value"])

        html = await connection.send(
            "Runtime.evaluate",
            {"expression": "document.documentElement.outerHTML", "returnByValue": True},
            session_id=session,
        )
        print("html bytes:", len(html["result"]["value"]))

        ax = await connection.send("Accessibility.getFullAXTree", session_id=session)
        roles = [n.get("role", {}).get("value") for n in ax["nodes"]]
        print("ax nodes:", len(ax["nodes"]), "sample roles:", roles[:8])

        shot = await connection.send(
            "Page.captureScreenshot", {"format": "png"}, session_id=session
        )
        print("screenshot b64 bytes:", len(shot["data"]))

        box = await connection.send(
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify(document.querySelector('button').getBoundingClientRect())",
                "returnByValue": True,
            },
            session_id=session,
        )
        print("button box:", box["result"]["value"])

        await connection.send("Target.closeTarget", {"targetId": target["targetId"]})
        print("OK")
        return 0
    finally:
        if connection:
            await connection.close()
        browser.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
