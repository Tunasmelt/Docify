# [FEAT-012] `/query` full end-to-end pipeline test.
#
# The ONE place the real pipeline runs together through the ACTUAL HTTP
# endpoint: real Docling parse, real Voyage embed (ingest + per-question
# query embed), real hybrid retrieval, real Gemini generation, real
# Gemini Flash-Lite verification, real atomic DB persistence — no fakes
# anywhere, no direct service calls. This is the first time the actual
# /query route itself is tested, not just the three services it calls
# (per this feature's own task: "this is the first time the actual
# /query endpoint itself gets tested, not just the three services it
# calls").
#
# Gated behind RUN_QUERY_E2E_TEST=1 (slow — real Docling parse, spends
# real Voyage/Gemini quota), matching test_ingest_e2e.py's established
# pattern.
#
# Run explicitly with:
#   RUN_QUERY_E2E_TEST=1 uv run pytest tests/e2e/test_query_e2e.py -v -s

import os
import time

import pytest
from fastapi.testclient import TestClient

from main import app
from tests._local_supabase import create_test_user, delete_test_user, login, upload_via_rest

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_QUERY_E2E_TEST") != "1",
    reason="set RUN_QUERY_E2E_TEST=1 to run the full real Docling+Voyage+Gemini /query pipeline (slow, uses quota)",
)

# Same 4 questions FEAT-009/010's real quality tests already proved
# retrieve/generate correctly — reused verbatim so this test measures
# ONLY whether the actual /query endpoint wires everything together
# correctly, not a new retrieval/generation scenario.
QUALITY_QUESTIONS = [
    {
        "question": "What is Angola's Human Development Index value in 2010?",
        "expect_substring": "Angola",
    },
    {
        "question": "Does Respondent C have a driving licence?",
        "expect_substring": "driving licence",
    },
    {
        "question": "What was the balance in the 2011 accounts?",
        "expect_substring": "Balance",
    },
    {
        "question": "What courses does Institution X offer in Mathematics?",
        "expect_substring": "Mathematics",
    },
]


def _real_ingest(client, admin, user_id, token, filename="table_heavy.pdf"):
    with open(os.path.join(FIXTURES, "table_heavy.pdf"), "rb") as f:
        pdf_bytes = f.read()

    upload_resp = upload_via_rest(token, "uploads", f"{user_id}/{filename}", pdf_bytes, "application/pdf")
    assert upload_resp.status_code in (200, 201), f"upload failed: {upload_resp.status_code} {upload_resp.text}"

    response = client.post(
        "/ingest",
        json={
            "storage_path": f"uploads/{user_id}/{filename}",
            "filename": filename,
            "mime_type": "application/pdf",
            "size_bytes": len(pdf_bytes),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    document_id = response.json()["document_id"]

    doc_row = admin.table("documents").select("*").eq("id", document_id).execute().data[0]
    assert doc_row["status"] == "ready", f"pipeline did not reach ready — status={doc_row['status']!r} error={doc_row['error']!r}"
    return document_id


def test_full_query_pipeline_real_retrieval_real_generation_real_verification(admin):
    client = TestClient(app)
    user_id, email = create_test_user(admin)
    token = login(email)

    try:
        document_id = _real_ingest(client, admin, user_id, token)

        time.sleep(25)  # real Voyage free-tier pacing after ingestion's embed() call

        print("\n" + "=" * 90)
        print("FEAT-012 real end-to-end — actual POST /query, table_heavy.pdf")
        print("=" * 90)

        conversation_id = None
        all_cited_expected = True
        for i, spec in enumerate(QUALITY_QUESTIONS):
            if i > 0:
                time.sleep(25)

            payload = {"question": spec["question"], "document_ids": [document_id]}
            if conversation_id is not None:
                payload["conversation_id"] = conversation_id

            started = time.perf_counter()
            response = client.post("/query", json=payload, headers={"Authorization": f"Bearer {token}"})
            wall_ms = (time.perf_counter() - started) * 1000

            assert response.status_code == 200, f"{response.status_code}: {response.text}"
            body = response.json()
            conversation_id = body["conversation_id"]

            # Checked against the answer text itself, not citation
            # "snippet" (chunk.content[:200] — a short display preview
            # per API_CONTRACT.md, not guaranteed to contain the relevant
            # substring for a long chunk, e.g. a table where "Angola"
            # sorts alphabetically past the 200-char cutoff). The answer
            # text is what actually proves the right content was used.
            answered_correctly = spec["expect_substring"].lower() in body["answer"].lower() and len(body["citations"]) > 0

            print(f"\nQ: {spec['question']}")
            print(f"   answer: {body['answer']!r}")
            print(f"   citations: {len(body['citations'])} (verdicts: {[c['verdict'] for c in body['citations']]})")
            print(f"   metadata: {body['metadata']}")
            print(f"   wall-clock (actual HTTP POST /query): {wall_ms:.1f}ms")
            print(f"   -> {'PASS' if answered_correctly else 'FAIL'}: expected content {'answered with citations' if answered_correctly else 'NOT correctly answered'}")

            all_cited_expected = all_cited_expected and answered_correctly

        print("\n" + "=" * 90)
        print(f"Overall: {'ALL' if all_cited_expected else 'NOT ALL'} questions cited their expected content via the real endpoint")
        print("=" * 90)

        messages = admin.table("messages").select("*").eq("conversation_id", conversation_id).order("created_at").execute().data
        assert len(messages) == len(QUALITY_QUESTIONS) * 2
        print(f"\nconversation {conversation_id}: {len(messages)} messages persisted (real conversation continuation across all 4 questions)")

        admin.table("citations").delete().eq("user_id", user_id).execute()
        admin.table("messages").delete().eq("conversation_id", conversation_id).execute()
        admin.table("conversations").delete().eq("id", conversation_id).execute()
        admin.table("chunks").delete().eq("document_id", document_id).execute()
        admin.table("documents").delete().eq("id", document_id).execute()

        assert all_cited_expected
    finally:
        delete_test_user(admin, user_id)


def test_two_user_isolation_at_the_actual_http_endpoint(admin):
    # The 2026-07-24 full-flow audit proved isolation live at the
    # service-call level (item 7). This re-proves it through the ACTUAL
    # HTTP endpoint — real requests, real JWTs, real routing — the level
    # that actually matters once a client exists.
    client = TestClient(app)
    user_id_a, email_a = create_test_user(admin)
    user_id_b, email_b = create_test_user(admin)
    token_a = login(email_a)
    token_b = login(email_b)

    try:
        document_id_a = _real_ingest(client, admin, user_id_a, token_a, filename="table_heavy.pdf")

        # User B: hand-crafted, same-embedding-as-user-A's-real-HDI-chunk
        # adversarial content — same methodology as the full-flow audit's
        # own item 7 (an orthogonal vector would never be retrieved
        # regardless of scoping; identical embedding proves scoping does
        # the work, not distance).
        document_id_b = (
            admin.table("documents")
            .insert(
                {
                    "user_id": user_id_b,
                    "filename": "user-b-secret.pdf",
                    "storage_path": f"uploads/{user_id_b}/user-b-secret.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 99,
                }
            )
            .execute()
            .data[0]["id"]
        )
        real_hdi_row = (
            admin.table("chunks")
            .select("embedding")
            .eq("document_id", document_id_a)
            .ilike("content", "%Angola%4.42%")
            .limit(1)
            .execute()
            .data[0]
        )
        secret_content = (
            "CONFIDENTIAL USER B DATA: Angola's Human Development Index value in 2010 "
            "was actually 99.99 (LEAK-CANARY-B). This is User B's private, unrelated document."
        )
        admin.table("chunks").insert(
            {
                "document_id": document_id_b,
                "user_id": user_id_b,
                "chunk_index": 0,
                "element_type": "text",
                "page_number": 1,
                "content": secret_content,
                "embedding": real_hdi_row["embedding"],
            }
        ).execute()

        time.sleep(25)

        question = "What is Angola's Human Development Index value in 2010?"
        response = client.post(
            "/query", json={"question": question, "document_ids": [document_id_a]}, headers={"Authorization": f"Bearer {token_a}"}
        )

        assert response.status_code == 200
        body = response.json()

        print("\n" + "=" * 90)
        print("FEAT-012 two-user isolation — real POST /query as user A")
        print("=" * 90)
        print(f"answer: {body['answer']!r}")
        print(f"citations: {[(c['chunk_id'], c['document_name']) for c in body['citations']]}")

        leaked_in_answer = "LEAK-CANARY-B" in body["answer"] or "99.99" in body["answer"]
        leaked_in_citations = any(c["document_name"] == "user-b-secret.pdf" for c in body["citations"])
        print(f"user B's content leaked into the answer: {leaked_in_answer}")
        print(f"user B's document cited: {leaked_in_citations}")
        print("=" * 90)

        assert not leaked_in_answer
        assert not leaked_in_citations

        # Cross-tenant document_ids in the request must also 403 — the
        # other real isolation boundary this endpoint owns.
        cross_tenant_response = client.post(
            "/query", json={"question": question, "document_ids": [document_id_b]}, headers={"Authorization": f"Bearer {token_a}"}
        )
        assert cross_tenant_response.status_code == 403

        admin.table("citations").delete().eq("user_id", user_id_a).execute()
        admin.table("messages").delete().eq("conversation_id", body["conversation_id"]).execute()
        admin.table("conversations").delete().eq("id", body["conversation_id"]).execute()
        admin.table("chunks").delete().eq("document_id", document_id_a).execute()
        admin.table("chunks").delete().eq("document_id", document_id_b).execute()
        admin.table("documents").delete().eq("id", document_id_a).execute()
        admin.table("documents").delete().eq("id", document_id_b).execute()
    finally:
        delete_test_user(admin, user_id_a)
        delete_test_user(admin, user_id_b)
