import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from paper_trade.main import create_app


def next_message(ws, kind: str, limit: int = 20) -> dict:
    """The next stream message of `kind`, skipping others (e.g. history-build progress)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    pytest.fail(f"no {kind!r} message in {limit} messages")


def wait_for_history(c: TestClient) -> TestClient:
    """Let the startup history build finish, so tests see a settled market and its
    progress messages don't interleave with what they check."""
    for _ in range(200):
        if not c.get("/api/clock").json()["building_history"]:
            return c
        time.sleep(0.02)
    pytest.fail("history build did not finish")


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    with TestClient(create_app(history_days=5, db_path=tmp_path / "test.db")) as c:
        yield wait_for_history(c)


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["server_time"]


def test_quotes(client: TestClient) -> None:
    quotes = client.get("/api/quotes").json()
    symbols = [q["symbol"] for q in quotes]
    assert "SPY" in symbols
    assert all(q["bid"] < q["ask"] for q in quotes)


def test_clock_speed_and_pause(client: TestClient) -> None:
    clock = client.get("/api/clock").json()
    assert clock["speed"] == 1
    assert clock["paused"] is False
    assert clock["speeds"] == [1, 10, 60, 600]

    clock = client.post("/api/clock", json={"speed": 60, "paused": True}).json()
    assert clock["speed"] == 60
    assert clock["paused"] is True

    assert client.post("/api/clock", json={"speed": 7}).status_code == 422


def test_paused_clock_does_not_advance(client: TestClient) -> None:
    first = client.post("/api/clock", json={"paused": True}).json()["time"]
    assert client.get("/api/clock").json()["time"] == first


def test_websocket_streams_ticks(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "orders"
        lists = ws.receive_json()
        assert lists["type"] == "watchlists" and lists["watchlists"][0]["name"] == "Main"
        first = ws.receive_json()
        assert first["type"] == "tick"
        assert first["clock"]["speed"] == 1
        assert {q["symbol"] for q in first["quotes"]} >= {"SPY", "AAPL"}
        assert first["quotes"][0]["bid_size"] % 100 == 0 and first["quotes"][0]["bid_size"] > 0
        second = next_message(ws, "tick")
        assert second["clock"]["time"] >= first["clock"]["time"]


def test_websocket_subscriptions_send_only_those_quotes(client: TestClient) -> None:
    client.post("/api/clock", json={"paused": True})  # no ticks flowing: the reply comes anyway
    with client.websocket_connect("/ws") as ws:
        assert len(next_message(ws, "tick")["quotes"]) == 40
        ws.send_json({"type": "subscribe", "symbols": ["AAPL", "SPY", "NOPE"]})
        tick = next_message(ws, "tick")
        assert sorted(q["symbol"] for q in tick["quotes"]) == ["AAPL", "SPY"]
        ws.send_text("not json")  # ignored
        ws.send_json({"type": "subscribe", "symbols": []})
        assert next_message(ws, "tick")["quotes"] == []


def test_websocket_sees_clock_changes(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.receive_json()
        client.post("/api/clock", json={"speed": 600})
        for _ in range(5):
            if next_message(ws, "tick")["clock"]["speed"] == 600:
                break
        else:
            pytest.fail("speed change never reached the stream")


def test_websocket_ignores_client_messages(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.receive_json()
        ws.send_bytes(b"\x00")
        ws.send_text("hello")
        assert next_message(ws, "tick")["type"] == "tick"


def test_goto_preview_and_jump(client: TestClient) -> None:
    now = client.get("/api/clock").json()["time"]

    # Naive times are New York wall-clock; Saturday snaps to Monday's open.
    preview = client.get("/api/clock/goto", params={"time": "2030-01-05T12:00"}).json()
    assert preview["action"] == "forward"
    assert preview["target"] == "2030-01-07T14:30:00+00:00"

    body = client.post("/api/clock/goto", json={"time": "2027-03-03T10:00"}).json()
    assert body["action"] == "forward"
    assert body["clock"]["time"] > now

    body = client.post("/api/clock/goto", json={"time": now, "future": "new"}).json()
    assert body["action"] == "rewind"
    assert body["clock"]["time"] == now


def test_goto_before_history_resets(client: TestClient) -> None:
    body = client.post("/api/clock/goto", json={"time": "2020-06-01T09:30"}).json()
    assert body["action"] == "reset"
    assert body["clock"]["origin"] == "2020-06-01T13:30:00+00:00"


def test_goto_rejects_bad_input(client: TestClient) -> None:
    assert client.get("/api/clock/goto", params={"time": "soon"}).status_code == 422
    assert client.get("/api/clock/goto", params={"time": "9999-12-31T17:00"}).status_code == 422
    assert client.post("/api/clock/goto", json={"time": "9999-12-31T17:00"}).status_code == 422
    assert client.post("/api/clock/goto", json={"time": "2027-01-04T10:00", "future": "maybe"}).status_code == 422


def test_clock_changes_rejected_during_a_jump(client: TestClient) -> None:
    client.app.state.market._jumping = True
    assert client.post("/api/clock", json={"speed": 60}).status_code == 409
    client.app.state.market._jumping = False


def test_place_and_cancel_orders(client: TestClient) -> None:
    order = client.post("/api/orders", json={"symbol": "SPY", "side": "buy", "qty": 10, "type": "market"}).json()
    assert order["status"] == "filled"

    working = client.post(
        "/api/orders",
        json={"symbol": "SPY", "side": "sell", "qty": 5, "type": "limit", "limit_price": 9999, "tif": "gtc"},
    ).json()
    assert working["status"] == "working"

    account = client.get("/api/account").json()
    assert account["positions"][0]["symbol"] == "SPY"
    assert account["cash"] == pytest.approx(100_000 - 10 * order["fill_price"], abs=0.01)

    assert client.delete(f"/api/orders/{working['id']}").json()["status"] == "canceled"
    assert [o["status"] for o in client.get("/api/orders").json()] == ["canceled", "filled"]


def test_rejected_order_explains_why(client: TestClient) -> None:
    resp = client.post("/api/orders", json={"symbol": "SPY", "side": "sell", "qty": 1, "type": "market"})
    assert resp.status_code == 422
    assert "Short selling" in resp.json()["detail"]


def test_reset_account(client: TestClient) -> None:
    client.post("/api/orders", json={"symbol": "SPY", "side": "buy", "qty": 1, "type": "market"})
    account = client.post("/api/account/reset", json={"starting_cash": 25_000}).json()
    assert account["cash"] == 25_000 and account["positions"] == []
    assert client.get("/api/orders").json() == []


def test_rewind_preview_lists_trades_to_erase(client: TestClient) -> None:
    client.post("/api/clock", json={"paused": True})
    before = client.get("/api/clock").json()["time"]
    client.post("/api/clock/goto", json={"time": "2027-01-05T10:00"})
    fill = client.post("/api/orders", json={"symbol": "AAPL", "side": "buy", "qty": 3, "type": "market"}).json()

    preview = client.get("/api/clock/goto", params={"time": before}).json()
    assert preview["action"] == "rewind"
    assert [f["id"] for f in preview["erased_fills"]] == [fill["id"]]
    assert preview["cash_then"] == 100_000

    client.post("/api/clock/goto", json={"time": before})
    assert client.get("/api/account").json()["cash"] == 100_000


def test_state_survives_restart(tmp_path) -> None:
    db = tmp_path / "restart.db"
    with TestClient(create_app(history_days=5, db_path=db)) as c:
        c.post("/api/clock", json={"paused": True, "speed": 60})
        c.post("/api/clock/goto", json={"time": "2027-02-01T11:00"})
        c.post("/api/orders", json={"symbol": "MSFT", "side": "buy", "qty": 7, "type": "market"})
        c.post("/api/orders", json={"symbol": "MSFT", "side": "sell", "qty": 7, "type": "limit", "limit_price": 9999, "tif": "gtc"})
        account = c.get("/api/account").json()
        quotes = c.get("/api/quotes").json()
        clock = c.get("/api/clock").json()

    with TestClient(create_app(history_days=5, db_path=db)) as c:
        # The market resumes where it stopped, not at the real-world time.
        volatile = ("timeline", "building_history")  # per-run counters, not saved state
        strip = lambda d: {k: v for k, v in d.items() if k not in volatile}  # noqa: E731
        assert strip(c.get("/api/clock").json()) == strip(clock)
        assert c.get("/api/quotes").json() == quotes
        assert c.get("/api/account").json() == account
        assert len(c.get("/api/orders").json()) == 2


def test_websocket_sends_orders_and_account(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "orders", "orders": []}
        assert next_message(ws, "tick")["account"]["cash"] == 100_000
        client.post("/api/orders", json={"symbol": "SPY", "side": "buy", "qty": 1, "type": "market"})
        for _ in range(10):
            msg = next_message(ws, "orders")
            if msg["orders"]:
                assert msg["orders"][0]["status"] == "filled"
                assert msg["orders"][0]["realized"] is None  # a buy realizes nothing
                break
        else:
            pytest.fail("order update never reached the stream")


def test_orders_carry_the_pnl_a_sell_realized(client: TestClient) -> None:
    client.post("/api/orders", json={"symbol": "SPY", "side": "buy", "qty": 2, "type": "market"})
    sell = client.post("/api/orders", json={"symbol": "SPY", "side": "sell", "qty": 2, "type": "market"}).json()
    assert sell["status"] == "filled"
    # Bought at the ask, sold at the bid a moment later: the spread is lost.
    assert sell["realized"] < 0
    orders = client.get("/api/orders").json()
    assert [o["realized"] for o in orders] == [sell["realized"], None]


def test_trading_blocked_during_a_jump(client: TestClient) -> None:
    client.app.state.market._jumping = True
    resp = client.post("/api/orders", json={"symbol": "SPY", "side": "buy", "qty": 1, "type": "market"})
    assert resp.status_code == 409
    client.app.state.market._jumping = False


def test_goto_preview_refused_during_a_jump(client: TestClient) -> None:
    client.app.state.market._jumping = True
    assert client.get("/api/clock/goto", params={"time": "2026-01-05T10:00"}).status_code == 409
    client.app.state.market._jumping = False


def test_pause_saves_the_current_market_time(tmp_path) -> None:
    import time

    from paper_trade.store import Store

    db = tmp_path / "pause.db"
    with TestClient(create_app(history_days=5, db_path=db)) as c:
        c.post("/api/clock", json={"speed": 600})
        time.sleep(0.3)  # about 3 market-minutes; the engine may lag the clock by a tick
        c.post("/api/clock", json={"paused": True})
        time.sleep(0.6)  # let a tick run while paused
        shown = c.get("/api/clock").json()["time"]
        saved = Store(db).get("market")["time"]  # read the file, as a restart would
    assert saved == shown


def test_rejects_infinite_prices(client: TestClient) -> None:
    resp = client.post(
        "/api/orders",
        content='{"symbol": "SPY", "side": "buy", "qty": 1, "type": "limit", "limit_price": Infinity}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert client.get("/api/orders").json() == []


def test_preview_and_replace_endpoints(client: TestClient) -> None:
    body = {"symbol": "SPY", "side": "buy", "qty": 2, "type": "limit", "limit_price": 100, "tif": "gtc"}
    preview = client.post("/api/orders/preview", json=body).json()
    assert preview["fills_now"] is False and preview["estimated_total"] == 200
    assert client.get("/api/orders").json() == []

    order = client.post("/api/orders", json=body).json()
    new = client.patch(f"/api/orders/{order['id']}", json={"limit_price": 101}).json()
    assert new["replaces"] == order["id"] and new["limit_price"] == 101
    statuses = {o["id"]: o["status"] for o in client.get("/api/orders").json()}
    assert statuses == {order["id"]: "canceled", new["id"]: "working"}

    resp = client.patch(f"/api/orders/{order['id']}", json={"qty": 3})
    assert resp.status_code == 422 and "already canceled" in resp.json()["detail"]
    resp = client.post("/api/orders/preview", json={**body, "side": "sell"})
    assert resp.status_code == 422 and "Short selling" in resp.json()["detail"]


def test_real_prices_status_and_sync(client: TestClient) -> None:
    from datetime import date

    status = client.get("/api/real-prices").json()
    assert status["configured"] is False and status["available"] == 0 and status["total"] == 40
    assert client.post("/api/real-prices/sync").status_code == 422  # nothing fetched yet

    svc = client.app.state.market
    client.portal.call(svc.real.record, {"SPY": (772.31, date(2026, 10, 28))}, ["SPY"])  # on the app's thread
    resp = client.post("/api/real-prices/sync")
    assert resp.status_code == 200 and resp.json()["available"] == 1
    assert client.get("/api/quotes").json()[0]["last"] == 772.31


def test_watchlist_endpoints_and_stream(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        assert next_message(ws, "watchlists")["watchlists"][0]["name"] == "Main"
        created = client.post("/api/watchlists", json={"name": "Energy", "symbols": ["XOM", "CVX"]})
        assert created.status_code == 200 and created.json()["symbols"] == ["XOM", "CVX"]
        list_id = created.json()["id"]
        assert [w["name"] for w in next_message(ws, "watchlists")["watchlists"]] == ["Main", "All symbols", "Energy"]

        resp = client.patch(f"/api/watchlists/{list_id}", json={"symbols": ["CVX", "XOM", "COP"]})
        assert resp.json()["symbols"] == ["CVX", "XOM", "COP"]
        assert client.patch(f"/api/watchlists/{list_id}", json={"symbols": ["NOPE"]}).status_code == 422
        assert client.patch("/api/watchlists/99", json={"name": "x"}).status_code == 404
        assert client.delete(f"/api/watchlists/{list_id}").status_code == 200
        assert [w["name"] for w in client.get("/api/watchlists").json()] == ["Main", "All symbols"]
        client.delete("/api/watchlists/2")
        resp = client.delete("/api/watchlists/1")
        assert resp.status_code == 422 and "last watchlist" in resp.json()["detail"]


def test_symbols_endpoint(client: TestClient) -> None:
    symbols = client.get("/api/symbols").json()
    assert len(symbols) == 40 and symbols[0] == {"symbol": "SPY", "name": "S&P 500 ETF", "sector": None}


def test_option_endpoints_and_streamed_chain(client: TestClient) -> None:
    expirations = client.get("/api/options/expirations", params={"symbol": "spy"}).json()
    assert expirations[0]["days"] == 0 and {"daily", "monthly", "leaps"} <= {e["kind"] for e in expirations}
    assert client.get("/api/options/expirations", params={"symbol": "NOPE"}).status_code == 404
    exp = expirations[2]["date"]
    chain = client.get("/api/options/chain", params={"symbol": "SPY", "expiration": exp}).json()
    assert chain["expiration"] == exp and len(chain["strikes"]) == len(chain["call"]["bid"]) == len(chain["put"]["delta"])
    assert client.get("/api/options/chain", params={"symbol": "SPY", "expiration": "2026-01-17"}).status_code == 422

    client.post("/api/clock", json={"paused": True})
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "subscribe", "symbols": ["SPY"], "chain": {"symbol": "SPY", "expiration": exp}})
        tick = next_message(ws, "tick")
        while "chain" not in tick:
            tick = next_message(ws, "tick")
        assert tick["chain"]["strikes"] == chain["strikes"] and [q["symbol"] for q in tick["quotes"]] == ["SPY"]
        ws.send_json({"type": "subscribe", "symbols": ["SPY"], "chain": {"symbol": "SPY", "expiration": "2026-01-17"}})
        tick = next_message(ws, "tick")
        while tick.get("chain", "missing") == chain:
            tick = next_message(ws, "tick")
        assert tick["chain"] is None  # no such expiration


def test_trade_and_exercise_options(client: TestClient) -> None:
    client.post("/api/clock", json={"paused": True})
    expiry = client.get("/api/options/expirations?symbol=AAPL").json()[2]["date"]
    chain = client.get(f"/api/options/chain?symbol=AAPL&expiration={expiry}").json()
    i = min(range(len(chain["strikes"])), key=lambda j: abs(chain["strikes"][j] - chain["spot"] * 0.9))
    symbol = f"AAPL{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}C{round(chain['strikes'][i] * 1000):08d}"
    preview = client.post("/api/orders/preview", json={"symbol": symbol, "side": "buy", "qty": 1, "type": "market"}).json()
    assert preview["option"]["right"] == "call" and preview["effect"] == "open" and preview["max_loss"] > 0
    order = client.post("/api/orders", json={"symbol": symbol, "side": "buy", "qty": 1, "type": "market"}).json()
    assert order["status"] == "filled" and order["option"]["strike"] == chain["strikes"][i]
    row = client.get("/api/account").json()["positions"][0]
    assert row["option"]["label"].startswith("AAPL ") and row["qty"] == 1
    ex = client.post("/api/options/exercise/preview", json={"symbol": symbol, "qty": 1}).json()
    assert ex["shares"] == 100 and ex["warnings"]  # selling would get more than exercising
    records = client.post("/api/options/exercise", json={"symbol": symbol, "qty": 1}).json()
    assert [r["type"] for r in records] == ["exercise", "exercise"]
    assert client.post("/api/options/exercise", json={"symbol": symbol, "qty": 1}).status_code == 422
    held = {p["symbol"]: p["qty"] for p in client.get("/api/account").json()["positions"]}
    assert held == {"AAPL": 100}
