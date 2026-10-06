# Tests for [FEAT-026] `GET /conversations` + `GET /conversations/{id}/messages`
#
# Same discipline as FEAT-008/FEAT-012: real local Supabase (Auth,
# Postgres, RLS, Storage) throughout, Retriever/Generator/Verifier faked
# via FastAPI dependency overrides (routes/query.py's own pattern) to
# drive a real POST /query call that persists a real conversation with
# real messages and real citations — never a hand-built fixture pretending
# to be one. Standard-depth review: no new trust boundary, same
# user_id-scoped-query + non-leaking-404 pattern FEAT-008 already proved.

import pytest

from main import app
from routes import query
from services.generator import GenerateResult
from services.retriever import RetrievedChunk
from services.verifier import Verdict, VerdictLabel
from tests.conftest import fake_elements, ingest_real_document

_DUMMY_EMBEDDING = [0.0] * 1024


class FakeRetriever:
    def __init__(self, chunks):
        self._chunks = chunks

    def retrieve(self, question, document_ids, user_id, k=8, rerank=False):
        return self._chunks


class FakeGenerator:
    def __init__(self, result):
        self._result = result

    def generate(self, question, chunks, history=None):
        return self._result


class FakeVerifier:
    def __init__(self, verdicts_by_chunk_id):
        self._verdicts_by_chunk_id = verdicts_by_chunk_id

    def verify_batch(self, pairs):
        return [self._verdicts_by_chunk_id[chunk.chunk_id] for _, chunk in pairs]


def _override(retriever, generator, verifier):
    app.dependency_overrides[query.get_retriever] = lambda: retriever
    app.dependency_overrides[query.get_generator] = lambda: generator
    app.dependency_overrides[query.get_verifier] = lambda: verifier
    app.dependency_overrides[query.get_query_rewriter] = lambda: _PassthroughRewriter()


@pytest.fixture(autouse=True)
def _clear_query_overrides_after_each_test():
    yield
    app.dependency_overrides.clear()


def _verdict(label, quote="a quote"):
    return Verdict(
        verdict=label,
        quote=quote if label != VerdictLabel.UNSUPPORTED else None,
        model="gemini-3.5-flash-lite",
        input_tokens=10,
        output_tokens=5,
        latency_ms=100.0,
    )


def _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, answer, verdict_label=VerdictLabel.SUPPORTED, conversation_id=None):
    """Drives one real POST /query turn end to end — real retrieval fake,
    real generation fake, real verification fake, but a REAL persisted
    conversation/message/citation via the real endpoint (never a
    hand-inserted row standing in for one)."""
    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"],
            content=chunk_row["content"],
            page=chunk_row["page_number"],
            document_id=document_id,
            document_name="doc.pdf",
            document_mime_type="application/pdf",
            element_type=chunk_row["element_type"],
            score=0.9,
        )
    ]
    gen_result = GenerateResult(
        answer=answer,
        cited_indices=[1],
        hallucinated_markers=[],
        model="gemini-3.6-flash",
        input_tokens=100,
        output_tokens=20,
        latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(verdict_label)}),
    )
    payload = {"question": "a question", "document_ids": [document_id]}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    response = app_client.post("/query", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


# Acceptance criterion: GET /conversations returns paginated list scoped to JWT user
def test_get_conversations_returns_paginated_list_scoped_to_jwt_user(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    _, token_b = user_b

    document_id = ingest_real_document(app_client, user_id_a, token_a, filename="doc.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]

    result_1 = _ask_real_question(app_client, admin, user_id_a, token_a, document_id, chunk_row, "Answer one [1].")
    conv_id = result_1["conversation_id"]
    # A second turn in the SAME conversation must not create a second
    # conversations row, and must bump message_count / updated_at.
    _ask_real_question(app_client, admin, user_id_a, token_a, document_id, chunk_row, "Answer two [1].", conversation_id=conv_id)
    # A second, SEPARATE conversation for user A (no conversation_id
    # passed) — needed so pagination below actually has 2+ rows to page
    # across, not just the one two-turn conversation.
    _ask_real_question(app_client, admin, user_id_a, token_a, document_id, chunk_row, "A different conversation [1].")

    document_id_b = ingest_real_document(app_client, *user_b, filename="other.pdf")
    chunk_row_b = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id_b).execute().data[0]
    _ask_real_question(app_client, admin, *user_b, document_id_b, chunk_row_b, "Other user's answer [1].")

    response = app_client.get("/conversations", headers={"Authorization": f"Bearer {token_a}"})
    assert response.status_code == 200
    body = response.json()
    ids = {c["id"] for c in body["conversations"]}
    assert conv_id in ids
    assert all(c["id"] != document_id_b for c in body["conversations"])  # sanity, not the real isolation check

    # user B's conversation must never appear in user A's list.
    conv_row = next(c for c in body["conversations"] if c["id"] == conv_id)
    assert conv_row["workspace_id"] == admin.table("documents").select("workspace_id").eq("id", document_id).execute().data[0]["workspace_id"]
    assert conv_row["message_count"] == 4  # 2 turns x (user question + assistant answer)
    assert set(conv_row.keys()) == {"id", "workspace_id", "title", "document_ids", "message_count", "updated_at"}

    list_as_b = app_client.get("/conversations", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert conv_id not in {c["id"] for c in list_as_b["conversations"]}

    # Pagination: limit=1 across 2+ conversations for user A pages correctly.
    page1 = app_client.get("/conversations?limit=1", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert len(page1["conversations"]) == 1
    assert page1["next_cursor"] is not None
    page2 = app_client.get(
        f"/conversations?limit=1&cursor={page1['next_cursor']}", headers={"Authorization": f"Bearer {token_a}"}
    ).json()
    assert page1["conversations"][0]["id"] != page2["conversations"][0]["id"]


# Acceptance criterion: GET /conversations/{id}/messages returns full history including citations
def test_get_conversation_messages_returns_full_history_with_citations(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]

    result = _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, "Revenue grew 12% [1].")
    conv_id = result["conversation_id"]

    response = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()

    assert body["conversation"]["id"] == conv_id
    assert set(body["conversation"].keys()) == {"id", "workspace_id", "title", "document_ids", "created_at", "updated_at"}

    assert len(body["messages"]) == 2
    user_msg, assistant_msg = body["messages"]
    assert user_msg["role"] == "user"
    assert "citations" not in user_msg  # omitted, not null — response_model_exclude_none

    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"] == "Revenue grew 12% [1]."
    assert len(assistant_msg["citations"]) == 1
    citation = assistant_msg["citations"][0]
    # Same shape as POST /query's own citations (API_CONTRACT.md) — and
    # crucially the SAME marker value POST /query itself returned, not a
    # renumbered 1..N reconstruction (the real gap this feature's own
    # migration closed).
    assert citation["marker"] == result["citations"][0]["marker"] == 1
    assert citation["chunk_id"] == chunk_row["id"]
    assert citation["document_id"] == document_id
    assert citation["document_name"] == "doc.pdf"
    assert citation["document_mime_type"] == "application/pdf"
    assert citation["verdict"] == "supported"
    assert "figure_url" not in citation  # text citation — omitted, not null


# FEAT-020 (2026-07-27): document_mime_type is what the citation-display
# layer needs to tell a real PDF page from a PPTX slide/DOCX-HTML
# sentinel — confirm a real NON-pdf value round-trips through this read
# path too (CITATION_JOIN_COLUMNS' documents(filename,mime_type) embed),
# not just the one hardcoded "application/pdf" every other test here uses.
def test_get_conversation_messages_citation_carries_the_real_non_pdf_mime_type(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(
        app_client, user_id, token, filename="slides.pptx",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]

    result = _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, "Customer count grew to 5,200 [1].")
    conv_id = result["conversation_id"]

    response = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    citation = response.json()["messages"][1]["citations"][0]

    assert citation["document_mime_type"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# Acceptance criterion: unsupported citations are not shown in message history either
def test_get_conversation_messages_omits_unsupported_citations(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]

    result = _ask_real_question(
        app_client, admin, user_id, token, document_id, chunk_row, "A fabricated claim [1].", verdict_label=VerdictLabel.UNSUPPORTED
    )
    conv_id = result["conversation_id"]
    assert result["citations"] == []  # sanity: POST /query itself already drops it

    response = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token}"})
    assistant_msg = response.json()["messages"][1]
    assert assistant_msg["citations"] == []


# Figure citations: real figure upload, real signed URL, real fetch.
def test_get_conversation_messages_figure_citation_has_a_real_fetchable_signed_url(app_client, admin, user_a):
    user_id, token = user_a
    document_id = admin.table("documents").insert(
        {"user_id": user_id, "filename": "figures.pdf", "storage_path": f"uploads/{user_id}/figures.pdf", "mime_type": "application/pdf", "size_bytes": 1}
    ).execute().data[0]["id"]

    fake_png = b"\x89PNG\r\n\x1a\nfake-figure-bytes"
    figure_path = f"{user_id}/{document_id}/0.png"
    admin.storage.from_("figures").upload(figure_path, fake_png, file_options={"content-type": "image/png"})

    figure_chunk = admin.table("chunks").insert(
        {
            "document_id": document_id, "user_id": user_id, "chunk_index": 0, "element_type": "figure",
            "page_number": 1, "content": "", "figure_path": figure_path, "embedding": _DUMMY_EMBEDDING,
        }
    ).execute().data[0]

    result = _ask_real_question(app_client, admin, user_id, token, document_id, figure_chunk, "See the chart [1].")
    conv_id = result["conversation_id"]

    # POST /query's own live response already has a real, fetchable signed URL.
    live_citation = result["citations"][0]
    assert live_citation["element_type"] == "figure"
    assert live_citation["figure_url"], "live /query response must include a real signed figure_url"
    import httpx
    live_fetch = httpx.get(live_citation["figure_url"])
    assert live_fetch.status_code == 200
    assert live_fetch.content == fake_png

    # And the SAME figure citation, read back later via message history,
    # also has a real, independently fetchable signed URL (a fresh one —
    # signed URLs aren't persisted, they're built at read time).
    response = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token}"})
    history_citation = response.json()["messages"][1]["citations"][0]
    assert history_citation["element_type"] == "figure"
    assert history_citation["figure_url"]
    history_fetch = httpx.get(history_citation["figure_url"])
    assert history_fetch.status_code == 200
    assert history_fetch.content == fake_png

    admin.table("chunks").delete().eq("document_id", document_id).execute()
    admin.table("documents").delete().eq("id", document_id).execute()
    admin.storage.from_("figures").remove([figure_path])


# Acceptance criterion: GET /conversations/{id}/messages returns 404 for another user's conversation
def test_get_conversation_messages_returns_404_for_another_user_s_conv(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    _, token_b = user_b
    document_id = ingest_real_document(app_client, user_id_a, token_a, filename="private.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id_a, token_a, document_id, chunk_row, "Private answer [1].")
    conv_id = result["conversation_id"]

    as_owner = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token_a}"})
    assert as_owner.status_code == 200  # positive control

    as_other_user = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token_b}"})
    genuinely_nonexistent = app_client.get(
        "/conversations/00000000-0000-0000-0000-000000000000/messages", headers={"Authorization": f"Bearer {token_b}"}
    )

    # Same status code AND same body for "belongs to someone else" and
    # "doesn't exist at all" — no existence leak, same discipline as
    # FEAT-008's get_document() 404.
    assert as_other_user.status_code == 404
    assert genuinely_nonexistent.status_code == 404
    assert as_other_user.json() == genuinely_nonexistent.json()


# Multi-tenant isolation across both endpoints — same live-verification
# discipline as FEAT-008: two real users, positive control proving user
# A's own access works, then confirm user B cannot list or read user A's
# conversation via either endpoint.
def test_multi_tenant_isolation_across_conversations_and_messages(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    _, token_b = user_b
    document_id = ingest_real_document(app_client, user_id_a, token_a, filename="isolated.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id_a, token_a, document_id, chunk_row, "Isolated answer [1].")
    conv_id = result["conversation_id"]

    assert app_client.get("/conversations", headers={"Authorization": f"Bearer {token_a}"}).status_code == 200
    list_as_a = app_client.get("/conversations", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert conv_id in {c["id"] for c in list_as_a["conversations"]}
    assert app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token_a}"}).status_code == 200

    list_as_b = app_client.get("/conversations", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert conv_id not in {c["id"] for c in list_as_b["conversations"]}

    get_as_b = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token_b}"})
    assert get_as_b.status_code == 404


# --- Batch 3 (2026-08-04): POST /conversations/{id}/rename, DELETE /conversations/{id} ---


def test_rename_conversation_updates_title_and_returns_the_updated_conversation(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, "Answer [1].")
    conv_id = result["conversation_id"]

    response = app_client.post(
        f"/conversations/{conv_id}/rename", json={"title": "  My renamed conversation  "}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Trimmed, not stored with the surrounding whitespace verbatim.
    assert body["title"] == "My renamed conversation"
    assert body["id"] == conv_id

    # Real persistence, not just an echoed request body -- confirmed via
    # a completely separate real GET.
    listed = app_client.get("/conversations", headers={"Authorization": f"Bearer {token}"}).json()
    renamed = next(c for c in listed["conversations"] if c["id"] == conv_id)
    assert renamed["title"] == "My renamed conversation"


def test_rename_conversation_does_not_bump_updated_at(app_client, admin, user_a):
    # Real product decision (API_CONTRACT.md): a rename is a metadata
    # edit, not new conversation activity -- must never reorder "Recent"
    # on its own.
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, "Answer [1].")
    conv_id = result["conversation_id"]

    before = admin.table("conversations").select("updated_at").eq("id", conv_id).execute().data[0]["updated_at"]

    response = app_client.post(
        f"/conversations/{conv_id}/rename", json={"title": "New title"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200

    after = admin.table("conversations").select("updated_at").eq("id", conv_id).execute().data[0]["updated_at"]
    assert after == before, "rename must not change updated_at"


def test_rename_conversation_rejects_empty_title(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, "Answer [1].")
    conv_id = result["conversation_id"]

    for bad_title in ["", "   ", "\t\n"]:
        response = app_client.post(
            f"/conversations/{conv_id}/rename", json={"title": bad_title}, headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_rename_conversation_rejects_title_over_200_chars(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, "Answer [1].")
    conv_id = result["conversation_id"]

    response = app_client.post(
        f"/conversations/{conv_id}/rename", json={"title": "x" * 201}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    # Exactly 200 is allowed -- the boundary itself, not just "too long"
    # is rejected.
    ok_response = app_client.post(
        f"/conversations/{conv_id}/rename", json={"title": "x" * 200}, headers={"Authorization": f"Bearer {token}"}
    )
    assert ok_response.status_code == 200


def test_rename_conversation_returns_404_for_nonexistent_or_another_user_s_conversation(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    document_id = ingest_real_document(app_client, user_id_a, token_a, filename="private.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id_a, token_a, document_id, chunk_row, "Private answer [1].")
    conv_id = result["conversation_id"]

    # A real, syntactically-valid but nonexistent conversation id.
    nonexistent = app_client.post(
        "/conversations/00000000-0000-0000-0000-000000000000/rename",
        json={"title": "x"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert nonexistent.status_code == 404

    # User B attempting to rename user A's real conversation -- identical
    # 404, no ownership oracle, matching every other owned-resource
    # lookup in this API.
    as_b = app_client.post(
        f"/conversations/{conv_id}/rename", json={"title": "hijacked"}, headers={"Authorization": f"Bearer {token_b}"}
    )
    assert as_b.status_code == 404

    # Confirm the title was genuinely untouched by the failed attempt.
    still_owned = admin.table("conversations").select("title").eq("id", conv_id).execute().data[0]
    assert still_owned["title"] != "hijacked"


def test_delete_conversation_removes_it_and_cascades_messages_and_citations(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, "Answer [1].")
    conv_id = result["conversation_id"]
    message_id = result["message_id"]

    # Real rows exist before delete -- not asserting against an empty
    # baseline.
    assert admin.table("messages").select("id").eq("conversation_id", conv_id).execute().data
    assert admin.table("citations").select("id").eq("message_id", message_id).execute().data

    response = app_client.delete(f"/conversations/{conv_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204
    assert response.content == b""

    # The conversation row itself is gone.
    assert admin.table("conversations").select("id").eq("id", conv_id).execute().data == []
    # messages.conversation_id -> conversations(id) on delete cascade
    # (SCHEMA.md) -- confirmed live, not assumed from reading the FK.
    assert admin.table("messages").select("id").eq("conversation_id", conv_id).execute().data == []
    # citations.message_id -> messages(id) on delete cascade -- the
    # second hop of the same real cascade, confirmed independently.
    assert admin.table("citations").select("id").eq("message_id", message_id).execute().data == []

    # GET /conversations/{id}/messages on the now-deleted conversation is
    # a clean 404, same as one that never existed.
    after_delete = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token}"})
    assert after_delete.status_code == 404


def test_delete_conversation_returns_404_for_nonexistent_or_another_user_s_conversation(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    _, token_b = user_b
    document_id = ingest_real_document(app_client, user_id_a, token_a, filename="private.pdf")
    chunk_row = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    result = _ask_real_question(app_client, admin, user_id_a, token_a, document_id, chunk_row, "Private answer [1].")
    conv_id = result["conversation_id"]

    nonexistent = app_client.delete(
        "/conversations/00000000-0000-0000-0000-000000000000", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert nonexistent.status_code == 404

    # User B attempting to delete user A's real conversation -- identical
    # 404, and the conversation must genuinely survive the attempt (not
    # just "the response said 404" while the row was silently removed
    # anyway).
    as_b = app_client.delete(f"/conversations/{conv_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert as_b.status_code == 404
    assert admin.table("conversations").select("id").eq("id", conv_id).execute().data != []

    still_works_for_owner = app_client.get(f"/conversations/{conv_id}/messages", headers={"Authorization": f"Bearer {token_a}"})
    assert still_works_for_owner.status_code == 200


class _PassthroughRewriter:
    """Follow-up questions are searched unchanged — no real Gemini rewrite call."""

    def rewrite(self, question, history):
        return question

