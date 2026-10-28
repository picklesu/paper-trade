"""WebSocket stream to the UI.

Every message is tagged with a `type`. Phase 1 sends `tick` messages carrying
the sim clock and all quotes. Later phases add fills and account updates.
"""

import asyncio

from fastapi import APIRouter, WebSocket

from paper_trade.market.service import MarketService

router = APIRouter()


@router.websocket("/ws")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    svc: MarketService = ws.app.state.market
    queue = svc.subscribe()

    async def send() -> None:
        await ws.send_json(svc.orders_message())
        await ws.send_json(svc.snapshot())
        while True:
            await ws.send_json(await queue.get())

    async def receive() -> None:
        # Returns when the client goes away, even if no ticks are flowing
        # because the market is paused. Anything the client sends is ignored.
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass

    tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        svc.unsubscribe(queue)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
