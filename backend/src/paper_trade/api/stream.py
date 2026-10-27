"""WebSocket stream to the UI.

Phase 0 only sends heartbeats. Later phases push quotes, fills and account
updates over this same connection, each message tagged with a `type`.
"""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

HEARTBEAT_SECONDS = 1.0


@router.websocket("/ws")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    seq = 0
    try:
        while True:
            await ws.send_json(
                {
                    "type": "heartbeat",
                    "seq": seq,
                    "server_time": datetime.now(UTC).isoformat(),
                }
            )
            seq += 1
            await asyncio.sleep(HEARTBEAT_SECONDS)
    except WebSocketDisconnect:
        pass
