from pydantic import BaseModel

from models.query import CitationResponse


class ConversationResponse(BaseModel):
    id: str
    title: str | None
    document_ids: list[str]
    message_count: int
    updated_at: str


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    next_cursor: str | None


class ConversationDetail(BaseModel):
    id: str
    title: str | None
    document_ids: list[str]
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    # None for role == "user" (a question never has citations — omitted
    # from the response via response_model_exclude_none, matching
    # API_CONTRACT.md's example, which shows no "citations" key at all on
    # user messages). Always a list (possibly empty) for role == "assistant".
    citations: list[CitationResponse] | None = None


class ConversationMessagesResponse(BaseModel):
    conversation: ConversationDetail
    messages: list[MessageResponse]
