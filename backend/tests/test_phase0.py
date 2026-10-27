from fastapi.testclient import TestClient

from paper_trade.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["server_time"]


def test_websocket_heartbeat() -> None:
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "heartbeat"
        assert first["seq"] == 0
        second = ws.receive_json()
        assert second["seq"] == 1
