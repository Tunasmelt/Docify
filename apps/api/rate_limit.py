from datetime import datetime, timezone

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


def global_key(request: Request) -> str:
    """A fixed key, ignoring the request entirely — every call sharing
    this key_func draws on ONE counter across every user combined, not a
    per-user one. Real gap this closes (FEAT-024 follow-up, 2026-08-02):
    the existing per-user limits (user_id_key above) each individually
    stay under Voyage's real 3 RPM ceiling, but N users each within their
    own per-user limit can still collectively exceed it — 2 users at
    2/minute each is 4/minute against a 3/minute shared vendor budget,
    and per-user limits alone structurally cannot see that. This key_func
    is what lets a SINGLE slowapi limit enforce a truly global, cross-
    user counter instead."""
    return "global"


class DailyLimitExceeded(Exception):
    """Raised by check_daily_limit below — routes catch this directly
    (not via slowapi's exception handler, since this check runs manually
    inside the route body, not through a @limiter.limit(...) decorator)
    and return the same RATE_LIMITED envelope real clients already expect
    from the in-memory-backed limits."""


def check_daily_limit(client, *, user_id: str, route: str, limit: int) -> None:
    """Postgres-backed daily counter — NOT slowapi/in-memory. Real reason
    (FEAT-024 follow-up, 2026-08-02, .agent/SCOPE.md's Phase 5
    "Production job execution" entry): Render's free tier loses all
    in-memory state on every ~15-minute idle spin-down. For a PER-MINUTE
    limit this is a non-issue — a 15-minute gap already exceeds any
    per-minute window, so a restart-induced reset and a legitimate window
    rollover are indistinguishable in practice; per-minute limits
    (INGEST_MINUTE_LIMIT, the global per-minute limit above, QUERY_MINUTE_
    LIMIT) deliberately stay on slowapi's in-memory storage, unchanged.
    For a DAILY limit the gap is real: a user could ride out one or more
    spin-downs over the course of a day and get a fresh 10/day or 40/day
    budget on each restart, silently multiplying their real daily quota
    against the shared Voyage/Gemini vendor ceilings this limit exists to
    protect. `increment_usage_counter` (migration
    20260802_001_usage_counters.sql) does an atomic upsert-and-return in
    one Postgres round trip — no separate read-then-write from Python,
    which would have a real race between two near-simultaneous requests
    from the same user.

    Every call that reaches this function counts against the day's
    total, whether or not it ends up over the limit — matching slowapi's
    own behavior on the per-minute limits (an over-limit attempt still
    consumes a slot in the window, it doesn't get a free pass for being
    rejected). Raises DailyLimitExceeded if the count including this call
    exceeds `limit`; returns None (already counted) otherwise.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    result = client.rpc(
        "increment_usage_counter",
        {"p_user_id": user_id, "p_route": route, "p_day": today},
    ).execute()
    count = result.data
    if count > limit:
        raise DailyLimitExceeded(f"daily limit exceeded for user {user_id!r} route {route!r}: {count}/{limit}")


def daily_limit_exceeded_response() -> JSONResponse:
    """Use the standard 429 envelope but distinguish daily exhaustion
    from the temporary, Retry-After-bearing minute window."""
    return JSONResponse(
        status_code=429,
        content=error_envelope(
            "RATE_LIMITED",
            "Daily request limit reached; retry after the UTC daily reset",
        ),
    )


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
