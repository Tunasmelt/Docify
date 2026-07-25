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
    # Only set for element_type == "figure" (a signed, time-limited Storage
    # URL — the "figures" bucket is private, RLS-scoped to the owning
    # user's own JWT, which the service-role client doesn't have). Routes
    # returning this must set response_model_exclude_none=True so a
    # non-figure citation OMITS this key entirely rather than sending
    # "figure_url": null — easy to mishandle client-side (a naive
    # `if (citation.figure_url)` guard is fine either way, but omission
    # is the less surprising, more standard REST shape for "not
    # applicable" versus "applicable but empty").
    figure_url: str | None = None


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
