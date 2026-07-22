import os

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from errors import error_envelope

EXEMPT_PATHS = {"/health"}


class JWTAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, jwt_secret: str | None = None):
        super().__init__(app)
        self.jwt_secret = jwt_secret if jwt_secret is not None else os.environ["SUPABASE_JWT_SECRET"]

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return _unauthorized("Missing or invalid Authorization header")

        try:
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return _unauthorized("Invalid or expired JWT")

        user_id = payload.get("sub")
        if not user_id:
            return _unauthorized("JWT missing sub claim")

        request.state.user_id = user_id
        return await call_next(request)


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(status_code=401, content=error_envelope("UNAUTHORIZED", message))
