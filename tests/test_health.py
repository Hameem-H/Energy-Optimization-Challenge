from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_invalid_request_returns_400():
    # Invalid JSON body missing required fields
    response = client.post("/optimize-energy", json={"scenario_id": "test"})
    assert response.status_code == 400
    assert "error" in response.json()
