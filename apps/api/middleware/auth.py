import os

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from errors import error_envelope

EXEMPT_PATHS = {"/health"}

# Every auth failure returns this exact message, regardless of which check
# failed (missing header, bad signature, wrong issuer/audience, expired,
# missing sub, ...) — per API_CONTRACT.md's UNAUTHORIZED contract. Do not
# let this vary by failure reason; that leaks which validation stage an
# attacker's token tripped.
GENERIC_UNAUTHORIZED_MESSAGE = "Missing or invalid JWT"


class JWTAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        jwks_client: PyJWKClient | None = None,
        issuer: str | None = None,
        audience: str = "authenticated",
    ):
        super().__init__(app)

        if jwks_client is not None:
            self.jwks_client = jwks_client
        else:
            supabase_url = os.environ["SUPABASE_URL"]
            self.jwks_client = PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json")

        if issuer is not None:
            self.issuer = issuer
        else:
            supabase_url = os.environ["SUPABASE_URL"]
            self.issuer = f"{supabase_url}/auth/v1"

        self.audience = audience

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return _unauthorized()

        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp"]},
            )
        except jwt.PyJWTError:
            return _unauthorized()

        user_id = payload.get("sub")
        if not user_id:
            return _unauthorized()

        request.state.user_id = user_id
        return await call_next(request)


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=error_envelope("UNAUTHORIZED", GENERIC_UNAUTHORIZED_MESSAGE),
    )
