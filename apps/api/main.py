import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # must run before any module below reads env vars at import/construction time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from middleware.auth import JWTAuthMiddleware
from rate_limit import limiter, rate_limit_exceeded_handler
from routes import account, conversations, documents, export, health, ingest, query, workspaces
from services import ingest_queue
from services.observability import init_sentry

# Before the app is created, so the FastAPI integration hooks in. No-op without SENTRY_DSN.
init_sentry()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The ingest worker (services/ingest_queue.py) runs in this process.
    # INGEST_WORKER_ENABLED=0 leaves jobs queued for a worker elsewhere.
    # Tests use TestClient without `with`, which skips lifespan, and drain
    # the queue themselves (tests/conftest.py).
    if os.environ.get("INGEST_WORKER_ENABLED", "1") != "0":
        ingest_queue.start_worker(ingest.run_ingest_pipeline)
    yield
    ingest_queue.stop_worker()


app = FastAPI(title="docify-api", lifespan=lifespan)

app.add_middleware(JWTAuthMiddleware)

# Rate limiting (FEAT-024). The per-route checks live in each route's own
# @limiter.limit(...) decorator, which runs after every middleware — so
# request.state.user_id from JWTAuthMiddleware is always set by then,
# regardless of where SlowAPIMiddleware sits (tests/test_rate_limit.py).
# SlowAPIMiddleware only adds the Retry-After / X-RateLimit-* headers.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# The browser calls this API directly, so CORS is required. Added last so
# it is outermost (Starlette runs middleware in reverse registration order)
# and answers OPTIONS preflights before JWTAuthMiddleware would 401 them.
# FRONTEND_ORIGINS is comma-separated; localhost and 127.0.0.1 are distinct
# origins, so local dev defaults include both.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "FRONTEND_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000"
    ).split(","),
    allow_methods=["*"],
    allow_headers=["*"],
    # Content-Disposition isn't exposed to cross-origin JS by default;
    # lib/api/export.ts reads it to name the downloaded export file.
    expose_headers=["Content-Disposition"],
)

app.include_router(health.router)
app.include_router(ingest.router)
app.include_router(documents.router)
app.include_router(query.router)
app.include_router(conversations.router)
app.include_router(export.router)
app.include_router(account.router)
app.include_router(workspaces.router)
