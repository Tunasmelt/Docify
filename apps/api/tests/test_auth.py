# Tests for [FEAT-003] JWT auth middleware
#
# Real Supabase Auth tokens on this project are ES256, verified via JWKS
# (fetched from {SUPABASE_URL}/auth/v1/.well-known/jwks.json), not a shared
# HS256 secret. These tests sign tokens with a locally-generated EC keypair
# and inject a FakeJWKClient that resolves keys by kid from a local dict —
# this exercises the exact same decode/claims/error-handling logic the
# middleware runs against a real JWKS response, including fail-closed
# behavior on an unknown kid, without a network call or a live project.
#
# Extended 2026-07-22 per Codex review (.agent/GAPS.md) — added coverage
# for missing exp, wrong/missing iss, wrong/missing aud, unknown kid, and
# an explicit algorithm-confusion attempt. See that review for context.

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from middleware.auth import GENERIC_UNAUTHORIZED_MESSAGE, JWTAuthMiddleware

ISSUER = "https://test-project.supabase.co/auth/v1"
AUDIENCE = "authenticated"
KID = "test-key-1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJWKClient:
    """Stands in for jwt.PyJWKClient — resolves keys by `kid` from a local
    dict instead of fetching a real JWKS endpoint, so unknown-kid handling
    is actually exercised rather than assumed from PyJWT's own source."""

    def __init__(self, keys_by_kid: dict):
        self._keys_by_kid = keys_by_kid

    def get_signing_key_from_jwt(self, token):
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in self._keys_by_kid:
            raise jwt.PyJWKClientError(f"Unable to find a signing key that matches: {kid!r}")
        return FakeSigningKey(self._keys_by_kid[kid])


@pytest.fixture(scope="module")
def keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def other_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def make_token(
    private_key,
    sub: str = "user-123",
    expired: bool = False,
    include_exp: bool = True,
    iss: str | None = ISSUER,
    aud: str | None = AUDIENCE,
    kid: str = KID,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": sub}
    if iss is not None:
        payload["iss"] = iss
    if aud is not None:
        payload["aud"] = aud
    if include_exp:
        payload["exp"] = now - timedelta(minutes=5) if expired else now + timedelta(minutes=5)
    return jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": kid})


def build_app(jwks_client) -> FastAPI:
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware, jwks_client=jwks_client, issuer=ISSUER, audience=AUDIENCE)

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
    return TestClient(build_app(FakeJWKClient({KID: public_key})))


def assert_generic_401(response):
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["message"] == GENERIC_UNAUTHORIZED_MESSAGE


# Acceptance criterion: Non-`/health` routes reject requests without `Authorization: Bearer <jwt>` with 401
def test_missing_authorization_header_returns_401(client):
    response = client.get("/protected")

    assert_generic_401(response)


def test_health_stays_unauthenticated(client):
    response = client.get("/health")

    assert response.status_code == 200


# Acceptance criterion: Invalid signature returns 401
def test_invalid_signature_returns_401(client, other_keypair):
    other_private, _ = other_keypair
    # kid matches (resolves to the real public key via the fake client), but
    # actually signed with a different private key -> signature check fails.
    bad_token = make_token(other_private)

    response = client.get("/protected", headers={"Authorization": f"Bearer {bad_token}"})

    assert_generic_401(response)


def test_expired_jwt_returns_401(client, keypair):
    private_key, _ = keypair
    expired_token = make_token(private_key, expired=True)

    response = client.get("/protected", headers={"Authorization": f"Bearer {expired_token}"})

    assert_generic_401(response)


def test_malformed_jwt_returns_401(client):
    response = client.get("/protected", headers={"Authorization": "Bearer not-a-real-jwt"})

    assert_generic_401(response)


# Acceptance criterion: Valid JWT attaches `user_id` to `request.state.user_id`
def test_valid_jwt_attaches_user_id_to_request_state(client, keypair):
    private_key, _ = keypair
    token = make_token(private_key, sub="user-abc-123")

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "user-abc-123"


# --- Codex review follow-ups -------------------------------------------------


def test_missing_exp_claim_returns_401(client, keypair):
    private_key, _ = keypair
    token = make_token(private_key, include_exp=False)

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert_generic_401(response)


def test_wrong_issuer_returns_401(client, keypair):
    private_key, _ = keypair
    token = make_token(private_key, iss="https://attacker-project.supabase.co/auth/v1")

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert_generic_401(response)


def test_missing_issuer_returns_401(client, keypair):
    private_key, _ = keypair
    token = make_token(private_key, iss=None)

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert_generic_401(response)


def test_wrong_audience_returns_401(client, keypair):
    private_key, _ = keypair
    token = make_token(private_key, aud="some-other-audience")

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert_generic_401(response)


def test_missing_audience_returns_401(client, keypair):
    private_key, _ = keypair
    token = make_token(private_key, aud=None)

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert_generic_401(response)


def test_unknown_kid_returns_401(client, keypair):
    private_key, _ = keypair
    # Correctly signed and all claims valid, but the kid isn't one the (fake)
    # JWKS client knows about -> must fail closed, not fall through.
    token = make_token(private_key, kid="some-other-kid-not-in-jwks")

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert_generic_401(response)


def test_algorithm_confusion_hs256_with_ec_public_key_rejected(client, keypair):
    # Classic ES256 -> HS256 downgrade attack: sign with the EC public key's
    # PEM bytes as if it were an HMAC shared secret. Anyone can derive this
    # "secret" from the public JWKS response, so if the server ever trusted
    # the token's own declared alg, this would forge a valid token. The
    # middleware hardcodes algorithms=["ES256"], so this must be rejected
    # regardless of what key material was used to sign it.
    #
    # PyJWT's own jwt.encode() refuses to sign HS256 with a PEM-shaped key
    # (it detects the asymmetric key format and raises InvalidKeyError) —
    # that's a real, useful guard rail, but it means we can't use jwt.encode
    # to build this attack payload. A real attacker wouldn't use PyJWT to
    # forge it either, so the token is built by hand here: raw HMAC-SHA256
    # over the base64url header+payload, using the EC public key's PEM
    # bytes directly as the HMAC key.
    _, public_key = keypair
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    now = datetime.now(timezone.utc)
    header = {"alg": "HS256", "typ": "JWT", "kid": KID}
    payload = {
        "sub": "attacker",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    forged_token = f"{header_b64}.{payload_b64}.{_b64url(signature)}"

    response = client.get("/protected", headers={"Authorization": f"Bearer {forged_token}"})

    assert_generic_401(response)


# Acceptance criterion: JWT verification uses Supabase's JWKS endpoint (derived from SUPABASE_URL, not hardcoded)
def test_jwks_url_and_issuer_derived_from_supabase_url_env_var(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example-project.supabase.co")

    middleware = JWTAuthMiddleware(FastAPI())

    assert middleware.jwks_client.uri == "https://example-project.supabase.co/auth/v1/.well-known/jwks.json"
    assert middleware.issuer == "https://example-project.supabase.co/auth/v1"


def test_missing_supabase_url_env_var_raises(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    with pytest.raises(KeyError):
        JWTAuthMiddleware(FastAPI())
