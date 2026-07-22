# Tests for [FEAT-003] JWT auth middleware
#
# Real Supabase Auth tokens on this project are ES256, verified via JWKS
# (fetched from {SUPABASE_URL}/auth/v1/.well-known/jwks.json), not a shared
# HS256 secret. These tests sign tokens with a locally-generated EC keypair
# and inject a FakeJWKClient that resolves to the matching public key —
# this exercises the exact same decode/claims/error-handling logic the
# middleware runs against a real JWKS response, without a network call or
# a live Supabase project in CI.

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from middleware.auth import JWTAuthMiddleware


class FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJWKClient:
    """Stands in for jwt.PyJWKClient — resolves to a known local EC public
    key instead of fetching a real JWKS endpoint."""

    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return FakeSigningKey(self._public_key)


@pytest.fixture(scope="module")
def keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def other_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def make_token(private_key, sub: str = "user-123", expired: bool = False) -> str:
    now = datetime.now(timezone.utc)
    exp = now - timedelta(minutes=5) if expired else now + timedelta(minutes=5)
    return jwt.encode({"sub": sub, "aud": "authenticated", "exp": exp}, private_key, algorithm="ES256")


def build_app(jwks_client) -> FastAPI:
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware, jwks_client=jwks_client)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/protected")
    async def protected(request: Request):
        return {"user_id": request.state.user_id}

    return app


@pytest.fixture
def client(keypair):
    _, public_key = keypair
    return TestClient(build_app(FakeJWKClient(public_key)))


# Acceptance criterion: Non-`/health` routes reject requests without `Authorization: Bearer <jwt>` with 401
def test_missing_authorization_header_returns_401(client):
    response = client.get("/protected")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_health_stays_unauthenticated(client):
    response = client.get("/health")

    assert response.status_code == 200


# Acceptance criterion: Invalid signature returns 401
def test_invalid_signature_returns_401(client, other_keypair):
    other_private, _ = other_keypair
    # Signed with a different key than the one the fake JWKS client resolves to.
    bad_token = make_token(other_private)

    response = client.get("/protected", headers={"Authorization": f"Bearer {bad_token}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_expired_jwt_returns_401(client, keypair):
    private_key, _ = keypair
    expired_token = make_token(private_key, expired=True)

    response = client.get("/protected", headers={"Authorization": f"Bearer {expired_token}"})

    assert response.status_code == 401


def test_malformed_jwt_returns_401(client):
    response = client.get("/protected", headers={"Authorization": "Bearer not-a-real-jwt"})

    assert response.status_code == 401


# Acceptance criterion: Valid JWT attaches `user_id` to `request.state.user_id`
def test_valid_jwt_attaches_user_id_to_request_state(client, keypair):
    private_key, _ = keypair
    token = make_token(private_key, sub="user-abc-123")

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "user-abc-123"


# Acceptance criterion: JWT verification uses Supabase's JWKS endpoint (derived from SUPABASE_URL, not hardcoded)
def test_jwks_url_derived_from_supabase_url_env_var(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example-project.supabase.co")

    middleware = JWTAuthMiddleware(FastAPI())

    assert middleware.jwks_client.uri == "https://example-project.supabase.co/auth/v1/.well-known/jwks.json"


def test_missing_supabase_url_env_var_raises(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    with pytest.raises(KeyError):
        JWTAuthMiddleware(FastAPI())
