from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: str
    # Render's own auto-injected RENDER_GIT_COMMIT env var (confirmed
    # against Render's real docs, 2026-08-18: available at both build
    # time and runtime for every deploy, no Dockerfile changes needed) —
    # a real, permanent way to confirm exactly which commit a running
    # container is actually executing, closing the gap this project hit
    # investigating whether a retry-timing fix had genuinely deployed:
    # Render reporting a deploy "live" was never proof the right code
    # was running, and neither SSH nor Render's Jobs feature (paid-tier
    # only) were available to check directly. Local/non-Render
    # environments (docker run without Render's orchestration) won't
    # have this set — "unknown" is the honest answer there, not a crash.
    commit: str
