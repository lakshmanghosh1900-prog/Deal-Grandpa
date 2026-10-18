from fastapi.testclient import TestClient
from datetime import datetime, timezone
import importlib

from deal_grandpa.adapters.sqlite import SQLiteStore
from deal_grandpa.api.app import app
from deal_grandpa.orchestrator import DealGrandpaService

app_module = importlib.import_module("deal_grandpa.api.app")


def test_health_is_explicitly_fixture_mode():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "FIXTURE"


def test_evidence_endpoint_exposes_cost_breakdown(tmp_path, monkeypatch):
    fixed = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    store = SQLiteStore(tmp_path / "api.sqlite3")
    service = DealGrandpaService(store, "tests/fixtures/golden_offers.json", clock=lambda: fixed)
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "service", service)
    client = TestClient(app)
    response = client.post("/api/runs", json={"query": "I need a phone under 30000 with 8GB RAM 128GB storage"})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    card = response.json()["recommendation"]
    assert {"item_price_minor", "shipping_cost_minor", "verified_discount_minor", "known_fees_minor", "effective_total_minor", "fees_status"} <= card.keys()
    evidence = client.get(f"/api/runs/{run_id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()["recommendation"]["effective_total_minor"] == card["effective_total_minor"]


def test_fault_injection_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DEAL_GRANDPA_ENABLE_FAULT_INJECTION", raising=False)
    monkeypatch.setattr(app_module.service, "fault_mode", None)
    client = TestClient(app)

    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json() == {"mode": "FIXTURE", "fault_injection_enabled": False}

    response = client.post("/api/fault-injection", json={"mode": "timeout_once"})
    assert response.status_code == 404
    assert app_module.service.fault_mode is None


def test_fault_injection_requires_explicit_local_enable(monkeypatch):
    monkeypatch.setenv("DEAL_GRANDPA_ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setattr(app_module.service, "fault_mode", None)
    client = TestClient(app)

    assert client.get("/api/config").json()["fault_injection_enabled"] is True
    response = client.post("/api/fault-injection", json={"mode": "timeout_once"})
    assert response.status_code == 200
    assert response.json()["fault"] == "timeout_once"

    clear = client.post("/api/fault-injection", json={"mode": "clear"})
    assert clear.status_code == 200
    assert clear.json()["fault"] == "clear"
