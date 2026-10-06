from uuid import UUID

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    storage_path: str
    filename: str
    mime_type: str
    size_bytes: int = Field(gt=0)
    # Omitted: the user's default workspace.
    workspace_id: UUID | None = None


class IngestResponse(BaseModel):
    document_id: str
    status: str
    created_at: str
