from pydantic import BaseModel


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    created_at: str
    document_count: int


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceResponse]


class WorkspaceNameRequest(BaseModel):
    name: str
