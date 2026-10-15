from fastapi.testclient import TestClient

from deal_grandpa.api.app import app


def test_health_is_explicitly_fixture_mode():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "FIXTURE"
