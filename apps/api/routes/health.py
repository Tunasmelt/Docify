import os
from datetime import datetime, timezone

from fastapi import APIRouter

from models.health import HealthResponse

APP_VERSION = "0.1.0"

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Real, direct proof of which commit this specific running
        # container is executing — see models/health.py's own comment.
        # "unknown" outside Render (local dev, docker run without
        # Render's orchestration), never a crash.
        commit=os.environ.get("RENDER_GIT_COMMIT", "unknown"),
    )
