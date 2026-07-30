import os

from dotenv import load_dotenv

load_dotenv()  # must run before any module below reads env vars at import/construction time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from middleware.auth import JWTAuthMiddleware
from rate_limit import limiter, rate_limit_exceeded_handler
from routes import conversations, documents, health, ingest, query

app = FastAPI(title="docify-api")

app.add_middleware(JWTAuthMiddleware)

# FEAT-024 (2026-07-28) — app.state.limiter/the exception handler must be
# registered regardless of SlowAPIMiddleware's own position in the chain
# below: the actual per-route rate-limit check happens inside each
# route's own @limiter.limit(...) decorator (routes/ingest.py,
# routes/query.py), which — like every route body — only ever runs
# AFTER every registered Starlette middleware, JWTAuthMiddleware
# included. So user_id_key() (rate_limit.py) is guaranteed
# request.state.user_id is already set by the time it's called,
# independent of where SlowAPIMiddleware itself sits here; confirmed
# live in tests/test_rate_limit.py, not assumed from this ordering
# alone. SlowAPIMiddleware's own job is narrower: it's what makes
# request.state.view_rate_limit available for _inject_headers() to add
# the standard Retry-After/X-RateLimit-* headers.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Added for FEAT-014 (Documents UI wiring) — the frontend calls this API
# directly from the browser (NEXT_PUBLIC_API_URL), and no CORS middleware
# existed at all until now; every real cross-origin fetch would have been
# blocked before ever reaching a route. Must be added AFTER
# JWTAuthMiddleware above: Starlette wraps middlewares in reverse
# registration order, so the last one added is outermost and runs first —
# CORSMiddleware needs to see (and short-circuit) the browser's OPTIONS
# preflight before JWTAuthMiddleware would otherwise reject it with 401
# (preflight requests never carry the Authorization header).
# FRONTEND_ORIGINS is comma-separated; defaults cover both `127.0.0.1` and
# `localhost` for local dev (FEAT-013 found these are NOT interchangeable
# for Supabase's own redirect allowlist — same caution applies to CORS
# here). Set a real deployed origin via env var once one exists (Phase 5).
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "FRONTEND_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000"
    ).split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(ingest.router)
app.include_router(documents.router)
app.include_router(query.router)
app.include_router(conversations.router)
