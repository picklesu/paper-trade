from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from paper_trade.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


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
        first = ws.receive_json()
        assert first["type"] == "tick"
        assert first["clock"]["speed"] == 1
        assert {q["symbol"] for q in first["quotes"]} >= {"SPY", "AAPL"}
        second = ws.receive_json()
        assert second["clock"]["time"] >= first["clock"]["time"]


def test_websocket_sees_clock_changes(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        client.post("/api/clock", json={"speed": 600})
        for _ in range(5):
            if ws.receive_json()["clock"]["speed"] == 600:
                break
        else:
            pytest.fail("speed change never reached the stream")


def test_websocket_ignores_client_messages(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00")
        ws.send_text("hello")
        assert ws.receive_json()["type"] == "tick"


def test_goto_preview_and_jump(client: TestClient) -> None:
    now = client.get("/api/clock").json()["time"]

    # Naive times are New York wall-clock; Saturday snaps to Monday's open.
    preview = client.get("/api/clock/goto", params={"time": "2030-01-05T12:00"}).json()
    assert preview == {"action": "forward", "target": "2030-01-07T14:30:00+00:00"}

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
