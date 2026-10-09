"""The BlackBox HTTP API and its served fallback dashboard.

    python -m uvicorn blackbox.api.main:app --port 8099

The API is a thin, honest shell over the agent: it exposes what has been learned
(model, graph, workflows, evidence, metrics), what is happening right now (a
Server-Sent Events stream of live events), and the two things a caller can ask
for (explore, run a task).  Nothing here talks to a website directly.

CORS is permissive because the dashboard's development server runs on another
port; the interface binds to localhost by default when started with uvicorn.
"""

from __future__ import annotations

import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .routes import ROUTERS
from .runtime import API_VERSION, runtime

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the runtime for the lifetime of the server; close browsers on exit."""
    log.info("BlackBox API %s starting (db=%s)", API_VERSION, runtime.settings.database_dsn)
    try:
        yield
    finally:
        log.info("BlackBox API shutting down: closing agents")
        with contextlib.suppress(Exception):
            await runtime.shutdown()


def create_app() -> FastAPI:
    """Build the FastAPI application (routers, CORS, static dashboard)."""
    app = FastAPI(
        title="BlackBox API",
        version=API_VERSION,
        summary="Black-box website behavioral learner: model, graph, evidence and control.",
        description=(
            "Read what BlackBox has learned about a registered target, watch an "
            "exploration live over Server-Sent Events, and start explorations or "
            "natural-language tasks as background jobs."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Type", "Cache-Control"],
    )

    for router in ROUTERS:
        app.include_router(router, prefix="/api")

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    else:  # pragma: no cover - the static directory ships with the package
        log.warning("static dashboard directory is missing: %s", STATIC_DIR)

    @app.get("/", include_in_schema=False)
    def dashboard() -> Response:
        """The served fallback dashboard (plain HTML + CSS + vanilla JS)."""
        if INDEX_FILE.is_file():
            return FileResponse(INDEX_FILE, media_type="text/html")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "the dashboard is not installed",
                "expected": str(INDEX_FILE),
                "api": "/docs",
            },
        )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    return app


app = create_app()
