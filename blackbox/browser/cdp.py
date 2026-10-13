"""Minimal, dependency-light Chrome DevTools Protocol client.

BlackBox's interaction boundary is the browser.  Chromium is launched with a
remote debugging port and driven over a WebSocket using CDP.  Driving CDP
directly keeps the execution layer explicit and auditable, and it means the
system runs even where the ``playwright`` package cannot be installed.

``browser/manager.py`` exposes this behind ``BrowserDriver`` so a
Playwright-backed implementation can be dropped in without touching the agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger(__name__)

Event = dict[str, Any]
EventHandler = Callable[[Event], Awaitable[None] | None]


class CDPError(RuntimeError):
    """A CDP command returned an error object."""

    def __init__(self, method: str, payload: dict[str, Any]) -> None:
        super().__init__(f"{method}: {payload.get('message', payload)}")
        self.method = method
        self.payload = payload


class CDPClosed(RuntimeError):
    """The CDP websocket or the underlying browser went away."""


def find_chromium() -> Path:
    """Locate a Chromium/Chrome executable.

    Order: ``BLACKBOX_CHROMIUM`` env override, the Playwright browser cache,
    then the usual system install locations.
    """
    override = os.environ.get("BLACKBOX_CHROMIUM")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))

    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if cache.is_dir():
        # Prefer the full browser over the headless shell: it supports headed
        # mode, screenshots and the accessibility domain identically.
        for pattern in ("chromium-*/chrome-win64/chrome.exe", "chromium-*/chrome-linux/chrome"):
            candidates.extend(sorted(cache.glob(pattern), reverse=True))
        for pattern in ("chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",):
            candidates.extend(sorted(cache.glob(pattern), reverse=True))

    candidates.extend(
        [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/google-chrome"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "No Chromium/Chrome executable found. Set BLACKBOX_CHROMIUM to the browser path."
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class LaunchOptions:
    headless: bool = True
    executable: Path | None = None
    window_size: tuple[int, int] = (1440, 900)
    user_data_dir: Path | None = None
    extra_args: list[str] = field(default_factory=list)
    startup_timeout: float = 30.0
    env: dict[str, str] = field(default_factory=dict)


class CDPConnection:
    """A single websocket carrying CDP traffic, with per-session routing."""

    def __init__(self, ws_url: str, *, timeout: float = 30.0) -> None:
        self.ws_url = ws_url
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._handlers: dict[str, list[EventHandler]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._close_reason: str = ""

    # -- lifecycle ---------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def close_reason(self) -> str:
        return self._close_reason

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        # max_msg_size=0 disables the frame limit: CDP payloads (screenshots,
        # accessibility trees) routinely exceed the 4 MiB default.
        self._ws = await self._session.ws_connect(self.ws_url, max_msg_size=0, heartbeat=30.0)
        self._reader = asyncio.create_task(self._read_loop(), name="cdp-reader")

    async def close(self) -> None:
        self._closed.set()
        if self._reader:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown path
                pass
            self._reader = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None
        self._fail_pending(CDPClosed("connection closed"))

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    # -- events ------------------------------------------------------------
    def on(self, method: str, handler: EventHandler) -> None:
        self._handlers.setdefault(method, []).append(handler)

    def off(self, method: str, handler: EventHandler) -> None:
        if method in self._handlers and handler in self._handlers[method]:
            self._handlers[method].remove(handler)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                        break
                    if message.type is aiohttp.WSMsgType.ERROR:
                        break
                    continue
                try:
                    payload = json.loads(message.data)
                except json.JSONDecodeError:
                    log.warning("cdp: dropping non-JSON frame")
                    continue
                self._dispatch(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reader must never die silently
            self._close_reason = str(exc)
            log.debug("cdp reader stopped: %s", exc)
        finally:
            self._closed.set()
            self._fail_pending(CDPClosed(self._close_reason or "browser connection lost"))

    def _dispatch(self, payload: Event) -> None:
        if "id" in payload:
            future = self._pending.pop(int(payload["id"]), None)
            if future and not future.done():
                if "error" in payload:
                    future.set_exception(CDPError(str(payload.get("method", "?")), payload["error"]))
                else:
                    future.set_result(payload.get("result", {}))
            return
        method = payload.get("method")
        if not method:
            return
        for handler in list(self._handlers.get(method, ())):
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 - a bad handler must not kill the reader
                log.exception("cdp handler failed for %s", method)

    # -- commands ----------------------------------------------------------
    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._ws is None or self._ws.closed:
            raise CDPClosed(self._close_reason or "not connected")
        message_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"id": message_id, "method": method}
        if params:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._ws.send_str(json.dumps(message))
        try:
            return await asyncio.wait_for(future, timeout or self.timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise CDPError(method, {"message": f"timed out after {timeout or self.timeout}s"}) from exc


class ChromiumProcess:
    """A launched Chromium with a CDP endpoint."""

    def __init__(self, process: subprocess.Popen[bytes], port: int, profile: Path, executable: Path):
        self.process = process
        self.port = port
        self.profile = profile
        self.executable = executable
        self.browser_ws_url = ""

    @property
    def pid(self) -> int:
        return self.process.pid

    def alive(self) -> bool:
        return self.process.poll() is None

    @property
    def version(self) -> str:
        return self.browser_ws_url

    async def resolve_ws_url(self, timeout: float = 30.0) -> str:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        async with aiohttp.ClientSession() as session:
            while time.monotonic() < deadline:
                if not self.alive():
                    raise CDPClosed(f"chromium exited early with code {self.process.returncode}")
                try:
                    async with session.get(f"http://127.0.0.1:{self.port}/json/version") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            self.browser_ws_url = data["webSocketDebuggerUrl"]
                            return self.browser_ws_url
                except Exception as exc:  # noqa: BLE001 - browser still starting
                    last = exc
                await asyncio.sleep(0.15)
        raise CDPClosed(f"chromium devtools endpoint never came up ({last})")

    def terminate(self, *, grace: float = 5.0) -> None:
        if self.process.poll() is not None:
            return
        with contextlib_suppress():
            self.process.terminate()
        try:
            self.process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            with contextlib_suppress():
                self.process.kill()
            with contextlib_suppress():
                self.process.wait(timeout=grace)

    def kill(self) -> None:
        with contextlib_suppress():
            self.process.kill()
        with contextlib_suppress():
            self.process.wait(timeout=5)


class contextlib_suppress:  # noqa: N801 - tiny local helper, avoids importing contextlib twice
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True


def launch_chromium(options: LaunchOptions | None = None) -> ChromiumProcess:
    """Start Chromium with a fresh profile and a CDP endpoint."""
    options = options or LaunchOptions()
    executable = options.executable or find_chromium()
    port = free_port()
    profile = options.user_data_dir or Path(tempfile.mkdtemp(prefix="blackbox-chrome-"))
    profile.mkdir(parents=True, exist_ok=True)

    args = [
        str(executable),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=Translate,BackForwardCache,OptimizationHints,MediaRouter",
        "--disable-sync",
        "--disable-extensions",
        "--metrics-recording-only",
        "--mute-audio",
        "--window-size=%d,%d" % options.window_size,
    ]
    if options.headless:
        args.append("--headless=new")
    args.extend(options.extra_args)

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    env = os.environ.copy()
    env.update(options.env)
    process = subprocess.Popen(  # noqa: S603 - executable is resolved above
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        env=env,
    )
    return ChromiumProcess(process, port, profile, executable)
