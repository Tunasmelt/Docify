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
    assert isinstance(body["commit"], str)


# 2026-08-18 incident follow-up: confirms which real commit a running
# container is executing, without needing SSH or Render's paid-tier-only
# Jobs feature (both confirmed unavailable investigating whether a
# retry-timing fix had genuinely deployed). Render auto-injects
# RENDER_GIT_COMMIT (confirmed against Render's real docs); outside
# Render (local dev, plain docker run) it's simply absent.
def test_health_reports_the_real_deployed_commit_from_render_git_commit_env_var(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "fdfed164a124")
    response = client.get("/health")
    assert response.json()["commit"] == "fdfed164a124"


def test_health_reports_unknown_commit_when_render_git_commit_is_absent(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    response = client.get("/health")
    assert response.json()["commit"] == "unknown"


def test_health_requires_no_auth():
    response = client.get("/health")

    assert response.status_code != 401
    assert response.status_code != 403
