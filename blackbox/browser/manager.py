"""BrowserManager: launch, isolate, observe, recover.

One BrowserManager owns exactly one Chromium process and profile.  It creates
isolated page contexts, applies the permission policy, enforces the origin
sandbox, detects crashes, and can rebuild the browser after a failure without
losing the agent's learned model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .cdp import CDPConnection, CDPError, ChromiumProcess, LaunchOptions, launch_chromium
from .context import BrowserCrashed, PageContext
from .permissions import PermissionPolicy
from .sandbox import Sandbox, origin_of

log = logging.getLogger(__name__)


@dataclass
class BrowserHealth:
    launched_at: float = 0.0
    restarts: int = 0
    crashes: int = 0
    last_error: str | None = None
    pages_created: int = 0
    recording: bool = False


class BrowserManager:
    """Owns the Chromium process and the pages BlackBox observes."""

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        permissions: PermissionPolicy,
        artifacts_dir: Path,
        headless: bool = True,
        recording: bool = False,
        window_size: tuple[int, int] = (1440, 900),
        user_data_dir: Path | None = None,
        executable: Path | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.permissions = permissions
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.recording = recording
        self.window_size = window_size
        self.user_data_dir = user_data_dir
        self.executable = executable
        self.extra_args = extra_args or []
        self.health = BrowserHealth(recording=recording)

        self._process: ChromiumProcess | None = None
        self._connection: CDPConnection | None = None
        self._page: PageContext | None = None
        self._browser_version = ""
        self._profile = user_data_dir
        self._watchdog: asyncio.Task[None] | None = None
        self._closed = False

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> PageContext:
        options = LaunchOptions(
            headless=self.headless and not self.recording,
            executable=self.executable,
            window_size=self.window_size,
            user_data_dir=self.user_data_dir,
            extra_args=self.extra_args,
        )
        self._process = launch_chromium(options)
        self._profile = self._process.profile
        ws_url = await self._process.resolve_ws_url()
        self._connection = CDPConnection(ws_url)
        await self._connection.connect()
        version = await self._connection.send("Browser.getVersion")
        self._browser_version = str(version.get("product", ""))
        self.health.launched_at = time.time()
        self.health.last_error = None
        self._closed = False
        self._watchdog = asyncio.create_task(self._watch(), name="browser-watchdog")
        log.info("browser ready: %s (pid %s)", self._browser_version, self._process.pid)
        return await self.new_page("about:blank")

    @property
    def browser_version(self) -> str:
        return self._browser_version

    @property
    def page(self) -> PageContext:
        if self._page is None:
            raise BrowserCrashed("no page is open")
        return self._page

    @property
    def connection(self) -> CDPConnection:
        if self._connection is None:
            raise BrowserCrashed("browser is not started")
        return self._connection

    def alive(self) -> bool:
        if self._process is None or not self._process.alive() or self._closed:
            return False
        if self._connection is None or self._connection.closed:
            return False
        return True

    async def _watch(self) -> None:
        while not self._closed:
            await asyncio.sleep(0.5)
            if self._process is not None and not self._process.alive():
                log.error("chromium process exited unexpectedly")
                self.health.crashes += 1
                self.health.last_error = f"chromium exited with code {self._process.returncode}"
                return
            if self._connection is not None and self._connection.closed:
                self.health.crashes += 1
                self.health.last_error = self._connection.close_reason or "cdp connection closed"
                return

    # -- pages -------------------------------------------------------------
    async def new_page(self, url: str = "about:blank") -> PageContext:
        if not self.alive():
            raise BrowserCrashed("cannot create a page: browser is not running")
        decision = self.sandbox.check(url, kind="initial_navigation")
        if not decision.allowed:
            raise PermissionError(f"refusing to open {url}: {decision.reason}")

        target = await self.connection.send("Target.createTarget", {"url": "about:blank"})
        attached = await self.connection.send(
            "Target.attachToTarget", {"targetId": target["targetId"], "flatten": True}
        )
        page = PageContext(
            self.connection,
            attached["sessionId"],
            target["targetId"],
            sandbox=self.sandbox,
            download_dir=str(self.permissions.downloads_dir),
        )
        await page.enable()
        await self._configure_page(page, url)
        self.health.pages_created += 1
        self._page = page
        if url and url != "about:blank":
            await page.navigate(url)
        return page

    async def _configure_page(self, page: PageContext, url: str) -> None:
        width, height = self.window_size
        try:
            await page.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
        except CDPError as exc:
            log.debug("viewport override failed: %s", exc)

        origin = origin_of(url)
        if origin.startswith("http"):
            for entry in self.permissions.permission_entries(origin):
                try:
                    await self.connection.send("Browser.setPermission", entry)
                except CDPError as exc:
                    log.debug("permission %s not applied: %s", entry, exc)

        if self.recording:
            await self._start_screencast(page)

    async def _start_screencast(self, page: PageContext) -> None:
        """Headed/recording mode: persist a frame stream for the dashboard."""
        frames_dir = self.artifacts_dir / "screencast" / page.target_id
        frames_dir.mkdir(parents=True, exist_ok=True)
        counter = {"n": 0}

        def on_frame(payload: dict[str, Any]) -> None:
            if payload.get("sessionId") != page.session_id:
                return
            data = payload.get("params", {}).get("data")
            if not data:
                return
            counter["n"] += 1
            if counter["n"] % 10 == 0:  # keep every 10th frame to bound disk usage
                (frames_dir / f"frame-{counter['n']:05d}.jpg").write_bytes(__import__("base64").b64decode(data))

        self.connection.on("Page.screencastFrame", on_frame)
        try:
            await page.send("Page.startScreencast", {"format": "jpeg", "quality": 40, "everyNthFrame": 5})
        except CDPError as exc:
            log.debug("screencast unavailable: %s", exc)

    async def switch_to(self, target_id: str) -> PageContext:
        """Attach to an existing target (used for popups the page opened)."""
        attached = await self.connection.send(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        page = PageContext(
            self.connection,
            attached["sessionId"],
            target_id,
            sandbox=self.sandbox,
            download_dir=str(self.permissions.downloads_dir),
        )
        await page.enable()
        self._page = page
        return page

    # -- recovery ----------------------------------------------------------
    async def restart(self, *, resume_url: str | None = None) -> PageContext:
        """Rebuild the browser after a crash, returning a live page."""
        log.warning("restarting browser (health=%s)", self.health)
        await self.shutdown()
        self.health.restarts += 1
        page = await self.start()
        if resume_url and self.sandbox.is_allowed(resume_url).allowed:
            try:
                await page.navigate(resume_url)
            except Exception as exc:  # noqa: BLE001 - recovery must not raise
                log.warning("could not resume %s: %s", resume_url, exc)
        return page

    # -- session -----------------------------------------------------------
    async def cookie_names(self) -> list[str]:
        try:
            result = await self.connection.send("Network.getAllCookies")
            return sorted({c.get("name", "") for c in result.get("cookies", []) if c.get("name")})
        except (CDPError, BrowserCrashed):
            return []

    async def clear_session(self, origin: str | None = None) -> None:
        """Forget session material. Values are never stored by BlackBox."""
        try:
            await self.connection.send("Network.clearBrowserCookies")
        except (CDPError, BrowserCrashed):
            pass
        if origin:
            try:
                await self.connection.send(
                    "Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"}
                )
            except (CDPError, BrowserCrashed):
                pass

    async def save_session_snapshot(self, path: Path) -> dict[str, Any]:
        """Persist only non-secret session metadata (names, counts, urls)."""
        snapshot = {
            "url": await self.page.url() if self._page else "",
            "cookie_names": await self.cookie_names(),
            "note": "cookie values are deliberately not stored",
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return snapshot

    # -- shutdown ----------------------------------------------------------
    async def shutdown(self) -> None:
        self._closed = True
        if self._watchdog:
            self._watchdog.cancel()
            try:
                await self._watchdog
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._watchdog = None
        if self._page:
            await self._page.close()
            self._page = None
        if self._connection:
            await self._connection.close()
            self._connection = None
        if self._process:
            self._process.terminate()
            self._process = None

    async def close(self) -> None:
        await self.shutdown()

    def health_report(self) -> dict[str, Any]:
        return {
            "alive": self.alive(),
            "browser_version": self._browser_version,
            "headless": self.headless and not self.recording,
            "recording": self.recording,
            "pid": self._process.pid if self._process else None,
            "pages_created": self.health.pages_created,
            "restarts": self.health.restarts,
            "crashes": self.health.crashes,
            "last_error": self.health.last_error,
            "profile": str(self._profile) if self._profile else None,
            "sandbox": self.sandbox.snapshot(),
        }
