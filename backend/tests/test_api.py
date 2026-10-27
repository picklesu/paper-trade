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
