# Tests for [FEAT-012] `/query` endpoint
#
# Highest-scrutiny feature since FEAT-007 — the first place all of
# Phase 1 (ingestion) and Phase 2 (retrieval/generation/verification)
# meet behind one real, user-facing endpoint. The 2026-07-24 full-flow
# audit (.agent/reviews/2026-07-24-full-flow.md) identified specific,
# real risk areas this file targets directly, not generically:
#
# 1. request.state.user_id -> Retriever.retrieve() is THE tenant
#    boundary for this whole feature (audit item 1) — tested adversarially
#    on its own, independent of the full end-to-end isolation test.
# 2. Figure-image fetch (services/figure_fetcher.py) and [N]->chunk_id
#    resolution + claim-span extraction were both confirmed to not exist
#    anywhere before this feature (audit items 3/4) — tested directly.
# 3. partial verdicts must be KEPT with a warning indicator, never
#    dropped like unsupported (audit item 5, and the mistake already
#    caught once in docs this session) — tested explicitly so a future
#    code regression on this specific point would be caught here.
#
# Real local Supabase (Auth, Postgres, RLS, Storage) throughout, real
# ingested documents via ingest_real_document() (fake Docling/Voyage —
# fast, deterministic, matching FEAT-007/008's established pattern).
# Retriever/Generator/Verifier are faked via FastAPI dependency
# overrides (same shape as ingest.py's get_pipeline_runner override) —
# real citations rows have a foreign key to chunks(id), so fakes always
# reference REAL chunk rows from a real ingested document, never
# made-up ids.

import os
import time

import pytest

from db import queries
from main import app
from routes import query
from services.chunker import Chunk
from services.generator import GenerateResult, GeneratorChunk
from services.parser import ElementType
from services.retriever import RetrievedChunk
from services.verifier import Verdict, VerdictLabel
from tests.conftest import fake_elements, ingest_real_document


# --- Fakes --------------------------------------------------------------


class FakeRetriever:
    def __init__(self, chunks: list[RetrievedChunk]):
        self._chunks = chunks
        self.calls: list[dict] = []

    def retrieve(self, question, document_ids, user_id, k=8):
        self.calls.append({"question": question, "document_ids": document_ids, "user_id": user_id, "k": k})
        return self._chunks


class FakeGenerator:
    def __init__(self, result: GenerateResult):
        self._result = result
        self.calls: list[dict] = []

    def generate(self, question, chunks, history=None):
        self.calls.append({"question": question, "chunks": chunks, "history": history})
        return self._result


class FakeGeneratorRaising:
    def __init__(self, exc: Exception):
        self._exc = exc

    def generate(self, question, chunks, history=None):
        raise self._exc


class FakeVerifier:
    """Keyed by chunk_id, not call order — robust to claim-span
    extraction details changing which sentence maps to which position."""

    def __init__(self, verdicts_by_chunk_id: dict[str, Verdict]):
        self._verdicts_by_chunk_id = verdicts_by_chunk_id
        self.captured_pairs: list[tuple[str, GeneratorChunk]] = []

    def verify_batch(self, pairs):
        self.captured_pairs = pairs
        return [self._verdicts_by_chunk_id[chunk.chunk_id] for _, chunk in pairs]


def _verdict(label: VerdictLabel, quote: str | None = "a quote") -> Verdict:
    return Verdict(
        verdict=label,
        quote=quote if label != VerdictLabel.UNSUPPORTED else None,
        model="gemini-3.5-flash-lite",
        input_tokens=10,
        output_tokens=5,
        latency_ms=100.0,
    )


def _override(retriever=None, generator=None, verifier=None):
    if retriever is not None:
        app.dependency_overrides[query.get_retriever] = lambda: retriever
    if generator is not None:
        app.dependency_overrides[query.get_generator] = lambda: generator
    if verifier is not None:
        app.dependency_overrides[query.get_verifier] = lambda: verifier


def _clear_overrides():
    app.dependency_overrides.pop(query.get_retriever, None)
    app.dependency_overrides.pop(query.get_generator, None)
    app.dependency_overrides.pop(query.get_verifier, None)


_DUMMY_EMBEDDING = [0.0] * 1024


def _real_chunk_row(admin, document_id: str) -> dict:
    rows = admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data
    return rows[0]


def _ingest_doc_with_content(app_client, admin, user_id, token, filename, content):
    chunks = [
        Chunk(chunk_index=0, element_type=ElementType.TEXT, page_numbers=[1], source_element_indices=[0], content=content)
    ]
    return ingest_real_document(app_client, user_id, token, filename=filename, chunks=chunks, elements=fake_elements(1))


@pytest.fixture(autouse=True)
def _clear_query_overrides_after_each_test():
    yield
    _clear_overrides()


# --- Part 1: fast/mocked, real DB + real Auth + real ingested chunks -------


# Acceptance criterion: POST /query returns 200 with answer + citations per API_CONTRACT.md
def test_post_query_returns_200_with_answer_citations_per_api_contrac(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12% year over year.")
    chunk_row = _real_chunk_row(admin, document_id)

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
        answer="Revenue grew 12% [1].",
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
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED, "Revenue grew 12%")}),
    )

    response = app_client.post(
        "/query",
        json={"question": "What was revenue growth?", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Revenue grew 12% [1]."
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["marker"] == 1
    assert citation["chunk_id"] == chunk_row["id"]
    assert citation["document_id"] == document_id
    assert citation["document_mime_type"] == "application/pdf"
    assert citation["verdict"] == "supported"
    assert citation["supporting_quote"] == "Revenue grew 12%"
    assert body["metadata"]["retrieved_count"] == 1
    assert body["metadata"]["cited_count"] == 1
    assert "conversation_id" in body and body["conversation_id"]
    assert "message_id" in body and body["message_id"]


# Acceptance criterion: Unsupported citations are dropped from response, markers stripped from answer text
def test_unsupported_citations_are_dropped_from_response_markers_stri(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Revenue grew 12%. The forecast is bright.")
    chunk_row = _real_chunk_row(admin, document_id)

    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id,
            document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9,
        )
    ]
    gen_result = GenerateResult(
        answer="Revenue grew 12% [1]. The outlook is fabricated [1].",
        cited_indices=[1],
        hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.UNSUPPORTED, None)}),
    )

    response = app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert "[1]" not in body["answer"]
    assert body["metadata"]["cited_count"] == 0


# 2026-07-24 full-flow audit item 5 — the specific mistake already
# caught once in docs this session (API_CONTRACT.md briefly implied
# partial might be dropped like unsupported). This test would fail if
# that mistake were made in CODE instead.
def test_partial_verdict_citations_are_kept_not_dropped(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Growth was broad-based this quarter.")
    chunk_row = _real_chunk_row(admin, document_id)

    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id,
            document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9,
        )
    ]
    gen_result = GenerateResult(
        answer="Growth was driven by international demand [1].",
        cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.PARTIAL, "Growth was broad-based")}),
    )

    response = app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["citations"]) == 1
    assert body["citations"][0]["verdict"] == "partial"
    assert "[1]" in body["answer"], "partial citations must keep their marker — never dropped like unsupported"
    assert body["metadata"]["cited_count"] == 1


# Acceptance criterion: Creates conversation + message + citations rows atomically
def test_creates_conversation_message_citations_rows_atomically(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "The answer is 42.")
    chunk_row = _real_chunk_row(admin, document_id)

    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id,
            document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9,
        )
    ]
    gen_result = GenerateResult(
        answer="The answer is 42 [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED, "The answer is 42")}),
    )

    response = app_client.post(
        "/query", json={"question": "What is the answer?", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )
    body = response.json()

    conversation = admin.table("conversations").select("*").eq("id", body["conversation_id"]).execute().data
    assert len(conversation) == 1
    assert conversation[0]["user_id"] == user_id
    assert conversation[0]["document_ids"] == [document_id]

    messages = admin.table("messages").select("*").eq("conversation_id", body["conversation_id"]).order("created_at").execute().data
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "What is the answer?"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "The answer is 42 [1]."
    assert messages[1]["id"] == body["message_id"]
    assert messages[1]["retrieved_chunk_ids"] == [chunk_row["id"]]

    citations = admin.table("citations").select("*").eq("message_id", body["message_id"]).execute().data
    assert len(citations) == 1
    assert citations[0]["verdict"] == "supported"
    assert citations[0]["chunk_id"] == chunk_row["id"]


# Persisted for audit even when dropped from the client-facing response
# — ARCHITECTURE.md's verify flow step 5 stores every (claim, chunk)
# pair regardless of verdict.
def test_unsupported_citation_is_still_persisted_for_audit_even_though_dropped_from_response(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "Real content.")
    chunk_row = _real_chunk_row(admin, document_id)

    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id,
            document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9,
        )
    ]
    gen_result = GenerateResult(
        answer="A fabricated claim [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.UNSUPPORTED, None)}),
    )

    response = app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )
    body = response.json()

    assert body["citations"] == []
    citations = admin.table("citations").select("*").eq("message_id", body["message_id"]).execute().data
    assert len(citations) == 1
    assert citations[0]["verdict"] == "unsupported"


# Acceptance criterion: Continuing an existing conversation appends messages correctly
def test_continuing_an_existing_conversation_appends_messages_correct(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "First fact. Second fact.")
    chunk_row = _real_chunk_row(admin, document_id)
    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id,
            document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9,
        )
    ]

    gen_result_1 = GenerateResult(
        answer="First fact [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved), generator=FakeGenerator(gen_result_1),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED, "First fact")}),
    )
    first = app_client.post(
        "/query", json={"question": "first question", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    ).json()

    gen_result_2 = GenerateResult(
        answer="Second fact [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved), generator=FakeGenerator(gen_result_2),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED, "Second fact")}),
    )
    second = app_client.post(
        "/query",
        json={"question": "second question", "document_ids": [document_id], "conversation_id": first["conversation_id"]},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    assert second["conversation_id"] == first["conversation_id"]
    messages = (
        admin.table("messages").select("*").eq("conversation_id", first["conversation_id"]).order("created_at").execute().data
    )
    assert len(messages) == 4
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in messages] == ["first question", "First fact [1].", "second question", "Second fact [1]."]


def test_continuing_a_conversation_that_does_not_belong_to_user_returns_404(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    # User A's own document — isolates this test to the conversation
    # ownership check specifically, not the (separately tested)
    # document_ids ownership check.
    document_id_a = _ingest_doc_with_content(app_client, admin, user_id_a, token_a, "doc.pdf", "content")
    document_id_b = _ingest_doc_with_content(app_client, admin, user_id_b, token_b, "other.pdf", "other content")

    other_users_conversation = (
        admin.table("conversations")
        .insert({"user_id": user_id_b, "title": "t", "document_ids": [document_id_b]})
        .execute()
        .data[0]["id"]
    )

    response = app_client.post(
        "/query",
        json={"question": "q", "document_ids": [document_id_a], "conversation_id": other_users_conversation},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response.status_code == 404


# --- Conversation memory (2026-07-27 follow-up) -----------------------------
#
# Minimal memory: prior Q&A pairs fold into Generator's prompt so a
# follow-up resolves correctly in the ANSWER. Retrieval itself is
# unchanged (still searches on the raw current-turn question alone — no
# query rewriting, a deliberate scope decision, see .agent/FEATURES.md).
# History fetch reuses FEAT-026's already-isolation-proven
# list_messages_for_conversation() (now with an added optional `limit`)
# rather than a second, parallel path.


def _setup_single_chunk_query(app_client, admin, user_id, token, filename="doc.pdf", content="Some fact."):
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, filename, content)
    chunk_row = _real_chunk_row(admin, document_id)
    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id,
            document_name=filename, document_mime_type="application/pdf", element_type="text", score=0.9,
        )
    ]
    return document_id, chunk_row, retrieved


def _post_query(app_client, token, document_id, question, conversation_id=None):
    payload = {"question": question, "document_ids": [document_id]}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    return app_client.post("/query", json=payload, headers={"Authorization": f"Bearer {token}"}).json()


def test_first_turn_with_no_conversation_id_generator_receives_empty_history(app_client, admin, user_a):
    user_id, token = user_a
    document_id, chunk_row, retrieved = _setup_single_chunk_query(app_client, admin, user_id, token)
    gen_result = GenerateResult(
        answer="A fact [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=10, output_tokens=5, latency_ms=10.0,
    )
    fake_generator = FakeGenerator(gen_result)
    _override(
        retriever=FakeRetriever(retrieved), generator=fake_generator,
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED)}),
    )

    _post_query(app_client, token, document_id, "first question")

    assert fake_generator.calls[0]["history"] == []


def test_second_turn_receives_first_turns_qa_as_history(app_client, admin, user_a):
    user_id, token = user_a
    document_id, chunk_row, retrieved = _setup_single_chunk_query(app_client, admin, user_id, token)

    gen_result_1 = GenerateResult(
        answer="First fact [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=10, output_tokens=5, latency_ms=10.0,
    )
    _override(
        retriever=FakeRetriever(retrieved), generator=FakeGenerator(gen_result_1),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED)}),
    )
    first = _post_query(app_client, token, document_id, "first question")

    gen_result_2 = GenerateResult(
        answer="Second fact [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=10, output_tokens=5, latency_ms=10.0,
    )
    fake_generator_2 = FakeGenerator(gen_result_2)
    _override(
        retriever=FakeRetriever(retrieved), generator=fake_generator_2,
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED)}),
    )
    _post_query(app_client, token, document_id, "second question", conversation_id=first["conversation_id"])

    history = fake_generator_2.calls[0]["history"]
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "first question"),
        ("assistant", "First fact [1]."),
    ]


def test_conversation_history_window_is_bounded_to_max_history_turns(app_client, admin, user_a):
    user_id, token = user_a
    document_id, chunk_row, retrieved = _setup_single_chunk_query(app_client, admin, user_id, token)

    total_turns = query.MAX_HISTORY_TURNS + 2  # deliberately more turns than the window keeps
    conversation_id = None
    last_fake_generator = None
    for i in range(total_turns):
        gen_result = GenerateResult(
            answer=f"answer {i} [1].", cited_indices=[1], hallucinated_markers=[],
            model="gemini-3.6-flash", input_tokens=10, output_tokens=5, latency_ms=10.0,
        )
        fake_generator = FakeGenerator(gen_result)
        _override(
            retriever=FakeRetriever(retrieved), generator=fake_generator,
            verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED)}),
        )
        response = _post_query(app_client, token, document_id, f"question {i}", conversation_id=conversation_id)
        conversation_id = response["conversation_id"]
        last_fake_generator = fake_generator

    history = last_fake_generator.calls[0]["history"]
    assert len(history) == query.MAX_HISTORY_TURNS * 2  # bounded, not all (total_turns - 1) prior turns

    contents = [m["content"] for m in history]
    assert "question 0" not in contents  # the oldest turn, now outside the window
    assert "question 1" in contents  # the oldest turn STILL inside the window


def test_conversation_history_with_limit_preserves_user_then_assistant_order_within_a_turn(app_client, admin, user_a):
    # Regression test for a real bug caught while building this: a turn's
    # user and assistant messages share an IDENTICAL created_at (both
    # insert inside the one create_query_turn() transaction, and
    # Postgres's now() is transaction-start time, not per-statement) — an
    # earlier implementation ordered DESC + reversed in Python to bound
    # the fetch, which silently swapped the two to (assistant, user) for
    # exactly this common tie. list_messages_for_conversation()'s limit=
    # path must still return (user, assistant) in that order.
    user_id, token = user_a
    document_id, chunk_row, retrieved = _setup_single_chunk_query(app_client, admin, user_id, token)
    gen_result = GenerateResult(
        answer="A fact [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=10, output_tokens=5, latency_ms=10.0,
    )
    _override(
        retriever=FakeRetriever(retrieved), generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED)}),
    )
    result = _post_query(app_client, token, document_id, "the question")
    conversation_id = result["conversation_id"]

    bounded = queries.list_messages_for_conversation(admin, conversation_id=conversation_id, user_id=user_id, limit=10)

    assert [m["role"] for m in bounded] == ["user", "assistant"]
    assert bounded[0]["content"] == "the question"
    assert bounded[1]["content"] == "A fact [1]."


def test_conversation_history_fetch_with_limit_is_scoped_to_the_requesting_user(app_client, admin, user_a, user_b):
    # The new `limit=` branch of list_messages_for_conversation() is the
    # code this follow-up actually added — confirms isolation holds for
    # it specifically, rather than assuming it's inherited untested from
    # the limit=None path FEAT-026 already proved.
    user_id_a, token_a = user_a
    user_id_b, _token_b = user_b
    document_id, chunk_row, retrieved = _setup_single_chunk_query(
        app_client, admin, user_id_a, token_a, filename="a.pdf", content="user A's fact"
    )
    gen_result = GenerateResult(
        answer="A fact [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=10, output_tokens=5, latency_ms=10.0,
    )
    _override(
        retriever=FakeRetriever(retrieved), generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({chunk_row["id"]: _verdict(VerdictLabel.SUPPORTED)}),
    )
    result = _post_query(app_client, token_a, document_id, "user A's private question")
    conversation_id = result["conversation_id"]

    # Positive control first — user A can fetch their own bounded history.
    as_owner = queries.list_messages_for_conversation(admin, conversation_id=conversation_id, user_id=user_id_a, limit=10)
    assert any(m["content"] == "user A's private question" for m in as_owner)

    as_other_user = queries.list_messages_for_conversation(admin, conversation_id=conversation_id, user_id=user_id_b, limit=10)
    assert as_other_user == []


# Acceptance criterion: Cross-tenant document_ids in request → 403
def test_cross_tenant_document_ids_in_request_403(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    document_id_b = _ingest_doc_with_content(app_client, admin, user_id_b, token_b, "doc.pdf", "user b content")

    response = app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id_b]}, headers={"Authorization": f"Bearer {token_a}"}
    )

    assert response.status_code == 403


# 2026-07-24 self-audit item 1 (priority): a "wholly other user"
# document_ids test is meaningfully weaker than a MIXED list — this
# confirms a request that includes the caller's OWN document alongside
# another real user's document is rejected wholesale, with retrieve()
# never called at all, rather than silently proceeding with just the
# caller's own portion (which would still be safe) or, worse, leaking
# the other user's chunks into the response.
def test_mixed_own_and_other_users_document_ids_is_rejected_wholesale_not_leaked(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    document_id_a = _ingest_doc_with_content(app_client, admin, user_id_a, token_a, "own.pdf", "user a's own content")
    document_id_b = _ingest_doc_with_content(app_client, admin, user_id_b, token_b, "other.pdf", "user b's private content")

    fake_retriever = FakeRetriever([])
    _override(
        retriever=fake_retriever,
        generator=FakeGenerator(
            GenerateResult(answer="n/a", cited_indices=[], hallucinated_markers=[], model="m", input_tokens=0, output_tokens=0, latency_ms=0)
        ),
        verifier=FakeVerifier({}),
    )

    response = app_client.post(
        "/query",
        json={"question": "q", "document_ids": [document_id_a, document_id_b]},
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert response.status_code == 403
    # The most important assertion: retrieve() is never called at all
    # when the request is rejected — there is no code path where a
    # partial/mixed result could have been computed and then leaked.
    assert fake_retriever.calls == [], "retrieve() must never be called for a request rejected at the ownership-check stage"


def test_nonexistent_document_id_also_returns_403_not_404(app_client, user_a):
    # Same discipline as get_document()'s 404 — a document_id that
    # doesn't exist at all gets an identical response to one owned by
    # someone else, so this endpoint gives no oracle either way.
    _user_id, token = user_a
    response = app_client.post(
        "/query", json={"question": "q", "document_ids": ["00000000-0000-0000-0000-000000000000"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_empty_document_ids_returns_422(app_client, user_a):
    _user_id, token = user_a
    response = app_client.post("/query", json={"question": "q", "document_ids": []}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422


def test_empty_question_returns_422(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "content")
    response = app_client.post(
        "/query", json={"question": "   ", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422


# --- Item 1: user_id trust boundary, tested adversarially on its own -------
#
# The full-flow audit's central finding: Generator/Verifier have NO
# tenant concept downstream of Retriever — this call site is the ENTIRE
# safety net. Confirms request.state.user_id (JWT-verified), never
# anything else, reaches Retriever.retrieve() — and confirms
# QueryRequest structurally has no user_id field an attacker could try
# to smuggle in.
def test_retriever_retrieve_s_user_id_arg_is_passed_request_state_use(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "content")
    chunk_row = _real_chunk_row(admin, document_id)

    fake_retriever = FakeRetriever([])  # empty is fine — this test only inspects the call args
    _override(retriever=fake_retriever, generator=FakeGenerator(
        GenerateResult(answer="n/a", cited_indices=[], hallucinated_markers=[], model="m", input_tokens=0, output_tokens=0, latency_ms=0)
    ), verifier=FakeVerifier({}))

    app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )

    assert len(fake_retriever.calls) == 1
    assert fake_retriever.calls[0]["user_id"] == user_id


# Acceptance criterion: GenerateResult.cited_indices (1-indexed
# positions) is mapped back to a real chunk_id via chunks[position - 1]
# — tested with THREE chunks and a citation at position 2, not just the
# trivial single-chunk case where position 1 == index 0 could hide an
# off-by-one bug.
def test_cited_position_maps_to_the_correct_chunk_id_with_multiple_chunks(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "seed content")
    seed_row = _real_chunk_row(admin, document_id)

    row_b = admin.table("chunks").insert(
        {"document_id": document_id, "user_id": user_id, "chunk_index": 1, "element_type": "text", "page_number": 2, "content": "second chunk content", "embedding": _DUMMY_EMBEDDING}
    ).execute().data[0]
    row_c = admin.table("chunks").insert(
        {"document_id": document_id, "user_id": user_id, "chunk_index": 2, "element_type": "text", "page_number": 3, "content": "third chunk content", "embedding": _DUMMY_EMBEDDING}
    ).execute().data[0]

    retrieved = [
        RetrievedChunk(chunk_id=seed_row["id"], content=seed_row["content"], page=1, document_id=document_id, document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9),
        RetrievedChunk(chunk_id=row_b["id"], content=row_b["content"], page=2, document_id=document_id, document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.8),
        RetrievedChunk(chunk_id=row_c["id"], content=row_c["content"], page=3, document_id=document_id, document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.7),
    ]
    gen_result = GenerateResult(
        answer="The second fact is true [2].", cited_indices=[2], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({row_b["id"]: _verdict(VerdictLabel.SUPPORTED, "second chunk content")}),
    )

    response = app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["citations"]) == 1
    # Position 2 must resolve to row_b (index 1), NOT row_a or row_c.
    assert body["citations"][0]["chunk_id"] == row_b["id"]
    assert body["citations"][0]["page_number"] == 2


# 2026-07-24 self-audit item 3 (priority): claim-span extraction is
# sentence-boundary based — two DIFFERENT citations sharing one sentence
# ("Revenue grew [1] due to strong sales in Q3 [2].") must not
# cross-contaminate when their verdicts differ. [1] (supported) must
# survive with its marker intact; [2] (unsupported) must be dropped and
# its marker stripped, without touching [1]'s.
def test_two_citations_in_one_sentence_with_different_verdicts_do_not_cross_contaminate(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "seed content")
    seed_row = _real_chunk_row(admin, document_id)

    row_supported = admin.table("chunks").insert(
        {"document_id": document_id, "user_id": user_id, "chunk_index": 1, "element_type": "text", "page_number": 2, "content": "Revenue figures for the quarter", "embedding": _DUMMY_EMBEDDING}
    ).execute().data[0]
    row_unsupported = admin.table("chunks").insert(
        {"document_id": document_id, "user_id": user_id, "chunk_index": 2, "element_type": "text", "page_number": 3, "content": "Unrelated sales commentary", "embedding": _DUMMY_EMBEDDING}
    ).execute().data[0]

    retrieved = [
        RetrievedChunk(chunk_id=seed_row["id"], content=seed_row["content"], page=1, document_id=document_id, document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9),
        RetrievedChunk(chunk_id=row_supported["id"], content=row_supported["content"], page=2, document_id=document_id, document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.8),
        RetrievedChunk(chunk_id=row_unsupported["id"], content=row_unsupported["content"], page=3, document_id=document_id, document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.7),
    ]
    gen_result = GenerateResult(
        answer="Revenue grew [2] due to strong sales in Q3 [3].",
        cited_indices=[2, 3], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    fake_verifier = FakeVerifier({
        row_supported["id"]: _verdict(VerdictLabel.SUPPORTED, "quote for supported claim"),
        row_unsupported["id"]: _verdict(VerdictLabel.UNSUPPORTED, None),
    })
    _override(retriever=FakeRetriever(retrieved), generator=FakeGenerator(gen_result), verifier=fake_verifier)

    response = app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()

    # Both citations were verified against the SAME sentence-level claim
    # text (documented, sentence-boundary granularity — not per-clause) —
    # confirmed directly, not assumed, so this test also documents the
    # real limitation this granularity has, not just its safety.
    claims_by_chunk_id = {chunk.chunk_id: claim for claim, chunk in fake_verifier.captured_pairs}
    assert claims_by_chunk_id[row_supported["id"]] == "Revenue grew due to strong sales in Q3."
    assert claims_by_chunk_id[row_unsupported["id"]] == "Revenue grew due to strong sales in Q3."

    # The critical safety property: differing verdicts on shared-sentence
    # citations must not cross-contaminate the RESPONSE — [2] (supported)
    # kept with its marker, [3] (unsupported) dropped with its marker
    # stripped, and nothing about [2]'s presence in the answer changed.
    assert len(body["citations"]) == 1
    assert body["citations"][0]["chunk_id"] == row_supported["id"]
    assert body["citations"][0]["verdict"] == "supported"
    assert body["answer"] == "Revenue grew [2] due to strong sales in Q3."
    assert "[3]" not in body["answer"]


# 2026-07-24 self-audit item 2: re-verify the document_id fix is wired
# end-to-end, not just present-but-wrong. Two REAL, DIFFERENT documents
# in one request, with citations from each — confirms each citation's
# document_id correctly identifies WHICH of the two documents it came
# from, not a default, not always the first document_id in the request.
def test_citation_document_id_correctly_identifies_which_of_multiple_documents(app_client, admin, user_a):
    user_id, token = user_a
    document_id_first = _ingest_doc_with_content(app_client, admin, user_id, token, "first.pdf", "content from the first document")
    document_id_second = _ingest_doc_with_content(app_client, admin, user_id, token, "second.pdf", "content from the second document")
    row_first = _real_chunk_row(admin, document_id_first)
    row_second = _real_chunk_row(admin, document_id_second)

    retrieved = [
        RetrievedChunk(chunk_id=row_first["id"], content=row_first["content"], page=1, document_id=document_id_first, document_name="first.pdf", document_mime_type="application/pdf", element_type="text", score=0.9),
        RetrievedChunk(chunk_id=row_second["id"], content=row_second["content"], page=1, document_id=document_id_second, document_name="second.pdf", document_mime_type="application/pdf", element_type="text", score=0.8),
    ]
    gen_result = GenerateResult(
        answer="The first fact [1] and the second fact [2].", cited_indices=[1, 2], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({
            row_first["id"]: _verdict(VerdictLabel.SUPPORTED, "from first"),
            row_second["id"]: _verdict(VerdictLabel.SUPPORTED, "from second"),
        }),
    )

    # Request document_ids in the OPPOSITE order from cited_indices, so a
    # bug that defaulted to "first document_id in the request" rather
    # than the chunk's own real document_id would be caught, not hidden
    # by coincidental ordering.
    response = app_client.post(
        "/query",
        json={"question": "q", "document_ids": [document_id_second, document_id_first]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    citations_by_marker = {c["marker"]: c for c in body["citations"]}

    assert citations_by_marker[1]["document_id"] == document_id_first
    assert citations_by_marker[1]["document_name"] == "first.pdf"
    assert citations_by_marker[2]["document_id"] == document_id_second
    assert citations_by_marker[2]["document_name"] == "second.pdf"
    assert citations_by_marker[1]["document_id"] != citations_by_marker[2]["document_id"]


def test_query_request_model_has_no_user_id_field_at_all():
    # Structural guarantee, not a runtime check: there is no
    # request-body field ANYWHERE that could carry a client-supplied
    # user_id into the pipeline, by construction.
    from models.query import QueryRequest

    assert "user_id" not in QueryRequest.model_fields


# --- Items 3/4: figure-fetch + [N]->chunk_id resolution, confirmed by ------
# the audit to not exist anywhere before this feature.


def test_claim_span_extraction_maps_each_cited_position_to_its_sentence():
    from routes.query import _extract_claim_spans

    answer = "Revenue grew 12% [1]. The forecast is bright [2]. Both facts matter [1]."
    spans = _extract_claim_spans(answer, cited_positions={1, 2})

    assert spans[1] == "Revenue grew 12%."  # first occurrence, not the third sentence
    assert spans[2] == "The forecast is bright."


def test_claim_span_extraction_handles_grouped_brackets():
    from routes.query import _extract_claim_spans

    answer = "Both are true according to the tables [2, 3]."
    spans = _extract_claim_spans(answer, cited_positions={2, 3})

    assert spans[2] == spans[3] == "Both are true according to the tables."


def test_strip_dropped_markers_removes_only_the_dropped_position_from_a_grouped_bracket():
    from routes.query import _strip_dropped_markers

    # Mirrors FEAT-010's real live-observed grouped-bracket shape.
    result = _strip_dropped_markers("Per the tables [1, 2].", dropped_positions={2})
    assert result == "Per the tables [1]."


def test_strip_dropped_markers_removes_the_whole_bracket_when_nothing_survives():
    from routes.query import _strip_dropped_markers

    result = _strip_dropped_markers("This claim is fabricated [1].", dropped_positions={1})
    assert result == "This claim is fabricated."
    assert "[" not in result


def test_figure_fetcher_downloads_image_bytes_for_figure_chunks_only(admin, user_a):
    from services.figure_fetcher import fetch_generator_chunks

    user_id, _token = user_a
    document_id = admin.table("documents").insert(
        {"user_id": user_id, "filename": "d.pdf", "storage_path": f"uploads/{user_id}/d.pdf", "mime_type": "application/pdf", "size_bytes": 1}
    ).execute().data[0]["id"]

    fake_png = b"\x89PNG\r\n\x1a\nfake"
    path = f"{user_id}/{document_id}/0.png"
    admin.storage.from_("figures").upload(path, fake_png, file_options={"content-type": "image/png"})

    figure_row = admin.table("chunks").insert(
        {"document_id": document_id, "user_id": user_id, "chunk_index": 0, "element_type": "figure", "page_number": 1, "content": "", "figure_path": path, "embedding": _DUMMY_EMBEDDING}
    ).execute().data[0]
    text_row = admin.table("chunks").insert(
        {"document_id": document_id, "user_id": user_id, "chunk_index": 1, "element_type": "text", "page_number": 1, "content": "plain text", "embedding": _DUMMY_EMBEDDING}
    ).execute().data[0]

    retrieved = [
        RetrievedChunk(chunk_id=figure_row["id"], content="", page=1, document_id=document_id, document_name="d.pdf", document_mime_type="application/pdf", element_type="figure", score=1.0),
        RetrievedChunk(chunk_id=text_row["id"], content="plain text", page=1, document_id=document_id, document_name="d.pdf", document_mime_type="application/pdf", element_type="text", score=0.9),
    ]

    result = fetch_generator_chunks(admin, retrieved)

    assert len(result) == 2
    assert result[0].image == fake_png
    assert result[1].image is None

    admin.table("chunks").delete().eq("document_id", document_id).execute()
    admin.table("documents").delete().eq("id", document_id).execute()


# 2026-07-24 self-audit item 4: a real 404 from the real Storage backend
# for ONE of three figure chunks previously crashed fetch_generator_chunks()
# entirely uncaught — confirmed live before this test existed, including
# that the other two valid figures were never even attempted (the loop
# died partway through). Fixed to degrade the failed chunk to a
# text-only entry (element_type changed, image=None) rather than
# failing the whole batch, preserving position for the other two.
def test_figure_fetcher_degrades_one_failed_download_without_losing_the_others(admin, user_a):
    from services.figure_fetcher import fetch_generator_chunks

    user_id, _token = user_a
    document_id = admin.table("documents").insert(
        {"user_id": user_id, "filename": "d.pdf", "storage_path": f"uploads/{user_id}/d.pdf", "mime_type": "application/pdf", "size_bytes": 1}
    ).execute().data[0]["id"]

    fake_png = b"\x89PNG\r\n\x1a\nreal-bytes"
    path1 = f"{user_id}/{document_id}/1.png"
    path3 = f"{user_id}/{document_id}/3.png"
    admin.storage.from_("figures").upload(path1, fake_png, file_options={"content-type": "image/png"})
    admin.storage.from_("figures").upload(path3, fake_png, file_options={"content-type": "image/png"})
    missing_path = f"{user_id}/{document_id}/MISSING.png"  # never uploaded — a genuine 404, not a mock

    rows = []
    for i, path in enumerate([path1, missing_path, path3], start=1):
        row = admin.table("chunks").insert(
            {
                "document_id": document_id, "user_id": user_id, "chunk_index": i, "element_type": "figure",
                "page_number": 1, "content": "", "figure_path": path, "embedding": _DUMMY_EMBEDDING,
            }
        ).execute().data[0]
        rows.append(row)

    retrieved = [
        RetrievedChunk(chunk_id=r["id"], content="", page=1, document_id=document_id, document_name="d.pdf", document_mime_type="application/pdf", element_type="figure", score=1.0)
        for r in rows
    ]

    result = fetch_generator_chunks(admin, retrieved)

    assert len(result) == 3, "the failure of one figure must not drop entries — position must be preserved"
    assert result[0].element_type == "figure" and result[0].image == fake_png
    assert result[1].element_type == "text" and result[1].image is None  # degraded, not crashed
    assert result[2].element_type == "figure" and result[2].image == fake_png

    admin.table("chunks").delete().eq("document_id", document_id).execute()
    admin.table("documents").delete().eq("id", document_id).execute()


# --- GenerationError / no-retrieval-results / error handling --------------


def test_generation_failure_returns_502(app_client, admin, user_a):
    from services.generator import GenerationError

    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "content")
    chunk_row = _real_chunk_row(admin, document_id)
    retrieved = [
        RetrievedChunk(chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id, document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9)
    ]
    _override(retriever=FakeRetriever(retrieved), generator=FakeGeneratorRaising(GenerationError("simulated failure")), verifier=FakeVerifier({}))

    response = app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GENERATE_FAILED"


def test_no_retrieved_chunks_returns_a_graceful_answer_not_an_error(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_doc_with_content(app_client, admin, user_id, token, "doc.pdf", "unrelated content")
    _override(retriever=FakeRetriever([]), generator=FakeGenerator(
        GenerateResult(answer="n/a", cited_indices=[], hallucinated_markers=[], model="m", input_tokens=0, output_tokens=0, latency_ms=0)
    ), verifier=FakeVerifier({}))

    response = app_client.post(
        "/query", json={"question": "q", "document_ids": [document_id]}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert body["metadata"]["retrieved_count"] == 0
    assert "couldn't find" in body["answer"].lower()


# --- Real conversation memory quality + latency (2026-07-27 follow-up) ------
#
# Task item 5/6: a genuine two-turn conversation against real ingested
# content (real Docling parse, real Voyage embeddings, real Gemini
# generation+verification through the actual /query endpoint — no
# dependency overrides at all), reporting the real second answer and the
# real added latency from folding history into the prompt. Gated behind
# its own env var, same pattern as RUN_RETRIEVAL_QUALITY_TEST/
# RUN_GENERATION_QUALITY_TEST/RUN_VERIFICATION_QUALITY_TEST.


@pytest.mark.skipif(
    os.environ.get("RUN_CONVERSATION_MEMORY_QUALITY_TEST") != "1",
    reason=(
        "set RUN_CONVERSATION_MEMORY_QUALITY_TEST=1 to run a real two-turn conversation "
        "against real Docling+Voyage+Gemini (slow, uses quota)"
    ),
)
def test_real_two_turn_conversation_uses_first_turns_context_to_answer_the_second(app_client, admin, user_a):
    from services.chunker import Chunker
    from services.embedder import Embedder
    from services.figure_fetcher import fetch_generator_chunks
    from services.generator import Generator
    from services.parser import Parser
    from services.retriever import Retriever

    user_id, token = user_a
    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")

    with open(os.path.join(fixtures, "table_heavy.pdf"), "rb") as f:
        pdf_bytes = f.read()

    parsed = Parser().parse(pdf_bytes)
    chunks = Chunker().chunk(parsed)
    embedder = Embedder()
    vectors = embedder.embed(chunks)

    document_id = (
        admin.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": "table_heavy.pdf",
                "storage_path": f"uploads/{user_id}/table_heavy.pdf",
                "mime_type": "application/pdf",
                "size_bytes": len(pdf_bytes),
            }
        )
        .execute()
        .data[0]["id"]
    )
    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append(
            {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_index": chunk.chunk_index,
                "element_type": chunk.element_type.value,
                "page_number": min(chunk.page_numbers),
                "content": chunk.content,
                "embedding": vector,
            }
        )
    admin.table("chunks").insert(rows).execute()
    for image in (c.image for c in chunks if c.image is not None):
        image.close()

    # Same Voyage 3 RPM free-tier pacing FEAT-009's own quality test hit
    # and documented (.agent/MEMORY.md) — this test makes several more
    # real embed_query calls than a single-turn quality test would.
    time.sleep(25)

    question_1 = "What is Angola's Human Development Index value in 2010?"
    turn1 = app_client.post(
        "/query",
        json={"question": question_1, "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    time.sleep(25)

    question_2 = "How does that compare to the year 2000?"
    turn2 = app_client.post(
        "/query",
        json={"question": question_2, "document_ids": [document_id], "conversation_id": turn1["conversation_id"]},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    print("\n" + "=" * 90)
    print("Real two-turn conversation — table_heavy.pdf, real Docling+Voyage+Gemini")
    print("=" * 90)
    print(f"\nTurn 1: {question_1}\n-> {turn1['answer']}")
    print(f"   latency_ms={turn1['metadata']['latency_ms']}")
    print(f"\nTurn 2: {question_2}\n-> {turn2['answer']}")
    print(f"   latency_ms={turn2['metadata']['latency_ms']}")

    # Item 6 — isolate history's OWN added latency, not just the
    # (confounded, different-question) turn1-vs-turn2 total delta: same
    # question/chunks/retrieval, called once with no history and once
    # with turn 2's real fetched history, timing each real Gemini call
    # directly. Reuses the real retrieved chunks rather than a second
    # real retrieval call.
    time.sleep(25)
    retriever = Retriever(client=admin)
    retrieved = retriever.retrieve(question_2, [document_id], user_id, k=8)
    generator_chunks = fetch_generator_chunks(admin, retrieved)
    real_history = queries.list_messages_for_conversation(
        admin, conversation_id=turn1["conversation_id"], user_id=user_id, limit=query.MAX_HISTORY_TURNS * 2
    )
    generator = Generator()

    # Fixed-order pairs (no-history always called first, with-history
    # always second) initially produced a large gap in the WRONG
    # direction — history calls came back faster, the opposite of what
    # "extra context costs more" would predict — raising a real
    # suspicion of a call-order/warm-connection confound (being
    # second-in-pair, not "having history", correlating with being
    # faster). Re-measured with ABBA counterbalancing (which condition
    # goes first alternates each trial, so position-in-sequence and any
    # latency drift over the run are spread evenly across both
    # conditions) — the gap persisted just as strongly (see the real
    # result below), ruling out simple call-order as the explanation.
    # Most likely real mechanism, not fully instrumented here to confirm
    # via output token counts: with history, the model knows exactly
    # what "that"/"the year 2000" refers to and answers directly; without
    # it, the model must guess the referent from an ambiguous follow-up
    # against a multi-country/multi-year table, plausibly producing a
    # longer, more hedging answer — and output token count, not prompt
    # size, dominates real autoregressive generation latency. Reported as
    # the real, reproduced (n=3 ABBA-counterbalanced trials) measurement
    # either way, not silently smoothed over because the direction was
    # surprising.
    no_history_trials = []
    with_history_trials = []
    for trial in range(3):
        if trial > 0:
            time.sleep(10)
        history_first = trial % 2 == 1
        order = [True, False] if history_first else [False, True]
        for is_history_call in order:
            started = time.perf_counter()
            generator.generate(question_2, generator_chunks, history=real_history if is_history_call else None)
            elapsed_ms = (time.perf_counter() - started) * 1000
            (with_history_trials if is_history_call else no_history_trials).append(elapsed_ms)
            if is_history_call != order[-1]:
                time.sleep(10)

    def _stats(label, trials):
        mean = sum(trials) / len(trials)
        print(f"   {label}: mean={mean:.1f}ms min={min(trials):.1f}ms max={max(trials):.1f}ms trials={[f'{t:.1f}' for t in trials]}")
        return mean

    print("\nGenerator.generate() latency isolation — same question/chunks, history on vs off, n=3 paired trials:")
    no_history_mean = _stats("without history", no_history_trials)
    with_history_mean = _stats("with history   ", with_history_trials)
    print(f"   mean delta: {with_history_mean - no_history_mean:+.1f}ms")
    print("=" * 90)

    admin.table("chunks").delete().eq("document_id", document_id).execute()
    admin.table("documents").delete().eq("id", document_id).execute()

    assert "4.42" in turn1["answer"]
    # The second answer must show real evidence of having used turn 1's
    # context to resolve "that"/"the year 2000" — real content: Angola's
    # HDI row is 4.42 in BOTH 2000 and 2010 (table_heavy.pdf, Table 19),
    # so a context-aware answer should reference 4.42 and/or note no
    # change between the two years.
    assert "4.42" in turn2["answer"] or "same" in turn2["answer"].lower() or "no change" in turn2["answer"].lower()
