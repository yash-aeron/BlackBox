"""Server-Sent Events: the live view of what the agent is doing.

``GET /api/targets/{target_id}/stream`` is an ``text/event-stream`` response fed
by the runtime's per-target event buffer.  A browser consumes it with
``new EventSource('/api/targets/demo_crm/stream')`` and receives one JSON object
per event; a 15s comment keeps proxies and idle tabs from dropping the socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..runtime import runtime
from ..schemas import EventOut

log = logging.getLogger(__name__)
router = APIRouter(tags=["stream"])

KEEP_ALIVE_SECONDS = 15.0
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _frame(event: dict[str, Any]) -> str:
    payload = EventOut.from_event(event).model_dump()
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/targets/{target_id}/stream")
async def stream_events(
    target_id: str, request: Request, replay: int = 0, kinds: str | None = None
) -> StreamingResponse:
    """Live events for one target as Server-Sent Events."""
    if not runtime.is_known(target_id):
        raise HTTPException(status_code=404, detail=f"target {target_id!r} is not registered")

    wanted = {item.strip() for item in kinds.split(",") if item.strip()} if kinds else set()
    slot = runtime.slot(target_id)
    queue, backlog = slot.subscribe(replay=max(0, min(replay, 200)))

    async def event_source() -> AsyncIterator[str]:
        try:
            yield "retry: 3000\n\n"
            yield f": connected to {target_id}\n\n"
            for event in backlog:
                if not wanted or str(event.get("type")) in wanted:
                    yield _frame(event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEP_ALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if wanted and str(event.get("type")) not in wanted:
                    continue
                yield _frame(event)
        except asyncio.CancelledError:  # client went away
            raise
        except Exception:  # noqa: BLE001 - a broken socket must not log a traceback per event
            log.debug("event stream for %s ended", target_id, exc_info=True)
        finally:
            slot.unsubscribe(queue)

    return StreamingResponse(event_source(), media_type="text/event-stream", headers=SSE_HEADERS)
