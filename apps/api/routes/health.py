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
    )
