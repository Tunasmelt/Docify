from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str
    document_ids: list[str]
    conversation_id: str | None = None
    k: int = Field(8, ge=1, le=50)


class CitationResponse(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    document_name: str
    page_number: int
    element_type: str
    snippet: str
    verdict: str
    supporting_quote: str | None


class QueryMetadata(BaseModel):
    model: str
    verifier_model: str
    retrieved_count: int
    cited_count: int
    latency_ms: int


class QueryResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    citations: list[CitationResponse]
    metadata: QueryMetadata
