"""Load a page in Chromium and report console output and uncaught exceptions with stacks.

Usage:
    python tools/debug_page.py --url http://127.0.0.1:8099/ --click "State Graph"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from blackbox.browser.cdp import CDPConnection, LaunchOptions, launch_chromium


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--click", default=None)
    parser.add_argument("--wait", type=int, default=4000)
    parser.add_argument("--eval", default=None, help="extra JS to evaluate before reporting")
    args = parser.parse_args()

    browser = launch_chromium(LaunchOptions(headless=True, window_size=(1600, 1000)))
    connection: CDPConnection | None = None
    messages: list[str] = []
    try:
        ws_url = await browser.resolve_ws_url()
        connection = CDPConnection(ws_url)
        await connection.connect()
        target = await connection.send("Target.createTarget", {"url": "about:blank"})
        attached = await connection.send("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        session = attached["sessionId"]

        def on_console(payload: dict) -> None:
            if payload.get("sessionId") != session:
                return
            params = payload.get("params", {})
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args", []))
            messages.append(f"[console.{params.get('type')}] {text[:400]}")

        def on_exception(payload: dict) -> None:
            if payload.get("sessionId") != session:
                return
            details = payload.get("params", {}).get("exceptionDetails", {})
            description = str(details.get("exception", {}).get("description", details.get("text", "")))
            stack = details.get("stackTrace", {}).get("callFrames", [])[:8]
            frames = "\n".join(
                f"    at {frame.get('functionName') or '<anonymous>'} "
                f"({frame.get('url', '')}:{frame.get('lineNumber')}:{frame.get('columnNumber')})"
                for frame in stack
            )
            messages.append(f"[exception] {description[:600]}\n{frames}")

        connection.on("Runtime.consoleAPICalled", on_console)
        connection.on("Runtime.exceptionThrown", on_exception)
        for domain in ("Page", "Runtime", "DOM", "Log"):
            await connection.send(f"{domain}.enable", session_id=session)
        await connection.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False},
            session_id=session,
        )
        await connection.send("Page.navigate", {"url": args.url}, session_id=session)
        await asyncio.sleep(args.wait / 1000.0)

        if args.click:
            script = """
            ((wanted) => {
              const nodes = Array.from(document.querySelectorAll('button, a, [role=tab]'));
              const target = nodes.find((el) => (el.innerText || '').trim().toLowerCase().includes(wanted.toLowerCase()));
              if (target) { target.click(); return true; }
              return false;
            })(%s)
            """ % json.dumps(args.click)
            clicked = await connection.send("Runtime.evaluate", {"expression": script, "returnByValue": True}, session_id=session)
            print(f"click {args.click!r}: {clicked.get('result', {}).get('value')}")
            await asyncio.sleep(2.5)

        if args.eval:
            result = await connection.send(
                "Runtime.evaluate",
                {"expression": args.eval, "returnByValue": True, "awaitPromise": True},
                session_id=session,
            )
            print("eval:", json.dumps(result.get("result", {}), default=str)[:2000])

        state = await connection.send(
            "Runtime.evaluate",
            {
                "expression": "JSON.stringify({view: document.querySelector('.view.active') && document.querySelector('.view.active').id, html: (document.querySelector('.view.active')||{}).innerHTML ? document.querySelector('.view.active').innerHTML.slice(0,300) : ''})",
                "returnByValue": True,
            },
            session_id=session,
        )
        print("active view:", str(state.get("result", {}).get("value"))[:600].encode("ascii", "replace").decode("ascii"))
        print("--- messages ---")
        for message in messages[-25:]:
            print(message)
        return 0
    finally:
        if connection:
            await connection.close()
        browser.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
