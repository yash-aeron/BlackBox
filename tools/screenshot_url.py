"""Load a URL in Chromium and save a screenshot (used to verify the dashboard).

Usage:
    python tools/screenshot_url.py --url http://127.0.0.1:8099/ --out artifacts/dashboard.png
    python tools/screenshot_url.py --url http://127.0.0.1:8099/ --wait 3000 --click "State Graph"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from blackbox.browser.cdp import CDPConnection, LaunchOptions, launch_chromium


async def capture(url: str, out: Path, *, wait_ms: int, click_text: str | None, width: int, height: int, full_page: bool) -> int:
    import base64

    browser = launch_chromium(LaunchOptions(headless=True, window_size=(width, height)))
    connection: CDPConnection | None = None
    try:
        ws_url = await browser.resolve_ws_url()
        connection = CDPConnection(ws_url)
        await connection.connect()
        target = await connection.send("Target.createTarget", {"url": "about:blank"})
        attached = await connection.send("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        session = attached["sessionId"]
        for domain in ("Page", "Runtime", "DOM"):
            await connection.send(f"{domain}.enable", session_id=session)
        await connection.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
            session_id=session,
        )
        await connection.send("Page.navigate", {"url": url}, session_id=session)
        await asyncio.sleep(wait_ms / 1000.0)

        if click_text:
            script = """
            ((wanted) => {
              const nodes = Array.from(document.querySelectorAll('button, a, [role=tab], [role=button], li'));
              const target = nodes.find((el) => (el.innerText || '').trim().toLowerCase().includes(wanted.toLowerCase()));
              if (target) { target.click(); return true; }
              return false;
            })(%s)
            """ % repr(click_text).replace("'", '"')
            clicked = await connection.send("Runtime.evaluate", {"expression": script, "returnByValue": True}, session_id=session)
            print(f"click {click_text!r}: {clicked.get('result', {}).get('value')}")
            await asyncio.sleep(max(1.5, wait_ms / 2000.0))

        shot = await connection.send(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": full_page}, session_id=session
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(shot["data"]))
        title = await connection.send("Runtime.evaluate", {"expression": "document.title", "returnByValue": True}, session_id=session)
        body = await connection.send(
            "Runtime.evaluate",
            {"expression": "(document.body.innerText || '').slice(0, 400)", "returnByValue": True},
            session_id=session,
        )
        print(f"saved {out} ({out.stat().st_size} bytes) title={title.get('result', {}).get('value')!r}")
        print("--- visible text ---")
        print(body.get("result", {}).get("value"))
        await connection.send("Target.closeTarget", {"targetId": target["targetId"]})
        return 0
    finally:
        if connection:
            await connection.close()
        browser.terminate()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", default="artifacts/screenshot.png")
    parser.add_argument("--wait", type=int, default=4000)
    parser.add_argument("--click", default=None)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--full-page", action="store_true")
    args = parser.parse_args()
    return asyncio.run(
        capture(
            args.url,
            Path(args.out),
            wait_ms=args.wait,
            click_text=args.click,
            width=args.width,
            height=args.height,
            full_page=args.full_page,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
