from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from errors import error_envelope

# In-memory storage (slowapi's default, no storage_uri given) — the
# right call at this project's scale, not a default left unexamined:
# a single Render instance, no Redis anywhere else in the stack, and
# adding one just for this would be real new infrastructure for a
# problem that doesn't need it yet. Known, accepted tradeoff, stated
# explicitly (also logged in .agent/SCOPE.md's rate-limiting entry):
# limits reset on every redeploy/cold-start restart, and this in-memory
# approach stops being correct the moment this service ever runs as
# more than one instance (each instance would track its own separate
# counters, silently multiplying the effective limit by instance count).
# Revisit with a shared store (e.g. Redis via storage_uri=) if either
# of those changes.


def user_id_key(request: Request) -> str:
    """Keys every limit by the JWT-verified user_id (request.state.user_id,
    set by JWTAuthMiddleware — the same trust source every other
    decision in this app already uses), never by IP. Both rate-limited
    routes (/ingest, /query, /query/stream) already require auth, so
    there is no legitimate unauthenticated case this could be called
    for — an invalid/missing JWT is rejected by JWTAuthMiddleware with
    401 before the route (and therefore this key function) ever runs,
    confirmed live in test_rate_limit.py, not assumed from middleware
    registration order alone."""
    return request.state.user_id


# headers_enabled=True: slowapi's own default is False, which silently
# no-ops _inject_headers() below (confirmed live — the Retry-After/
# X-RateLimit-* headers were simply absent from a real 429 response
# until this was set explicitly). Real clients need Retry-After to back
# off correctly instead of guessing/retrying immediately.
limiter = Limiter(key_func=user_id_key, headers_enabled=True)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Matches API_CONTRACT.md's standard error envelope — slowapi's own
    default handler returns a differently-shaped body ({"error": "..."}
    as a bare string, not this project's {"error": {"code", "message"}}
    object), so every other client-side error-handling path in this app
    would have to special-case rate-limit responses if left as-is."""
    response = JSONResponse(
        status_code=429,
        content=error_envelope("RATE_LIMITED", f"Rate limit exceeded: {exc.detail}"),
    )
    # _inject_headers is the same call slowapi's own default handler
    # makes (confirmed against the installed source) — adds the
    # standard Retry-After/X-RateLimit-* headers real clients use to
    # back off correctly, not just the JSON body.
    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
