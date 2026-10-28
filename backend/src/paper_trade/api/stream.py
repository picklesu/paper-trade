"""WebSocket stream to the UI.

Every message is tagged with a `type`: `tick` (clock, quotes, account),
`orders`, and progress messages for fast-forwards and the history build.
"""

import contextlib

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from paper_trade.market.service import MarketService

router = APIRouter()


@router.websocket("/ws")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    svc: MarketService = ws.app.state.market
    queue = svc.subscribe()
    try:
        # A task group (not bare asyncio tasks) so that when either side ends, or the
        # server cancels this handler, both halves are cancelled and awaited together.
        async with anyio.create_task_group() as tg:

            async def receive() -> None:
                # Ends when the client goes away, even if no ticks are flowing because
                # the market is paused. Anything the client sends is ignored.
                with contextlib.suppress(WebSocketDisconnect):
                    while (await ws.receive())["type"] != "websocket.disconnect":
                        pass
                tg.cancel_scope.cancel()

            tg.start_soon(receive)
            await ws.send_json(svc.orders_message())
            await ws.send_json(svc.snapshot())
            while True:
                await ws.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        svc.unsubscribe(queue)

