"""Static server for the bundled demo applications.

The demos are plain files; this serves them over HTTP on the ports the demo
targets are registered against.  It is deliberately the dumbest possible server:
no directory traversal outside the app root, no caching surprises, and no
knowledge of BlackBox.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent
DEMO_DIRS = ("crm", "ecommerce", "project_manager", "forms")


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the app root and never caches, so a reload always re-reads files."""

    def end_headers(self) -> None:  # noqa: D102
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if "--quiet" not in sys.argv:
            sys.stderr.write(f"[demo] {self.address_string()} {format % args}\n")


class ReusableServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(app_dir: Path, port: int, host: str = "127.0.0.1") -> ReusableServer:
    handler = functools.partial(Handler, directory=str(app_dir))
    server = ReusableServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, name=f"demo-{app_dir.name}", daemon=True)
    thread.start()
    return server


def serve_all(root: Path | None = None, ports: dict[str, int] | None = None) -> dict[str, ReusableServer]:
    """Start every demo app; returns app name -> server."""
    root = root or DEFAULT_ROOT
    ports = ports or {"crm": 3001, "ecommerce": 3002, "project_manager": 3003, "forms": 3004}
    servers: dict[str, ReusableServer] = {}
    for name in DEMO_DIRS:
        app_dir = root / name
        if not (app_dir / "index.html").exists():
            continue
        servers[name] = serve(app_dir, ports[name])
    return servers


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the BlackBox demo applications")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--app", choices=DEMO_DIRS, help="serve a single app instead of all")
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if args.app:
        server = serve(args.root / args.app, args.port, args.host)
        print(f"serving {args.app} at http://{args.host}:{args.port} (ctrl-c to stop)")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            server.shutdown()
        return 0

    servers = serve_all(args.root)
    for name, server in servers.items():
        print(f"serving {name} at http://127.0.0.1:{server.server_address[1]}")
    print("ctrl-c to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        for server in servers.values():
            server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
