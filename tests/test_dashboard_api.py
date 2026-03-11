from fastapi.testclient import TestClient

from nepali_corpus.core.services.dashboard.app import app, _log_buffer


def test_dashboard_status_and_logs():
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "running" in data

    log_resp = client.get("/api/logs")
    assert log_resp.status_code == 200
    assert "lines" in log_resp.json()


def test_dashboard_websockets():
    client = TestClient(app)
    _log_buffer.append("dashboard ws test")

    with client.websocket_connect("/ws/logs") as ws:
        msg = ws.receive_text()
        assert "dashboard ws test" in msg

    with client.websocket_connect("/ws/stats") as ws:
        payload = ws.receive_json()
        assert "running" in payload
