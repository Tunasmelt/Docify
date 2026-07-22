# Tests for [FEAT-003] JWT auth middleware

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from middleware.auth import JWTAuthMiddleware

TEST_SECRET = "unit-test-only-secret-padded-to-32-bytes-min"


def make_token(secret: str = TEST_SECRET, sub: str = "user-123", expired: bool = False) -> str:
    now = datetime.now(timezone.utc)
    exp = now - timedelta(minutes=5) if expired else now + timedelta(minutes=5)
    return jwt.encode({"sub": sub, "aud": "authenticated", "exp": exp}, secret, algorithm="HS256")


def build_app(jwt_secret: str | None = TEST_SECRET) -> FastAPI:
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware, jwt_secret=jwt_secret)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/protected")
    async def protected(request: Request):
        return {"user_id": request.state.user_id}

    return app


@pytest.fixture
def client():
    return TestClient(build_app())


# Acceptance criterion: Non-`/health` routes reject requests without `Authorization: Bearer <jwt>` with 401
def test_missing_authorization_header_returns_401(client):
    response = client.get("/protected")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_health_stays_unauthenticated(client):
    response = client.get("/health")

    assert response.status_code == 200


# Acceptance criterion: Invalid signature returns 401
def test_invalid_signature_returns_401(client):
    bad_token = make_token(secret="a-completely-different-32-byte-secret")

    response = client.get("/protected", headers={"Authorization": f"Bearer {bad_token}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_expired_jwt_returns_401(client):
    expired_token = make_token(expired=True)

    response = client.get("/protected", headers={"Authorization": f"Bearer {expired_token}"})

    assert response.status_code == 401


def test_malformed_jwt_returns_401(client):
    response = client.get("/protected", headers={"Authorization": "Bearer not-a-real-jwt"})

    assert response.status_code == 401


# Acceptance criterion: Valid JWT attaches `user_id` to `request.state.user_id`
def test_valid_jwt_attaches_user_id_to_request_state(client):
    token = make_token(sub="user-abc-123")

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "user-abc-123"


# Acceptance criterion: JWT verification uses `SUPABASE_JWT_SECRET`
def test_jwt_verification_uses_supabase_jwt_secret_env_var(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "env-provided-secret-padded-to-32-bytes")
    # jwt_secret=None -> middleware must fall back to reading SUPABASE_JWT_SECRET from env,
    # not a hardcoded value.
    app = build_app(jwt_secret=None)
    client = TestClient(app)
    token = make_token(secret="env-provided-secret-padded-to-32-bytes")

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "user-123"


def test_jwt_verification_env_var_missing_raises(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    app = build_app(jwt_secret=None)
    client = TestClient(app)

    # Starlette builds the middleware stack lazily on first request, so the
    # missing-env-var KeyError only surfaces here, not at add_middleware() time.
    with pytest.raises(KeyError):
        client.get("/protected")
