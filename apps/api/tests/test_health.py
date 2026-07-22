from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_returns_200_with_expected_shape():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert isinstance(body["timestamp"], str)


def test_health_requires_no_auth():
    response = client.get("/health")

    assert response.status_code != 401
    assert response.status_code != 403
