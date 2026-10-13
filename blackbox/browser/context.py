"""A single browser page driven over CDP.

This is the only place that touches raw browser input.  The agent above it works
with elements, locators and actions; everything below is honest browser
mechanics: real mouse events at element geometry, real text insertion, real key
events, and explicit waiting for the page to settle.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .cdp import CDPClosed, CDPConnection, CDPError
from .sandbox import OriginViolation, Sandbox

log = logging.getLogger(__name__)

KEY_CODES: dict[str, tuple[str, int]] = {
    "Enter": ("Enter", 13),
    "Tab": ("Tab", 9),
    "Escape": ("Escape", 27),
    "Backspace": ("Backspace", 8),
    "Delete": ("Delete", 46),
    "ArrowUp": ("ArrowUp", 38),
    "ArrowDown": ("ArrowDown", 40),
    "ArrowLeft": ("ArrowLeft", 37),
    "ArrowRight": ("ArrowRight", 39),
    "Home": ("Home", 36),
    "End": ("End", 35),
    "PageUp": ("PageUp", 33),
    "PageDown": ("PageDown", 34),
    " ": ("Space", 32),
}


class BrowserCrashed(RuntimeError):
    """The browser process or its CDP connection died."""


class NavigationFailed(RuntimeError):
    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"navigation to {url} failed: {detail}")
        self.url = url
        self.detail = detail


@dataclass
class PageEvents:
    """Observable browser-side events collected since the last reset."""

    console: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    dialogs: list[dict[str, Any]] = field(default_factory=list)
    navigations: list[dict[str, Any]] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    popups: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        for name in ("console", "errors", "dialogs", "navigations", "downloads", "popups"):
            getattr(self, name).clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "console": self.console[-50:],
            "errors": self.errors[-20:],
            "dialogs": self.dialogs[-10:],
            "navigations": self.navigations[-20:],
            "downloads": self.downloads[-10:],
            "popups": self.popups[-10:],
        }


class PageContext:
    """One inspected page: navigation, evaluation, input, observation."""

    def __init__(
        self,
        connection: CDPConnection,
        session_id: str,
        target_id: str,
        *,
        sandbox: Sandbox,
        download_dir: str | None = None,
    ) -> None:
        self.connection = connection
        self.session_id = session_id
        self.target_id = target_id
        self.sandbox = sandbox
        self.download_dir = download_dir
        self.events = PageEvents()
        self.last_url = "about:blank"
        self.closed = False
        self._network_count = 0
        self._mutation_counter_installed = False

    # -- plumbing ----------------------------------------------------------
    async def send(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
        if self.closed:
            raise BrowserCrashed(f"page {self.target_id} is closed")
        try:
            return await self.connection.send(method, params, session_id=self.session_id, timeout=timeout)
        except CDPClosed as exc:
            raise BrowserCrashed(str(exc)) from exc

    async def enable(self) -> None:
        for domain in ("Page", "Runtime", "DOM", "Network", "Accessibility", "Log"):
            try:
                await self.send(f"{domain}.enable")
            except CDPError as exc:  # pragma: no cover - domain availability differs by build
                log.debug("could not enable %s: %s", domain, exc)
        self.connection.on("Runtime.consoleAPICalled", self._on_console)
        self.connection.on("Runtime.exceptionThrown", self._on_exception)
        self.connection.on("Page.javascriptDialogOpening", self._on_dialog)
        self.connection.on("Page.frameNavigated", self._on_frame_navigated)
        self.connection.on("Network.requestWillBeSent", self._on_request)
        self.connection.on("Page.downloadWillBegin", self._on_download)
        self.connection.on("Target.targetCreated", self._on_target_created)
        if self.download_dir:
            try:
                await self.connection.send(
                    "Browser.setDownloadBehavior",
                    {"behavior": "allow", "downloadPath": self.download_dir, "eventsEnabled": True},
                )
            except CDPError as exc:
                log.debug("download behaviour not set: %s", exc)

    def _for_this_session(self, payload: dict[str, Any]) -> bool:
        return payload.get("sessionId") in (None, self.session_id)

    # -- event handlers ----------------------------------------------------
    def _on_console(self, payload: dict[str, Any]) -> None:
        if not self._for_this_session(payload):
            return
        params = payload.get("params", {})
        args = params.get("args", [])
        text = " ".join(str(a.get("value", a.get("description", ""))) for a in args).strip()
        entry = {"type": params.get("type", "log"), "text": text[:500], "ts": time.time()}
        self.events.console.append(entry)
        if params.get("type") in ("error", "assert"):
            self.events.errors.append(entry)

    def _on_exception(self, payload: dict[str, Any]) -> None:
        if not self._for_this_session(payload):
            return
        details = payload.get("params", {}).get("exceptionDetails", {})
        self.events.errors.append(
            {
                "type": "exception",
                "text": (details.get("text") or "") + " " + str(details.get("exception", {}).get("description", ""))[:300],
                "ts": time.time(),
            }
        )

    def _on_dialog(self, payload: dict[str, Any]) -> None:
        if not self._for_this_session(payload):
            return
        params = payload.get("params", {})
        self.events.dialogs.append(
            {"type": params.get("type"), "message": params.get("message"), "ts": time.time()}
        )
        # Never accept a JavaScript dialog automatically: dismissing is the safe
        # choice, and the event itself is recorded as observable behavior.
        asyncio.create_task(self._dismiss_dialog())

    async def _dismiss_dialog(self) -> None:
        try:
            await self.send("Page.handleJavaScriptDialog", {"accept": False})
        except (CDPError, BrowserCrashed):
            pass

    def _on_frame_navigated(self, payload: dict[str, Any]) -> None:
        if not self._for_this_session(payload):
            return
        frame = payload.get("params", {}).get("frame", {})
        if frame.get("parentId"):
            return
        url = frame.get("url", "")
        self.last_url = url or self.last_url
        self.events.navigations.append({"url": url, "ts": time.time()})

    def _on_request(self, payload: dict[str, Any]) -> None:
        # Network activity is counted, never captured: BlackBox does not
        # reconstruct APIs from traffic, so no URL paths, headers or bodies are
        # retained here.
        if not self._for_this_session(payload):
            return
        self._network_count += 1

    def _on_download(self, payload: dict[str, Any]) -> None:
        if not self._for_this_session(payload):
            return
        params = payload.get("params", {})
        self.events.downloads.append(
            {"suggested_filename": params.get("suggestedFilename"), "url": params.get("url"), "ts": time.time()}
        )

    def _on_target_created(self, payload: dict[str, Any]) -> None:
        params = payload.get("params", {}).get("targetInfo", {})
        if params.get("openerId"):
            self.events.popups.append(
                {"target_id": params.get("targetId"), "type": params.get("type"), "url": params.get("url")}
            )

    # -- navigation --------------------------------------------------------
    async def navigate(self, url: str, *, timeout: float = 30.0, wait: bool = True) -> dict[str, Any]:
        decision = self.sandbox.check(url, kind="navigation")
        if not decision.allowed:
            raise OriginViolation(url, decision.reason)
        try:
            result = await self.send("Page.navigate", {"url": url}, timeout=timeout)
        except CDPError as exc:
            raise NavigationFailed(url, str(exc)) from exc
        if result.get("errorText"):
            raise NavigationFailed(url, result["errorText"])
        if wait:
            await self.wait_for_ready(timeout=timeout)
        self.last_url = url
        return result

    async def wait_for_ready(self, *, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = await self.evaluate("document.readyState")
            except (BrowserCrashed, CDPError):
                return False
            if state in ("interactive", "complete"):
                await self.wait_for_stable(quiet_ms=250, timeout=max(1.0, deadline - time.monotonic()))
                return True
            await asyncio.sleep(0.1)
        return False

    async def wait_for_stable(self, *, quiet_ms: int = 300, timeout: float = 8.0) -> bool:
        """Wait until the DOM stops mutating (dynamic content settles)."""
        deadline = time.monotonic() + timeout
        await self._install_mutation_probe()
        last = await self._mutation_count()
        quiet_started = time.monotonic()
        while time.monotonic() < deadline:
            await asyncio.sleep(min(0.12, max(0.03, quiet_ms / 4000)))
            current = await self._mutation_count()
            if current != last:
                last = current
                quiet_started = time.monotonic()
                continue
            if (time.monotonic() - quiet_started) * 1000 >= quiet_ms:
                return True
        return False

    async def _install_mutation_probe(self) -> None:
        if self._mutation_counter_installed:
            return
        script = """
        (() => {
          if (window.__bbProbe) return;
          window.__bbProbe = { count: 0 };
          const bump = () => { window.__bbProbe.count += 1; };
          try {
            new MutationObserver(bump).observe(document.documentElement, {
              childList: true, subtree: true, attributes: true, characterData: true
            });
          } catch (e) {}
          window.__bbProbe.count += 1;
        })()
        """
        await self.evaluate(script)
        self._mutation_counter_installed = True

    async def _mutation_count(self) -> int:
        try:
            value = await self.evaluate("(window.__bbProbe && window.__bbProbe.count) || 0")
            return int(value or 0)
        except (BrowserCrashed, CDPError):
            return 0

    # -- evaluation --------------------------------------------------------
    async def evaluate(self, expression: str, *, await_promise: bool = False, timeout: float = 30.0) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            raise CDPError("Runtime.evaluate", {"message": details.get("text", "evaluation failed")})
        remote = result.get("result", {})
        return remote.get("value")

    async def evaluate_json(self, expression: str, *, timeout: float = 30.0) -> Any:
        """Evaluate an expression that returns a JSON string."""
        import json

        raw = await self.evaluate(expression, timeout=timeout)
        if raw in (None, ""):
            return None
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    # -- input -------------------------------------------------------------
    async def click_point(self, x: float, y: float) -> None:
        common = {"x": x, "y": y, "button": "left", "clickCount": 1}
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
        await self.send("Input.dispatchMouseEvent", {"type": "mousePressed", **common})
        await asyncio.sleep(0.02)
        await self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", **common})

    async def double_click_point(self, x: float, y: float) -> None:
        for count in (1, 2):
            common = {"x": x, "y": y, "button": "left", "clickCount": count}
            await self.send("Input.dispatchMouseEvent", {"type": "mousePressed", **common})
            await self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", **common})
            await asyncio.sleep(0.02)

    async def hover_point(self, x: float, y: float) -> None:
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})

    async def insert_text(self, text: str) -> None:
        await self.send("Input.insertText", {"text": text})

    async def type_text(self, text: str, *, delay_ms: int = 8) -> None:
        """Type like a user: real key events per character."""
        for char in text:
            await self.send(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "text": char, "unmodifiedText": char, "key": char},
            )
            await self.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": char})
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000.0)

    async def press_key(self, key: str) -> None:
        name, code = KEY_CODES.get(key, (key, 0))
        base = {"key": name, "code": name, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
        await self.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
        if name in ("Enter", "Tab", " "):
            await self.send("Input.dispatchKeyEvent", {"type": "char", "text": "\r" if name == "Enter" else name, **base})
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    async def hotkey(self, *keys: str) -> None:
        modifiers = 0
        modifier_map = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}
        for key in keys[:-1]:
            modifiers |= modifier_map.get(key, 0)
        final = keys[-1]
        name, code = KEY_CODES.get(final, (final, 0))
        base = {
            "key": name,
            "code": name,
            "windowsVirtualKeyCode": code,
            "nativeVirtualKeyCode": code,
            "modifiers": modifiers,
        }
        await self.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **base})
        await self.send("Input.dispatchKeyEvent", {"type": "keyUp", **base})

    async def scroll(self, *, delta_x: float = 0, delta_y: float = 400, x: float = 200, y: float = 300) -> None:
        await self.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": x, "y": y, "deltaX": delta_x, "deltaY": delta_y, "button": "none"},
        )

    # -- capture -----------------------------------------------------------
    async def screenshot(self, *, full_page: bool = False) -> bytes:
        params: dict[str, Any] = {"format": "png", "captureBeyondViewport": full_page}
        try:
            result = await self.send("Page.captureScreenshot", params, timeout=30.0)
        except CDPError:
            result = await self.send("Page.captureScreenshot", {"format": "png"}, timeout=30.0)
        return base64.b64decode(result["data"])

    async def url(self) -> str:
        try:
            value = await self.evaluate("location.href")
            return str(value or self.last_url)
        except (BrowserCrashed, CDPError):
            return self.last_url

    async def title(self) -> str:
        try:
            value = await self.evaluate("document.title")
            return str(value or "")
        except (BrowserCrashed, CDPError):
            return ""

    async def viewport(self) -> dict[str, int]:
        try:
            value = await self.evaluate("JSON.stringify({w: innerWidth, h: innerHeight})")
            import json

            data = json.loads(value) if value else {}
            return {"width": int(data.get("w", 0)), "height": int(data.get("h", 0))}
        except Exception:  # noqa: BLE001 - viewport is best-effort metadata
            return {"width": 0, "height": 0}

    # -- lifecycle ---------------------------------------------------------
    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for event, handler in (
            ("Runtime.consoleAPICalled", self._on_console),
            ("Runtime.exceptionThrown", self._on_exception),
            ("Page.javascriptDialogOpening", self._on_dialog),
            ("Page.frameNavigated", self._on_frame_navigated),
            ("Network.requestWillBeSent", self._on_request),
            ("Page.downloadWillBegin", self._on_download),
            ("Target.targetCreated", self._on_target_created),
        ):
            self.connection.off(event, handler)

    @property
    def network_request_count(self) -> int:
        return self._network_count
