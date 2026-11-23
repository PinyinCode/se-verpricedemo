import app as api


def setup_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(api, "API_KEY", "test-secret")
    return TestClient(api.app)


def test_health(tmp_path, monkeypatch):
    with setup_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["mode"] == "DEMO_ONLY"


def test_signal_lifecycle_is_paper_only(tmp_path, monkeypatch):
    with setup_client(tmp_path, monkeypatch) as client:
        headers = {"X-API-Key": "test-secret"}
        payload = {
            "symbol": "EURUSD", "timeframe": "M15", "direction": "BUY",
            "entry": 1.085, "sl": 1.083, "score": 72, "source": "unit-test"
        }
        created = client.post("/api/signals", json=payload, headers=headers)
        assert created.status_code == 200
        signal_id = created.json()["signal_id"]

        claimed = client.get("/api/mt5/next?consumer=test-ea", headers=headers)
        assert claimed.status_code == 200
        assert claimed.json()["signal"]["id"] == signal_id
        assert claimed.json()["signal"]["status"] == "claimed"

        ack = client.post("/api/mt5/ack", json={
            "signal_id": signal_id, "status": "SIMULATED",
            "message": "paper test"
        }, headers=headers)
        assert ack.status_code == 200

        dashboard = client.get("/api/dashboard").json()
        assert dashboard["counts"]["completed"] == 1
        assert dashboard["signals"][0]["ack_status"] == "SIMULATED"


def test_rejects_wrong_sl_side_and_missing_key(tmp_path, monkeypatch):
    with setup_client(tmp_path, monkeypatch) as client:
        payload = {
            "symbol": "EURUSD", "timeframe": "M15", "direction": "BUY",
            "entry": 1.085, "sl": 1.086
        }
        assert client.post("/api/signals", json=payload).status_code == 401
        response = client.post("/api/signals", json=payload,
                               headers={"X-API-Key": "test-secret"})
        assert response.status_code == 422

def test_legacy_signal_source_is_deduplicated(tmp_path, monkeypatch):
    with setup_client(tmp_path, monkeypatch) as client:
        headers = {"X-API-Key": "test-secret"}
        payload = {
            "symbol": "EURUSD", "timeframe": "M15", "direction": "BUY",
            "entry": 1.085, "sl": 1.083, "score": 70,
            "source": "div-EURUSD-M15-1795000000"
        }
        first = client.post("/api/signals", json=payload, headers=headers).json()
        second = client.post("/api/signals", json=payload, headers=headers).json()
        assert first["signal_id"] == second["signal_id"]
        assert second["duplicate"] is True
        assert len(client.get("/api/dashboard").json()["signals"]) == 1


def test_chart_page_and_candle_feed(tmp_path, monkeypatch):
    with setup_client(tmp_path, monkeypatch) as client:
        headers = {"X-API-Key": "test-secret"}
        page = client.get("/chart")
        assert page.status_code == 200
        assert "Forex AI · Chart" in page.text

        payload = {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "candles": [
                {"time": 1795161600, "open": 1.08, "high": 1.09,
                 "low": 1.07, "close": 1.085, "volume": 120}
            ]
        }
        posted = client.post("/api/mt5/candles", json=payload, headers=headers)
        assert posted.status_code == 200
        assert posted.json()["received"] == 1

        assert client.get("/api/chart/data?symbol=eurusd&timeframe=M15").status_code == 401
        chart = client.get("/api/chart/data?symbol=eurusd&timeframe=M15", headers=headers)
        assert chart.status_code == 200
        data = chart.json()
        assert data["mode"] == "DEMO_ONLY"
        assert len(data["candles"]) == 1
        assert data["candles"][0]["close"] == 1.085


def test_candle_feed_requires_api_key_and_valid_ohlc(tmp_path, monkeypatch):
    with setup_client(tmp_path, monkeypatch) as client:
        payload = {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "candles": [
                {"time": 1795161600, "open": 1.08, "high": 1.07,
                 "low": 1.06, "close": 1.065}
            ]
        }
        assert client.post("/api/mt5/candles", json=payload).status_code == 401
        response = client.post("/api/mt5/candles", json=payload,
                               headers={"X-API-Key": "test-secret"})
        assert response.status_code == 422

def test_settings_read_requires_api_key(tmp_path, monkeypatch):
    with setup_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/settings").status_code == 401
        response = client.get("/api/settings", headers={"X-API-Key": "test-secret"})
        assert response.status_code == 200
        assert "settings" in response.json()

