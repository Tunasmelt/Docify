from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    workspace_id: str
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


class RenameDocumentRequest(BaseModel):
    filename: str


class SourceContextBlock(BaseModel):
    chunk_id: str
    element_type: str
    content: str
    cited: bool
    figure_url: str | None = None


class SourceContextResponse(BaseModel):
    # "slide" (PPTX: everything on the cited slide) or "section" (DOCX/HTML:
    # the cited chunk's neighbours within the same section).
    kind: str
    label: str | None
    blocks: list[SourceContextBlock]
