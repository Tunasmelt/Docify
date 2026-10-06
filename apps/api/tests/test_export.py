# Tests for Settings batch 3, part 1: GET /export/conversations
# (routes/export.py). Same real-infrastructure discipline as
# test_conversations.py: real local Supabase, Retriever/Generator/
# Verifier faked via dependency overrides to drive real POST /query
# turns that persist real conversations/messages/citations, never a
# hand-built fixture standing in for one.
#
# Priority per the task brief: completeness. An export that silently
# omits a message or citation is worse than no export at all, since it
# creates false confidence right before an account might be deleted.
# The tests below cross-check the export's contents against direct
# admin (service-role) reads of the same rows, not just against what
# the seeding helper *intended* to create -- if seeding itself silently
# under-created something, comparing export-vs-seeding-intent would
# pass vacuously; comparing export-vs-real-DB-state cannot.

import json

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


def _verdict(label, quote="a supporting quote"):
    return Verdict(
        verdict=label,
        # Same real shape create_query_turn/Verifier itself produces --
        # an UNSUPPORTED verdict never carries a quote.
        quote=quote if label != VerdictLabel.UNSUPPORTED else None,
        model="gemini-3.5-flash-lite",
        input_tokens=10,
        output_tokens=5,
        latency_ms=100.0,
    )


def _ask_real_question(
    app_client, admin, user_id, token, document_id, chunk_row, answer, verdict_label, conversation_id=None
):
    """Drives one real POST /query turn end to end (real retrieval/
    generation/verification fakes, real persisted conversation/message/
    citation via the real endpoint) -- same pattern as
    test_conversations.py's own helper, parameterized by verdict label
    so a single seeding call can cover any of the four real verdicts
    (supported/partial/unverified/unsupported)."""
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


def _real_message_and_citation_counts(admin, user_id) -> tuple[int, int]:
    """Ground truth, read directly with the service-role client --
    independent of both the seeding helper's own intent and the export
    endpoint under test, so a completeness check against these numbers
    can't pass vacuously."""
    messages = admin.table("messages").select("id").eq("user_id", user_id).execute().data
    citations = admin.table("citations").select("id").eq("user_id", user_id).execute().data
    return len(messages), len(citations)


# Acceptance criterion (priority): the export contains every message and
# every citation that actually exists in the DB for the user, across all
# four real verdict labels -- including 'unsupported', which the live
# chat UI/GET /conversations/{id}/messages deliberately never shows.
def test_export_json_is_complete_across_all_verdict_labels(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="lease.pdf")
    chunk_row = (
        admin.table("chunks")
        .select("id,document_id,element_type,page_number,content")
        .eq("document_id", document_id)
        .execute()
        .data[0]
    )

    # One conversation, one turn per real verdict label -- the exact
    # dimension the task brief calls out ("supported/partial/unverified
    # -- the state added earlier this session").
    result_supported = _ask_real_question(
        app_client, admin, user_id, token, document_id, chunk_row, "Rent is $2000/mo [1].", VerdictLabel.SUPPORTED
    )
    conv_id = result_supported["conversation_id"]
    _ask_real_question(
        app_client, admin, user_id, token, document_id, chunk_row, "The lease is likely renewable [1].",
        VerdictLabel.PARTIAL, conversation_id=conv_id,
    )
    _ask_real_question(
        app_client, admin, user_id, token, document_id, chunk_row, "Pets may be allowed [1].",
        VerdictLabel.UNVERIFIED, conversation_id=conv_id,
    )
    # A second, separate conversation carrying the fourth verdict --
    # confirms completeness spans multiple conversations, not just
    # multiple turns within one.
    result_unsupported = _ask_real_question(
        app_client, admin, user_id, token, document_id, chunk_row, "The building has a pool [1].",
        VerdictLabel.UNSUPPORTED,
    )
    conv_id_2 = result_unsupported["conversation_id"]

    real_message_count, real_citation_count = _real_message_and_citation_counts(admin, user_id)
    # Sanity on the ground truth itself: 4 turns x 2 messages = 8;
    # UNSUPPORTED is still persisted (create_query_turn's own
    # full-audit-trail behavior), so all 4 citations exist too.
    assert real_message_count == 8
    assert real_citation_count == 4

    response = app_client.get("/export/conversations?format=json", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = json.loads(response.content)

    assert body["conversation_count"] == 2
    assert body["message_count"] == real_message_count
    assert body["citation_count"] == real_citation_count

    exported_conv_ids = {c["id"] for c in body["conversations"]}
    assert exported_conv_ids == {conv_id, conv_id_2}

    all_exported_messages = [m for c in body["conversations"] for m in c["messages"]]
    assert len(all_exported_messages) == real_message_count

    all_exported_citations = [cit for c in body["conversations"] for m in c["messages"] for cit in m["citations"]]
    assert len(all_exported_citations) == real_citation_count
    exported_verdicts = {cit["verdict"] for cit in all_exported_citations}
    assert exported_verdicts == {"supported", "partial", "unverified", "unsupported"}

    # UNSUPPORTED specifically: dropped from the live-UI message.content
    # (routes/query.py's _strip_dropped_markers) AND from
    # list_citations_for_messages()'s own live query -- prove the
    # export's completeness guarantee is real, not inherited from a
    # query that already filters it.
    unsupported_citation = next(cit for cit in all_exported_citations if cit["verdict"] == "unsupported")
    assert unsupported_citation["supporting_quote"] is None  # real shape, not a placeholder
    assert unsupported_citation["verifier_model"] == "gemini-3.5-flash-lite"
    assert unsupported_citation["claim_span"]  # real, non-empty claim text carried through

    # Every message's real DB content byte-for-byte in the export --
    # not a sample, the actual full set, per the task's own bar.
    real_message_rows = admin.table("messages").select("id,content").eq("user_id", user_id).execute().data
    real_contents = {m["content"] for m in real_message_rows}
    exported_contents = {m["content"] for m in all_exported_messages}
    assert exported_contents == real_contents


# Acceptance criterion (priority): user B's export must not contain ANY
# of user A's data -- verified by real content inspection, not a row
# count, which could pass even if isolation silently mixed user A's
# and B's rows into the same counts.
def test_export_is_scoped_to_the_requesting_user_by_content_not_just_count(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b

    doc_a = ingest_real_document(app_client, user_id_a, token_a, filename="user-a-only.pdf")
    chunk_a = (
        admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", doc_a).execute().data[0]
    )
    result_a = _ask_real_question(
        app_client, admin, user_id_a, token_a, doc_a, chunk_a,
        "A secret only user A should ever see [1].", VerdictLabel.SUPPORTED,
    )

    doc_b = ingest_real_document(app_client, user_id_b, token_b, filename="user-b-only.pdf")
    chunk_b = (
        admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", doc_b).execute().data[0]
    )
    result_b = _ask_real_question(
        app_client, admin, user_id_b, token_b, doc_b, chunk_b,
        "A secret only user B should ever see [1].", VerdictLabel.SUPPORTED,
    )

    export_a = json.loads(
        app_client.get("/export/conversations?format=json", headers={"Authorization": f"Bearer {token_a}"}).content
    )
    export_b = json.loads(
        app_client.get("/export/conversations?format=json", headers={"Authorization": f"Bearer {token_b}"}).content
    )

    raw_a = json.dumps(export_a)
    raw_b = json.dumps(export_b)

    # Content inspection, not row counts -- the actual real strings/ids
    # from each user's turn must never cross into the other's export.
    assert result_a["conversation_id"] in raw_a
    assert result_a["conversation_id"] not in raw_b
    assert result_b["conversation_id"] in raw_b
    assert result_b["conversation_id"] not in raw_a

    assert "A secret only user A should ever see" in raw_a
    assert "A secret only user A should ever see" not in raw_b
    assert "A secret only user B should ever see" in raw_b
    assert "A secret only user B should ever see" not in raw_a

    assert "user-a-only.pdf" in raw_a
    assert "user-a-only.pdf" not in raw_b
    assert "user-b-only.pdf" in raw_b
    assert "user-b-only.pdf" not in raw_a

    assert export_a["conversation_count"] == 1
    assert export_b["conversation_count"] == 1


# Acceptance criterion: Markdown format is also complete (same citation
# set, including 'unsupported'), renders citations as the app's own
# footnote motif, and is equally user-scoped.
def test_export_markdown_is_complete_and_renders_citations_as_footnotes(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="report.pdf")
    chunk_row = (
        admin.table("chunks").select("id,document_id,element_type,page_number,content").eq("document_id", document_id).execute().data[0]
    )

    _ask_real_question(
        app_client, admin, user_id, token, document_id, chunk_row, "Revenue grew 12% [1].", VerdictLabel.SUPPORTED
    )
    _ask_real_question(
        app_client, admin, user_id, token, document_id, chunk_row, "Costs may have risen too [1].",
        VerdictLabel.UNSUPPORTED,
    )

    response = app_client.get("/export/conversations?format=markdown", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/markdown")
    text = response.content.decode("utf-8")

    assert "# Docify Conversation Export" in text
    assert "report.pdf" in text
    # Both real answers present -- raw_content, so the UNSUPPORTED
    # citation's own [1] marker is still visible in context (it would be
    # stripped from the UI-facing `content` field).
    assert "Revenue grew 12%" in text
    assert "Costs may have risen too" in text
    # Footnote-motif Sources block, both real verdicts represented
    # explicitly -- including unsupported, which the live app never
    # shows the user at all.
    assert "**Sources**" in text
    assert "**supported**" in text
    assert "**unsupported**" in text
    assert "report.pdf, p." in text


def test_export_content_disposition_and_default_format(app_client, admin, user_a):
    user_id, token = user_a
    # No conversations at all yet -- an empty, still well-formed export
    # (zero of everything) must not error.
    response = app_client.get("/export/conversations", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")  # default format
    assert "attachment" in response.headers["content-disposition"]
    assert ".json" in response.headers["content-disposition"]
    body = json.loads(response.content)
    assert body == {
        "exported_at": body["exported_at"],
        "conversation_count": 0,
        "message_count": 0,
        "citation_count": 0,
        "conversations": [],
    }

    md_response = app_client.get(
        "/export/conversations?format=markdown", headers={"Authorization": f"Bearer {token}"}
    )
    assert md_response.status_code == 200
    assert md_response.headers["content-type"].startswith("text/markdown")
    assert ".md" in md_response.headers["content-disposition"]


def test_export_rejects_invalid_format(app_client, admin, user_a):
    user_id, token = user_a
    response = app_client.get("/export/conversations?format=csv", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422


def test_export_requires_auth(app_client):
    response = app_client.get("/export/conversations")
    assert response.status_code == 401


class _PassthroughRewriter:
    """Follow-up questions are searched unchanged — no real Gemini rewrite call."""

    def rewrite(self, question, history):
        return question

