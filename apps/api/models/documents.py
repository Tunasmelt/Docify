from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    filename: str
    page_count: int | None
    status: str
    error: str | None
    created_at: str
    parsed_at: str | None
    embedded_at: str | None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    next_cursor: str | None
