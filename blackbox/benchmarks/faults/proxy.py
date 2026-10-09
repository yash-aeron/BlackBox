"""Fault-injection proxy.

Websites change.  This proxy sits between BlackBox and a demo site and rewrites
what the browser receives, so the agent can be tested against the failures the
specification lists: renamed labels, moved or removed elements, duplicated
controls, delayed rendering, unexpected dialogs, broken navigation and changed
form fields.

The proxy only ever modifies responses *to the browser* - the agent has no idea
it is there, which is the point: it must detect the change from observation and
recover, mark knowledge stale, or stop safely.

Usage:
    python -m blackbox.benchmarks.faults.proxy --upstream http://127.0.0.1:3001 --port 3011 \
        --fault rename_label --fault-arg old="Add customer" --fault-arg new="New record"
"""

from __future__ import annotations

import argparse
import http.server
import json
import re
import socketserver
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

TEXT_CONTENT_TYPES = ("text/html", "text/css", "application/javascript", "text/javascript", "application/json")


@dataclass
class FaultSpec:
    """One fault to apply to a response."""

    kind: str
    args: dict[str, str] = field(default_factory=dict)
    path_contains: str = ""
    only_first: bool = False

    def matches(self, path: str) -> bool:
        return not self.path_contains or self.path_contains in path

    def describe(self) -> str:
        detail = ", ".join(f"{key}={value!r}" for key, value in self.args.items())
        scope = f" path~{self.path_contains!r}" if self.path_contains else ""
        return f"{self.kind}({detail}){scope}"


@dataclass
class FaultApplication:
    kind: str
    detail: str
    path: str


class FaultInjector:
    """Applies response-rewriting faults and records what it changed."""

    def __init__(self, faults: list[FaultSpec]) -> None:
        self.faults = faults
        self.applied: list[FaultApplication] = []
        self.counters: dict[str, int] = {}

    def apply(self, path: str, content_type: str, body: bytes, *, status: int = 200) -> tuple[bytes, int, float]:
        """Return (body, status, extra_delay_seconds) after applying faults."""
        if not body:
            return body, status, 0.0
        is_text = any(content_type.startswith(prefix) for prefix in TEXT_CONTENT_TYPES)
        text = body.decode("utf-8", errors="replace") if is_text else None
        delay = 0.0

        for fault in self.faults:
            if not fault.matches(path):
                continue
            key = f"{fault.kind}:{fault.path_contains}"
            count = self.counters.get(key, 0)
            if fault.only_first and count > 0:
                continue

            if fault.kind == "delay":
                milliseconds = int(fault.args.get("ms", "800"))
                delay += milliseconds / 1000.0
                self._note(fault, path, f"delayed {milliseconds} ms")

            elif fault.kind == "rename_label" and text is not None:
                old = fault.args.get("old", "")
                new = fault.args.get("new", "")
                if old and old in text:
                    occurrences = text.count(old)
                    text = text.replace(old, new)
                    self._note(fault, path, f"renamed {occurrences}x {old!r} -> {new!r}")

            elif fault.kind == "remove_element" and text is not None:
                marker = fault.args.get("marker", "")
                if marker and marker in text:
                    pattern = re.compile(r"<[^>]*" + re.escape(marker) + r"[^>]*>.*?</[^>]+>", re.DOTALL)
                    text, removed = pattern.subn("", text, count=1)
                    if removed:
                        self._note(fault, path, f"removed element containing {marker!r}")

            elif fault.kind == "duplicate_element" and text is not None:
                marker = fault.args.get("marker", "")
                if marker and marker in text:
                    pattern = re.compile(r"<[^>]*" + re.escape(marker) + r"[^>]*>.*?</[^>]+>", re.DOTALL)
                    match = pattern.search(text)
                    if match:
                        text = text[: match.end()] + match.group(0) + text[match.end() :]
                        self._note(fault, path, f"duplicated element containing {marker!r}")

            elif fault.kind == "inject_dialog" and text is not None:
                message = fault.args.get("message", "Session expired. Please sign in again.")
                snippet = (
                    "<dialog id=\"fault-dialog\" open><h2>Attention</h2><p>"
                    + message
                    + "</p><button type=\"button\">Dismiss</button></dialog>"
                )
                text = text.replace("<body", snippet + "<body", 1) if "<body" in text else snippet + text
                self._note(fault, path, "injected an unexpected modal dialog")

            elif fault.kind == "break_navigation" and text is not None:
                target = fault.args.get("target", "#/customers")
                replacement = fault.args.get("replacement", "http://127.0.0.1:3999/elsewhere")
                if target in text:
                    text = text.replace(target, replacement)
                    self._note(fault, path, f"rewrote navigation {target!r} -> external origin")

            elif fault.kind == "rename_field" and text is not None:
                old = fault.args.get("old", "")
                new = fault.args.get("new", "")
                if old and old in text:
                    text = text.replace(old, new)
                    self._note(fault, path, f"renamed field/attribute {old!r} -> {new!r}")

            elif fault.kind == "break_script" and text is not None:
                marker = fault.args.get("marker", "addEventListener")
                if marker in text:
                    text = text.replace(marker, "___disabled___", 1)
                    self._note(fault, path, f"disabled first {marker!r} binding")

            elif fault.kind == "fail_request":
                status = int(fault.args.get("status", "500"))
                body = fault.args.get("body", "injected failure").encode()
                self._note(fault, path, f"returned HTTP {status}")
                return body, status, delay

            self.counters[key] = count + 1

        if text is not None:
            body = text.encode("utf-8")
        return body, status, delay

    def _note(self, fault: FaultSpec, path: str, detail: str) -> None:
        self.applied.append(FaultApplication(kind=fault.kind, detail=detail, path=path))

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for application in self.applied:
            counts[application.kind] = counts.get(application.kind, 0) + 1
        return {"applied": len(self.applied), "by_kind": counts, "recent": [f"{a.kind}: {a.detail}" for a in self.applied[-8:]]}


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Reverse proxy that rewrites responses on the way through."""

    server_version = "BlackBoxFaultProxy/1.0"
    injector: FaultInjector
    upstream: str
    quiet: bool = True

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if not self.quiet:
            sys.stderr.write(f"[fault-proxy] {fmt % args}\n")

    def _proxy(self, method: str) -> None:
        parsed = urlparse(self.path)
        target = urljoin(self.upstream, parsed.path or "/")
        if parsed.query:
            target += f"?{parsed.query}"

        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else None
        headers = {key: value for key, value in self.headers.items() if key.lower() not in ("host", "accept-encoding")}

        request = urllib.request.Request(target, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed upstream
                body = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            body = exc.read()
            status = exc.code
            content_type = exc.headers.get("Content-Type", "")
        except Exception as exc:  # noqa: BLE001 - upstream failure is reported, not hidden
            self.send_error(502, f"upstream error: {exc}")
            return

        if method == "GET":
            body, status, delay = self.injector.apply(parsed.path or "/", content_type, body, status=status)
            if delay:
                time.sleep(delay)

        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._proxy("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._proxy("POST")

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy("HEAD")


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_proxy(upstream: str, port: int, faults: list[FaultSpec], *, quiet: bool = True) -> tuple[ProxyServer, FaultInjector]:
    injector = FaultInjector(faults)
    handler = type(
        "BoundProxyHandler",
        (ProxyHandler,),
        {"injector": injector, "upstream": upstream, "quiet": quiet},
    )
    server = ProxyServer(("127.0.0.1", port), handler)
    import threading

    threading.Thread(target=server.serve_forever, name=f"fault-proxy-{port}", daemon=True).start()
    return server, injector


def parse_fault_argument(raw: str) -> tuple[str, dict[str, str]]:
    if ":" in raw:
        kind, _, rest = raw.partition(":")
    else:
        kind, rest = raw, ""
    args: dict[str, str] = {}
    for pair in rest.split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, _, value = pair.partition("=")
        args[key.strip()] = value.strip()
    return kind, args


def main() -> int:
    parser = argparse.ArgumentParser(description="Fault-injection proxy for BlackBox")
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--port", type=int, default=3011)
    parser.add_argument("--fault", action="append", default=[], help="kind:key=value,key=value")
    parser.add_argument("--path-contains", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    faults: list[FaultSpec] = []
    for raw in args.fault:
        kind, fault_args = parse_fault_argument(raw)
        faults.append(FaultSpec(kind=kind, args=fault_args, path_contains=args.path_contains))

    server, injector = start_proxy(args.upstream, args.port, faults, quiet=not args.verbose)
    print(json.dumps({"proxy": f"http://127.0.0.1:{args.port}", "upstream": args.upstream,
                      "faults": [f.describe() for f in faults]}, indent=2))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(json.dumps(injector.summary(), indent=2))
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
