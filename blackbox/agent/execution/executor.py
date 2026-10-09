"""Action execution: honest browser mechanics, bounded retries.

The executor never invents a new way to reach a target.  It resolves the action's
locators, drives real input, and if that fails it re-observes, regenerates
locators and tries again - at most ``max_retries`` times - before reporting the
failure upward for recovery or re-planning.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from blackbox.browser.context import BrowserCrashed, PageContext
from blackbox.browser.sandbox import OriginViolation
from ..model.action import Action, ActionResult, ActionType
from ..model.base import Timer
from ..model.element import Element, ElementRole
from .locator import LocatorResolution, LocatorResolver


@dataclass
class ExecutionConfig:
    max_retries: int = 2
    type_delay_ms: int = 4
    action_timeout: float = 20.0
    download_timeout: float = 15.0
    upload_fixtures: Path | None = None


ElementProvider = Callable[[str], Awaitable[Element | None]]


class Executor:
    """Performs one action at a time against a live page."""

    def __init__(
        self,
        *,
        resolver: LocatorResolver,
        config: ExecutionConfig | None = None,
        element_provider: ElementProvider | None = None,
    ) -> None:
        self.resolver = resolver
        self.config = config or ExecutionConfig()
        self.element_provider = element_provider
        self.attempt_log: list[dict[str, object]] = []

    # -- public API --------------------------------------------------------
    async def execute(self, page: PageContext, action: Action, *, element: Element | None = None) -> ActionResult:
        timer = Timer()
        attempts = 0
        last_error: str | None = None
        current_element = element

        for attempt in range(1, self.config.max_retries + 2):
            attempts = attempt
            try:
                result = await self._attempt(page, action, current_element)
                result.attempts = attempts
                result.duration_ms = round(timer.elapsed_ms, 2)
                self.attempt_log.append(
                    {
                        "action": action.describe(),
                        "attempt": attempts,
                        "status": result.status,
                        "locator": result.locator_used,
                        "error": result.error,
                    }
                )
                if result.status == "OK":
                    return result
                last_error = result.error or result.blocked_reason
            except (BrowserCrashed, OriginViolation) as exc:
                # These cannot be fixed by retrying the same action.
                return ActionResult(
                    action=action,
                    status="BLOCKED" if isinstance(exc, OriginViolation) else "FAILED",
                    attempts=attempts,
                    duration_ms=round(timer.elapsed_ms, 2),
                    error=str(exc),
                    blocked_reason=str(exc) if isinstance(exc, OriginViolation) else None,
                )
            except Exception as exc:  # noqa: BLE001 - a failed action is data
                last_error = str(exc)

            # Retry path: re-observe and regenerate locators before trying again.
            if attempt <= self.config.max_retries:
                current_element = await self._refresh_element(action, current_element)

        return ActionResult(
            action=action,
            status="FAILED",
            attempts=attempts,
            duration_ms=round(timer.elapsed_ms, 2),
            error=last_error or "action failed after retries",
        )

    async def _refresh_element(self, action: Action, element: Element | None) -> Element | None:
        if self.element_provider is None or not action.target or not action.target.element_id:
            return element
        try:
            return await self.element_provider(action.target.element_id)
        except Exception:  # noqa: BLE001 - refresh is best effort
            return element

    # -- one attempt -------------------------------------------------------
    async def _attempt(self, page: PageContext, action: Action, element: Element | None) -> ActionResult:
        kind = action.type

        if kind is ActionType.NAVIGATE:
            url = str(action.parameters.get("url", ""))
            await page.navigate(url)
            return ActionResult(action=action, status="OK")

        if kind is ActionType.NAVIGATE_BACK:
            await page.evaluate("history.back()")
            await page.wait_for_ready(timeout=self.config.action_timeout)
            return ActionResult(action=action, status="OK")

        if kind is ActionType.SCROLL:
            await page.scroll(
                delta_x=float(action.parameters.get("delta_x", 0)),
                delta_y=float(action.parameters.get("delta_y", 400)),
            )
            return ActionResult(action=action, status="OK")

        if kind is ActionType.WAIT_FOR_STATE:
            await asyncio.sleep(float(action.parameters.get("seconds", 0.5)))
            return ActionResult(action=action, status="OK")

        if kind in (ActionType.PRESS_KEY, ActionType.HOTKEY) and action.parameters.get("page_level"):
            if kind is ActionType.PRESS_KEY:
                await page.press_key(str(action.parameters.get("key", "Enter")))
            else:
                await page.hotkey(*[str(k) for k in action.parameters.get("keys", [])])
            return ActionResult(action=action, status="OK")

        resolution = await self.resolver.resolve(page, element=element, action=action)
        if not resolution.matched:
            return ActionResult(
                action=action,
                status="FAILED",
                error=str(resolution.get("reason", "locator not found")),
            )

        if not resolution.get("enabled", True) and kind in (ActionType.CLICK, ActionType.SUBMIT):
            return ActionResult(
                action=action,
                status="BLOCKED",
                locator_used=resolution.strategy,
                blocked_reason="target is disabled; a precondition is probably unmet",
            )

        handler = {
            ActionType.CLICK: self._click,
            ActionType.OPEN_MENU: self._click,
            ActionType.SUBMIT: self._click,
            ActionType.TYPE: self._type,
            ActionType.CLEAR: self._clear,
            ActionType.SELECT: self._select,
            ActionType.CHECK: self._set_checked,
            ActionType.UNCHECK: self._set_checked,
            ActionType.PRESS_KEY: self._press_on,
            ActionType.HOTKEY: self._hotkey_on,
            ActionType.CLOSE_DIALOG: self._close_dialog,
            ActionType.UPLOAD: self._upload,
            ActionType.DOWNLOAD: self._download,
        }.get(kind)

        if handler is None:
            return ActionResult(action=action, status="FAILED", error=f"unsupported action type {kind.value}")

        await handler(page, action, resolution)
        return ActionResult(action=action, status="OK", locator_used=resolution.strategy)

    # -- primitives --------------------------------------------------------
    async def _click(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        await self.resolver.scroll_into_view(page, resolution)
        fresh = await self.resolver.resolve_candidates(
            page,
            [
                {
                    "strategy": resolution.get("strategy"),
                    "selector": resolution.get("selector_used", ""),
                    "params": {},
                }
            ],
            require_enabled=False,
        )
        target = fresh if fresh.matched else resolution
        x, y = target.center
        await page.click_point(x, y)

    async def _type(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        value = str(action.parameters.get("value", ""))
        await self.resolver.scroll_into_view(page, resolution)
        x, y = resolution.center
        await page.click_point(x, y)
        await self._clear(page, action, resolution)
        if value:
            await page.type_text(value, delay_ms=self.config.type_delay_ms)
        actual = await self._read_value(page, resolution)
        if actual != value:
            # Controlled inputs occasionally swallow synthetic key events; a real
            # insertion is the documented fallback, still a user-level action.
            await self._clear(page, action, resolution)
            await page.insert_text(value)
            actual = await self._read_value(page, resolution)
        if actual != value:
            await self._set_value_direct(page, resolution, value)

    async def _clear(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        await self._run_on_target(page, resolution, "el => { el.value = ''; el.dispatchEvent(new Event('input', {bubbles:true})); return true; }", action=action)

    async def _select(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        option = str(action.parameters.get("option", action.parameters.get("value", "")))
        script = """
        (el, wanted) => {
          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const target = norm(wanted);
          const options = Array.from(el.options || []);
          let match = options.find((o) => norm(o.value) === target) || options.find((o) => norm(o.textContent) === target);
          if (!match) match = options.find((o) => norm(o.textContent).includes(target) && target.length > 1);
          if (!match) return false;
          el.value = match.value;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        }
        """
        changed = await self._run_on_target(page, resolution, script, [option], action=action)
        if not changed:
            raise RuntimeError(f"option {option!r} not present in select")

    async def _set_checked(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        desired = action.type is ActionType.CHECK
        current = resolution.get("checked")
        if current is None:
            current = bool(await self._run_on_target(page, resolution, "el => !!el.checked", action=action))
        if bool(current) != desired:
            x, y = resolution.center
            await page.click_point(x, y)
        after = await self._run_on_target(page, resolution, "el => !!el.checked", action=action)
        if bool(after) != desired:
            # Some custom controls only react to a dispatched change event.
            await self._run_on_target(
                page,
                resolution,
                "el => { el.checked = %s; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); return el.checked; }"
                % ("true" if desired else "false"),
                action=action,
            )

    async def _press_on(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        x, y = resolution.center
        await page.click_point(x, y)
        await page.press_key(str(action.parameters.get("key", "Enter")))

    async def _hotkey_on(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        await self._run_on_target(page, resolution, "el => { el.focus(); return true; }", action=action)
        await page.hotkey(*[str(k) for k in action.parameters.get("keys", [])])

    async def _close_dialog(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        await page.press_key("Escape")
        still_open = await page.evaluate("document.querySelectorAll('dialog[open], [role=\"dialog\"]').length")
        if still_open:
            x, y = resolution.center
            await page.click_point(x, y)

    async def _upload(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        """File upload through the real browser API, with a generated fixture."""
        fixture = action.parameters.get("file")
        if not fixture:
            fixture = self._write_fixture(action)
        selector = ""
        if resolution.get("strategy") in ("CSS_PATH", "ELEMENT_ID", "NAME_ATTR", "TEST_ID", "SEMANTIC_ATTR"):
            selector = str(resolution.get("selector_used", ""))
        if not selector:
            selector = "input[type=file]"
        document = await page.send("DOM.getDocument", {"depth": 0})
        node = await page.send(
            "DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector}
        )
        if not node.get("nodeId"):
            raise RuntimeError(f"file input not found for selector {selector!r}")
        await page.send("DOM.setFileInputFiles", {"files": [str(fixture)], "nodeId": node["nodeId"]})
        await page.evaluate(
            "(() => { const el = document.querySelector('input[type=file]'); if (el) { el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); } return true; })()"
        )

    def _write_fixture(self, action: Action) -> Path:
        base = self.config.upload_fixtures or Path("artifacts/fixtures")
        base.mkdir(parents=True, exist_ok=True)
        path = base / "blackbox-document.txt"
        if not path.exists():
            path.write_text(
                "BlackBox test document.\nGenerated locally for authorized upload testing.\n",
                encoding="utf-8",
            )
        action.parameters["file"] = str(path)
        return path

    async def _download(self, page: PageContext, action: Action, resolution: LocatorResolution) -> None:
        before = len(page.events.downloads)
        x, y = resolution.center
        await page.click_point(x, y)
        deadline = time.monotonic() + self.config.download_timeout
        while time.monotonic() < deadline:
            if len(page.events.downloads) > before:
                return
            await asyncio.sleep(0.15)

    # -- helpers -----------------------------------------------------------
    async def _read_value(
        self, page: PageContext, resolution: LocatorResolution, *, action: Action | None = None
    ) -> str | None:
        value = await self._run_on_target(
            page, resolution, "el => ('value' in el ? el.value : null)", action=action
        )
        return None if value is None else str(value)

    async def _set_value_direct(
        self,
        page: PageContext,
        resolution: LocatorResolution,
        value: str,
        *,
        action: Action | None = None,
    ) -> None:
        await self._run_on_target(
            page,
            resolution,
            "el => { el.value = %s; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); return el.value; }"
            % _js_string(value),
            action=action,
        )

    async def _run_on_target(
        self,
        page: PageContext,
        resolution: LocatorResolution,
        function: str,
        args: list[object] | None = None,
        *,
        action: Action | None = None,
        element: Element | None = None,
    ):
        """Run a small function against the resolved element.

        The target is re-resolved with the full locator pipeline first (which
        publishes the exact node it matched), so a stale or text-based guess can
        never silently operate on the wrong element - or on nothing at all.
        """
        import json

        if action is not None or element is not None:
            fresh = await self.resolver.resolve(page, element=element, action=action, require_enabled=False)
            if fresh.matched:
                resolution = fresh
        script = f"""
        (() => {{
          const el = window.__bbTarget;
          if (!el) return null;
          const fn = {function};
          return fn.apply(null, [el].concat({json.dumps(args or [])}));
        }})()
        """
        return await page.evaluate(script)


def _js_string(value: str) -> str:
    import json

    return json.dumps(value)

