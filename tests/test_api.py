from fastapi.testclient import TestClient

from business_graph_api.main import app
from business_graph_core.settings import settings


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_requires_key():
    client = TestClient(app)
    response = client.get("/api/v1/graph/summary")
    assert response.status_code == 401


def test_graph_summary_with_key():
    client = TestClient(app)
    response = client.get("/api/v1/graph/summary", headers={"X-API-Key": settings.api_key})
    assert response.status_code == 200
