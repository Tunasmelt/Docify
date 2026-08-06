from pydantic import BaseModel


class ExportCitation(BaseModel):
    """Full citation record for export — a superset of models/query.py's
    CitationResponse (marker, chunk_id, document_name, page_number,
    element_type, verdict, supporting_quote), adding the fields that
    exist in the citations table (SCHEMA.md) but were never part of the
    live-UI response shape: claim_span/claim_start/claim_end (the exact
    claim the verdict was verified against), verifier_model, and
    verified_at. An export's whole point is completeness, not matching
    the trimmed live-response shape."""

    marker: int
    chunk_id: str
    document_name: str
    page_number: int
    element_type: str
    claim_span: str
    claim_start: int | None
    claim_end: int | None
    verdict: str
    supporting_quote: str | None
    verifier_model: str
    verified_at: str


class ExportMessage(BaseModel):
    id: str
    role: str
    # UI-facing, post-verification text (identical to what the app ever
    # rendered — matches MessageResponse.content, models/conversations.py).
    content: str
    # Pre-verification text (None only for role == "user", which never
    # had a raw/final distinction to begin with) — included because an
    # UNSUPPORTED citation's [N] marker is stripped from `content` by
    # routes/query.py's _strip_dropped_markers, but the citation itself
    # is still exported in full below; raw_content is the only place its
    # marker is still visible in context.
    raw_content: str | None
    created_at: str
    citations: list[ExportCitation] = []


class ExportConversation(BaseModel):
    id: str
    title: str | None
    # Resolved to real filenames (db.queries.get_document_filenames) —
    # conversations.document_ids on its own is just a list of ids, not
    # something a human-readable export should surface as-is.
    document_names: list[str]
    created_at: str
    updated_at: str
    messages: list[ExportMessage]


class ExportPayload(BaseModel):
    """The JSON export's root shape. exported_at/*_count exist so the
    file is self-describing without cross-referencing the app — a user
    opening this a year from now, account already deleted, should be
    able to tell what it is and whether it looks complete from the file
    alone."""

    exported_at: str
    conversation_count: int
    message_count: int
    citation_count: int
    conversations: list[ExportConversation]
