# [FEAT-020] DOCX/PPTX/HTML ingestion — full end-to-end pipeline test.
#
# Same "no fakes anywhere" standard as test_ingest_e2e.py/test_query_e2e.py,
# applied to the three new formats: real Docling parse, real Voyage embed
# (ingest + per-question query embed), real hybrid retrieval, real Gemini
# generation, real Gemini Flash-Lite verification — through the ACTUAL
# /ingest and /query HTTP endpoints, not a direct service call. This is
# task item 5's explicit ask: "not just parser-level unit tests -- same
# standard as every PDF fixture."
#
# Gated behind RUN_FORMAT_INGEST_E2E_TEST=1 (slow — 3x real Docling parse,
# spends real Voyage/Gemini quota), matching the established pattern.
#
# Run explicitly with:
#   RUN_FORMAT_INGEST_E2E_TEST=1 uv run pytest tests/e2e/test_format_ingest_e2e.py -v -s

import os
import time

import pytest
from fastapi.testclient import TestClient

from main import app
from tests._local_supabase import create_test_user, delete_test_user, login, upload_via_rest

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_FORMAT_INGEST_E2E_TEST") != "1",
    reason="set RUN_FORMAT_INGEST_E2E_TEST=1 to run the full real DOCX/PPTX/HTML ingest+query pipeline (slow, uses quota)",
)

# One real, targeted question per format, built from content read directly
# out of each fixture (not assumed from memory of building them) — same
# discipline as FEAT-009's real quality questions. expected_page_number
# is the real, per-format design decision from FEATURES.md's FEAT-020
# entry: DOCX/HTML always report page 1 (Docling gives no page concept
# for these formats at all); PPTX reports the real slide index.
FORMAT_CASES = [
    {
        "fixture": "table.docx",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "question": "What was Q3 2026 revenue?",
        "expect_substring": "1,410,000",
        "expected_page_number": 1,
    },
    {
        "fixture": "slides.pptx",
        "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "question": "What was the customer count in Q3 2026?",
        "expect_substring": "5,200",
        "expected_page_number": 2,  # the real slide index this bullet lives on
    },
    {
        "fixture": "page.html",
        "mime_type": "text/html",
        "question": "What was Q3 2026 revenue?",
        "expect_substring": "1,410,000",
        "expected_page_number": 1,
    },
]


def _real_ingest(client, admin, user_id, token, fixture, mime_type):
    with open(os.path.join(FIXTURES, fixture), "rb") as f:
        file_bytes = f.read()

    upload_resp = upload_via_rest(token, "uploads", f"{user_id}/{fixture}", file_bytes, mime_type)
    assert upload_resp.status_code in (200, 201), f"upload failed: {upload_resp.status_code} {upload_resp.text}"

    response = client.post(
        "/ingest",
        json={
            "storage_path": f"uploads/{user_id}/{fixture}",
            "filename": fixture,
            "mime_type": mime_type,
            "size_bytes": len(file_bytes),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202, f"{response.status_code}: {response.text}"
    document_id = response.json()["document_id"]

    doc_row = admin.table("documents").select("*").eq("id", document_id).execute().data[0]
    assert doc_row["status"] == "ready", f"pipeline did not reach ready — status={doc_row['status']!r} error={doc_row['error']!r}"
    return document_id, doc_row


def test_docx_pptx_html_real_ingest_and_query_through_the_actual_endpoints(admin):
    client = TestClient(app)
    user_id, email = create_test_user(admin)
    token = login(email)

    try:
        print("\n" + "=" * 90)
        print("FEAT-020 real end-to-end — DOCX/PPTX/HTML through the actual /ingest + /query endpoints")
        print("=" * 90)

        all_correct = True
        for i, case in enumerate(FORMAT_CASES):
            if i > 0:
                time.sleep(25)  # real Voyage free-tier pacing between formats' ingest embed() calls

            document_id, doc_row = _real_ingest(client, admin, user_id, token, case["fixture"], case["mime_type"])

            chunk_rows = (
                admin.table("chunks")
                .select("id, element_type, page_number, bbox, content")
                .eq("document_id", document_id)
                .execute()
                .data
            )

            time.sleep(25)  # pacing before the query-side embed() call

            response = client.post(
                "/query",
                json={"question": case["question"], "document_ids": [document_id]},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200, f"{response.status_code}: {response.text}"
            body = response.json()

            answered_correctly = case["expect_substring"] in body["answer"] and len(body["citations"]) > 0
            citation_page_numbers = {c["page_number"] for c in body["citations"]}
            page_number_sensible = citation_page_numbers == {case["expected_page_number"]}

            print(f"\n--- {case['fixture']} ---")
            print(f"document status: {doc_row['status']}, page_count: {doc_row['page_count']}, chunks: {len(chunk_rows)}")
            print(f"chunk page_numbers seen: {sorted({c['page_number'] for c in chunk_rows})}")
            print(f"Q: {case['question']}")
            print(f"-> {body['answer']!r}")
            print(f"   citations: {[(c['page_number'], c['element_type'], c['verdict']) for c in body['citations']]}")
            print(f"   -> answer {'PASS' if answered_correctly else 'FAIL'}, citation page_number {'PASS' if page_number_sensible else 'FAIL'} (expected {case['expected_page_number']}, got {citation_page_numbers})")

            all_correct = all_correct and answered_correctly and page_number_sensible

            admin.table("citations").delete().eq("user_id", user_id).execute()
            admin.table("messages").delete().eq("conversation_id", body["conversation_id"]).execute()
            admin.table("conversations").delete().eq("id", body["conversation_id"]).execute()
            admin.table("chunks").delete().eq("document_id", document_id).execute()
            admin.table("documents").delete().eq("id", document_id).execute()

        print("\n" + "=" * 90)
        print(f"Overall: {'ALL' if all_correct else 'NOT ALL'} formats answered correctly with a sensible citation page_number")
        print("=" * 90)

        assert all_correct
    finally:
        delete_test_user(admin, user_id)
