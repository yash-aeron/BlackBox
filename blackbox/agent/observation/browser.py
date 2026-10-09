"""The observation pipeline: one call that yields a fused Observation.

Order matters: wait for the page to settle, then read the DOM, the
accessibility tree and the pixels, then fold in browser-level state.  Every
channel is optional at runtime - if the accessibility tree is unavailable or a
screenshot fails, observation still succeeds and says so in ``channel_health``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from blackbox.browser.context import PageContext
from ..model.base import Timer
from .accessibility import extract_accessibility
from .dom import extract_dom
from .fusion import fuse_observation
from .network_guard import NetworkGuard
from .observation import BrowserState, Observation, Viewport
from .screenshot import capture_screenshot


class PageObserver:
    """Turns a live page into a structured, multi-channel observation."""

    def __init__(
        self,
        artifacts_dir: Path,
        *,
        capture_screenshots: bool = True,
        full_page_screenshots: bool = False,
        network_guard: NetworkGuard | None = None,
        settle_quiet_ms: int = 250,
        settle_timeout: float = 8.0,
    ) -> None:
        self.artifacts_dir = Path(artifacts_dir)
        self.capture_screenshots = capture_screenshots
        self.full_page_screenshots = full_page_screenshots
        self.network_guard = network_guard
        self.settle_quiet_ms = settle_quiet_ms
        self.settle_timeout = settle_timeout
        self.observations: list[Observation] = []

    async def observe(self, page: PageContext, *, settle: bool = True, prefix: str = "obs") -> Observation:
        timer = Timer()
        if settle:
            await self._settle(page)

        dom = await extract_dom(page)
        ax_tree = await extract_accessibility(page)

        screenshot_ref: str | None = None
        screenshot_hash = ""
        screenshot_note = ""
        if self.capture_screenshots:
            shot = await capture_screenshot(
                page, self.artifacts_dir, full_page=self.full_page_screenshots, prefix=prefix
            )
            screenshot_ref = shot.ref
            screenshot_hash = shot.hash
            screenshot_note = shot.note

        viewport = Viewport(**{k: int(v) for k, v in (await page.viewport()).items()})
        browser_state = await self._browser_state(page, dom)
        counts = dict(dom.get("counts") or {})
        counts["ax_nodes"] = ax_tree.get("node_count", 0)

        observation = fuse_observation(
            dom=dom,
            ax_tree=ax_tree,
            screenshot_ref=screenshot_ref,
            screenshot_hash=screenshot_hash,
            browser_state=browser_state,
            viewport=viewport,
            counts=counts,
            captured_ms=timer.elapsed_ms,
            screenshot_note=screenshot_note,
        )
        self.observations.append(observation)
        return observation

    async def _settle(self, page: PageContext) -> None:
        """Dynamic content: wait until the DOM stops changing and loading ends."""
        try:
            await page.wait_for_ready(timeout=self.settle_timeout)
            await page.wait_for_stable(quiet_ms=self.settle_quiet_ms, timeout=self.settle_timeout)
            await self._wait_out_loading(page)
        except Exception:  # noqa: BLE001 - settling is best effort
            pass

    async def _wait_out_loading(self, page: PageContext, *, timeout: float = 5.0) -> None:
        """A visible loading indicator means the state is not settled yet.

        Applications commonly render a spinner, then swap in content a few hundred
        milliseconds later; observing during that window would record a state the
        user never really sees.
        """
        deadline = time.monotonic() + timeout
        probe = r"""
        (() => {
          const busy = document.querySelectorAll('[aria-busy="true"]').length;
          const status = Array.from(document.querySelectorAll('[role="status"], .loading, .spinner'))
            .map((el) => (el.innerText || '').trim())
            .filter((text) => /load|saving|processing|please wait|submitting|deleting/i.test(text));
          return JSON.stringify({ busy: busy, status: status.length });
        })()
        """
        while time.monotonic() < deadline:
            try:
                state = await page.evaluate_json(probe)
            except Exception:  # noqa: BLE001 - probe failure is not fatal
                return
            if not state or (not state.get("busy") and not state.get("status")):
                return
            await asyncio.sleep(0.12)
        try:
            await page.wait_for_stable(quiet_ms=self.settle_quiet_ms, timeout=2.0)
        except Exception:  # noqa: BLE001
            pass

    async def _browser_state(self, page: PageContext, dom: dict[str, Any]) -> BrowserState:
        scroll = await page.evaluate_json(
            "JSON.stringify({x: Math.round(scrollX), y: Math.round(scrollY), h: document.body ? document.body.scrollHeight : 0})"
        ) or {}
        cookie_names: list[str] = []
        try:
            result = await page.connection.send("Network.getAllCookies")
            cookie_names = sorted({c.get("name", "") for c in result.get("cookies", []) if c.get("name")})
        except Exception:  # noqa: BLE001 - cookie visibility is optional
            cookie_names = []

        events = page.events.snapshot()
        return BrowserState(
            url=str(dom.get("url") or await page.url()),
            title=str(dom.get("title") or ""),
            can_go_back=False,
            cookie_names=cookie_names,
            scroll_x=int(scroll.get("x", 0)),
            scroll_y=int(scroll.get("y", 0)),
            document_height=int(scroll.get("h", 0)),
            popups=events.get("popups", []),
            native_dialogs=events.get("dialogs", []),
            downloads=events.get("downloads", []),
            console_errors=events.get("errors", []),
        )

    async def observe_quiet(self, page: PageContext, *, attempts: int = 3, prefix: str = "obs") -> Observation:
        """Observe repeatedly until two consecutive observations agree.

        Used where a single sample could catch a mid-render frame; returns the
        most recent observation if the page never settles.
        """
        previous: Observation | None = None
        for _ in range(max(1, attempts)):
            current = await self.observe(page, settle=True, prefix=prefix)
            if previous is not None:
                if current.screenshot_hash and current.screenshot_hash == previous.screenshot_hash:
                    return current
                if current.visible_text == previous.visible_text and len(current.interactive_elements) == len(
                    previous.interactive_elements
                ):
                    return current
            previous = current
            await page.wait_for_stable(quiet_ms=self.settle_quiet_ms, timeout=self.settle_timeout)
        return previous if previous is not None else await self.observe(page, prefix=prefix)

    def stats(self) -> dict[str, Any]:
        if not self.observations:
            return {"observations": 0}
        durations = [o.captured_ms for o in self.observations]
        return {
            "observations": len(self.observations),
            "mean_capture_ms": round(sum(durations) / len(durations), 2),
            "last_observation": self.observations[-1].observation_id,
            "last_captured_at": time.time(),
        }
