"""WebSocket stream to the UI.

Every message is tagged with a `type`: `tick` (clock, quotes, account),
`orders`, `watchlists`, and progress messages for fast-forwards and the history
build. A client can send {"type": "subscribe", "symbols": [...]} to receive only
those symbols' quotes in its ticks (until then, it gets every symbol); it gets
a tick with them straight away.
"""

import contextlib
import json

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from paper_trade.market.service import MarketService

router = APIRouter()


@router.websocket("/ws")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    svc: MarketService = ws.app.state.market
    queue = svc.subscribe()
    symbols: set[str] | None = None  # quotes this client wants (None: all)

    def only_subscribed(msg: dict) -> dict:
        if msg["type"] != "tick" or symbols is None:
            return msg
        return {**msg, "quotes": [q for q in msg["quotes"] if q["symbol"] in symbols]}

    try:
        # A task group (not bare asyncio tasks) so that when either side ends, or the
        # server cancels this handler, both halves are cancelled and awaited together.
        async with anyio.create_task_group() as tg:

            async def receive() -> None:
                # Ends when the client goes away, even if no ticks are flowing because
                # the market is paused.
                nonlocal symbols
                with contextlib.suppress(WebSocketDisconnect):
                    while (event := await ws.receive())["type"] != "websocket.disconnect":
                        try:
                            msg = json.loads(event.get("text") or "")
                        except ValueError:
                            continue
                        if isinstance(msg, dict) and msg.get("type") == "subscribe" and isinstance(msg.get("symbols"), list):
                            symbols = {s for s in msg["symbols"] if isinstance(s, str)}
                            # Right away, even while paused (no ticks flowing); during a jump
                            # the final state comes when it ends.
                            if not svc.jumping:
                                svc.send(queue, svc.snapshot())
                tg.cancel_scope.cancel()

            tg.start_soon(receive)
            await ws.send_json(svc.orders_message())
            await ws.send_json(svc.watchlists.message())
            await ws.send_json(only_subscribed(svc.snapshot()))
            while True:
                await ws.send_json(only_subscribed(await queue.get()))
    except* WebSocketDisconnect:
        # The client went away (possibly mid-send, which surfaces through the task group).
        pass
    finally:
        svc.unsubscribe(queue)

