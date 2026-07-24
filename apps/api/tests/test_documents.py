# Tests for [FEAT-008] `/documents` list + detail + delete
#
# Same discipline as FEAT-007: real local Supabase Auth+DB+Storage
# throughout, Docling/Voyage faked for speed. Every test that needs a
# document ingests it through the real /ingest endpoint via
# conftest.py's ingest_real_document() helper — never a hand-inserted
# row — so GET/DELETE are exercised against data that actually went
# through the real pipeline.

from unittest.mock import patch

import pytest
from PIL import Image

import routes.documents as documents_module
from services.chunker import Chunk
from services.parser import ElementType
from tests.conftest import fake_elements, ingest_real_document


# Acceptance criterion: GET /documents returns paginated list scoped to JWT user
def test_get_documents_returns_paginated_list_scoped_to_jwt_user(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    _, token_b = user_b

    doc_ids_a = [
        ingest_real_document(app_client, user_id_a, token_a, filename=f"doc-{i}.pdf") for i in range(3)
    ]
    doc_id_b = ingest_real_document(app_client, *user_b, filename="other-user-doc.pdf")

    # Full list scoped to user A only — user B's document never appears.
    response = app_client.get("/documents", headers={"Authorization": f"Bearer {token_a}"})
    assert response.status_code == 200
    body = response.json()
    returned_ids = {d["id"] for d in body["documents"]}
    assert set(doc_ids_a) <= returned_ids
    assert doc_id_b not in returned_ids

    # Response shape matches API_CONTRACT.md's GET /documents/{id} shape.
    sample = body["documents"][0]
    assert set(sample.keys()) == {
        "id", "filename", "page_count", "status", "error", "created_at", "parsed_at", "embedded_at"
    }

    # Pagination: limit=2 across 3+ documents must page correctly with no
    # duplicates and no omissions, using real keyset cursors end to end.
    page1 = app_client.get("/documents?limit=2", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert len(page1["documents"]) == 2
    assert page1["next_cursor"] is not None

    page2 = app_client.get(
        f"/documents?limit=2&cursor={page1['next_cursor']}", headers={"Authorization": f"Bearer {token_a}"}
    ).json()
    assert len(page2["documents"]) >= 1

    page1_ids = {d["id"] for d in page1["documents"]}
    page2_ids = {d["id"] for d in page2["documents"]}
    assert page1_ids.isdisjoint(page2_ids), "pagination returned an overlapping document across pages"
    assert doc_ids_a[0] in (page1_ids | page2_ids)
    assert doc_ids_a[-1] in (page1_ids | page2_ids)

    # status filter
    ready_only = app_client.get("/documents?status=ready", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert all(d["status"] == "ready" for d in ready_only["documents"])

    # Invalid query params are rejected cleanly, not left to surface a
    # raw Postgres/PostgREST error.
    bad_status = app_client.get("/documents?status=not_a_real_status", headers={"Authorization": f"Bearer {token_a}"})
    assert bad_status.status_code == 422
    bad_cursor = app_client.get("/documents?cursor=not-valid-base64!!!", headers={"Authorization": f"Bearer {token_a}"})
    assert bad_cursor.status_code == 422


def test_get_document_returns_full_metadata_for_owner(app_client, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="detail.pdf")

    response = app_client.get(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document_id
    assert body["filename"] == "detail.pdf"
    assert body["status"] == "ready"
    assert body["error"] is None
    assert body["page_count"] == 2  # default fake_elements() produces 2 elements/chunks
    assert body["created_at"]
    assert body["parsed_at"]
    assert body["embedded_at"]


# Acceptance criterion: GET /documents/{id} returns 404 for another user's doc
def test_get_documents_id_returns_404_for_another_user_s_doc(app_client, user_a, user_b):
    user_id_a, token_a = user_a
    _, token_b = user_b
    document_id = ingest_real_document(app_client, user_id_a, token_a, filename="private.pdf")

    as_owner = app_client.get(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert as_owner.status_code == 200  # positive control — the doc genuinely exists and is fetchable

    as_other_user = app_client.get(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token_b}"})
    genuinely_nonexistent = app_client.get(
        "/documents/00000000-0000-0000-0000-000000000000", headers={"Authorization": f"Bearer {token_b}"}
    )

    # Same status code AND same body for "belongs to someone else" and
    # "doesn't exist at all" — no 403-vs-404 distinction to leak
    # existence, matching FEAT-007's storage_path hardening discipline.
    assert as_other_user.status_code == 404
    assert genuinely_nonexistent.status_code == 404
    assert as_other_user.json() == genuinely_nonexistent.json()


# Acceptance criterion: DELETE /documents/{id} cascades to chunks, conversations reference, storage files
def test_delete_documents_id_cascades_to_chunks_conversations_referen(app_client, admin, user_a):
    user_id, token = user_a

    figure_image = Image.new("RGB", (10, 10), color="blue")
    elements = fake_elements(2)
    chunks = [
        Chunk(chunk_index=0, element_type=ElementType.TEXT, page_numbers=[1], source_element_indices=[0], content="body text"),
        Chunk(
            chunk_index=1,
            element_type=ElementType.FIGURE,
            page_numbers=[2],
            source_element_indices=[1],
            content="",
            image=figure_image,
        ),
    ]
    document_id = ingest_real_document(app_client, user_id, token, filename="cascade.pdf", elements=elements, chunks=chunks)

    chunk_rows = admin.table("chunks").select("id,figure_path").eq("document_id", document_id).execute().data
    assert len(chunk_rows) == 2
    figure_path = next(r["figure_path"] for r in chunk_rows if r["figure_path"] is not None)

    # Confirm the storage objects genuinely exist before deletion — the
    # test would be meaningless if it only ever asserted "still absent"
    # without first proving "was present".
    admin.storage.from_("uploads").download(f"{user_id}/cascade.pdf")
    admin.storage.from_("figures").download(figure_path)

    conversation = (
        admin.table("conversations")
        .insert({"user_id": user_id, "title": "test conversation", "document_ids": [document_id]})
        .execute()
        .data[0]
    )

    response = app_client.delete(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204
    assert response.content == b""

    # documents row gone.
    assert admin.table("documents").select("id").eq("id", document_id).execute().data == []

    # chunks cascade-deleted at the DB level.
    assert admin.table("chunks").select("id").eq("document_id", document_id).execute().data == []

    # Storage objects actually removed — checked directly against the
    # bucket, not inferred from "the delete call didn't error".
    with pytest.raises(Exception):
        admin.storage.from_("uploads").download(f"{user_id}/cascade.pdf")
    with pytest.raises(Exception):
        admin.storage.from_("figures").download(figure_path)

    # conversation reference cleaned up (array column, not FK-cascaded).
    refreshed_conversation = admin.table("conversations").select("document_ids").eq("id", conversation["id"]).execute().data[0]
    assert document_id not in refreshed_conversation["document_ids"]

    admin.table("conversations").delete().eq("id", conversation["id"]).execute()


# Acceptance criterion: DELETE while status='parsing' returns 409
def test_delete_while_status_parsing_returns_409(app_client, admin, user_a):
    user_id, token = user_a
    # A document genuinely stuck mid-parse isn't reproducible through the
    # real endpoint under TestClient (the background task runs to
    # completion synchronously before the HTTP call returns) — inserted
    # directly with status='parsing' to exercise this specific state,
    # the one deliberate exception to this file's "always go through
    # /ingest" rule.
    document = (
        admin.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": "in-progress.pdf",
                "storage_path": f"uploads/{user_id}/in-progress.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 17,
                "status": "parsing",
            }
        )
        .execute()
        .data[0]
    )

    response = app_client.delete(f"/documents/{document['id']}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"

    # Document must still exist — the delete was refused, not partially applied.
    assert admin.table("documents").select("id").eq("id", document["id"]).execute().data != []

    admin.table("documents").delete().eq("id", document["id"]).execute()


# 'embedded' still has a real in-flight background task (figure upload +
# the bulk chunks insert + mark_ready all happen strictly after
# mark_embedded() sets this status — routes/ingest.py's run_ingest_pipeline)
# — deleting the documents row out from under that window let the
# still-running task's later insert_chunks() call fail with a dangling
# chunks_document_id_fkey violation, confirmed live during FEAT-014's UI
# wiring pass (.agent/GAPS.md). Same reasoning and the same TestClient
# limitation as the 'parsing' test above (the real pipeline runs
# synchronously to completion under TestClient, so 'embedded' is inserted
# directly rather than reproduced through /ingest).
def test_delete_while_status_embedded_returns_409(app_client, admin, user_a):
    user_id, token = user_a
    document = (
        admin.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": "mid-embed.pdf",
                "storage_path": f"uploads/{user_id}/mid-embed.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 17,
                "status": "embedded",
            }
        )
        .execute()
        .data[0]
    )

    response = app_client.delete(f"/documents/{document['id']}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"

    # Document must still exist — the delete was refused, not partially applied.
    assert admin.table("documents").select("id").eq("id", document["id"]).execute().data != []

    admin.table("documents").delete().eq("id", document["id"]).execute()


# Multi-tenant isolation across all three endpoints (task item 4) — same
# live-verification discipline as FEAT-007: two real users, confirm user
# B cannot list, get, or delete user A's document via any of the three
# endpoints, with a positive control proving user A's own access works.
def test_multi_tenant_isolation_across_list_get_delete(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    document_id = ingest_real_document(app_client, user_id_a, token_a, filename="isolated.pdf")

    # Positive control.
    assert app_client.get("/documents", headers={"Authorization": f"Bearer {token_a}"}).status_code == 200
    list_as_a = app_client.get("/documents", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert document_id in {d["id"] for d in list_as_a["documents"]}
    assert app_client.get(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token_a}"}).status_code == 200

    # user B: list never includes A's document.
    list_as_b = app_client.get("/documents", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert document_id not in {d["id"] for d in list_as_b["documents"]}

    # user B: direct GET by id is a 404, not a 403 (no existence leak).
    get_as_b = app_client.get(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert get_as_b.status_code == 404

    # user B: DELETE is also a 404, and critically, does not actually
    # delete user A's document.
    delete_as_b = app_client.delete(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert delete_as_b.status_code == 404

    still_there = admin.table("documents").select("id").eq("id", document_id).execute().data
    assert len(still_there) == 1, "user B's rejected DELETE must not have removed user A's document"


# --- Storage-failure handling regression tests, 2026-07-23 ------------------
#
# Self-verification of FEAT-008 found delete_document had no error
# handling around its two Storage .remove() calls — a partial Storage
# failure surfaced as a bare unhandled 500 with no error envelope and no
# log line, even though the underlying retry-safety was already proven
# correct live (the document row survives, a retry succeeds cleanly with
# no corruption). These tests turn that one-off live check into a
# permanent regression test.


class _FailingBucket:
    def __init__(self, real_bucket):
        self._real = real_bucket

    def __getattr__(self, name):
        return getattr(self._real, name)

    def remove(self, paths):
        raise RuntimeError("simulated Storage outage")


class _StorageProxyWithOneFailingBucket:
    def __init__(self, real_storage, fail_bucket):
        self._real = real_storage
        self._fail_bucket = fail_bucket

    def from_(self, bucket):
        real_bucket = self._real.from_(bucket)
        return _FailingBucket(real_bucket) if bucket == self._fail_bucket else real_bucket


class _PartiallyFailingStorageClient:
    """Wraps a real Supabase client: every call (.table(), .storage.from_
    for any other bucket) proxies straight through to the real client
    unchanged — only .storage.from_(fail_bucket).remove() always raises,
    simulating a Storage-layer outage confined to one bucket."""

    def __init__(self, real_client, fail_bucket: str):
        self._real = real_client
        self._fail_bucket = fail_bucket

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def storage(self):
        return _StorageProxyWithOneFailingBucket(self._real.storage, self._fail_bucket)


def _ingest_document_with_figure(app_client, user_id, token, filename):
    figure_image = Image.new("RGB", (10, 10), color="orange")
    elements = fake_elements(2)
    chunks = [
        Chunk(chunk_index=0, element_type=ElementType.TEXT, page_numbers=[1], source_element_indices=[0], content="body"),
        Chunk(
            chunk_index=1,
            element_type=ElementType.FIGURE,
            page_numbers=[2],
            source_element_indices=[1],
            content="",
            image=figure_image,
        ),
    ]
    return ingest_real_document(app_client, user_id, token, filename=filename, elements=elements, chunks=chunks)


def test_delete_returns_storage_error_envelope_when_figures_removal_fails(app_client, admin, user_a, caplog):
    user_id, token = user_a
    document_id = _ingest_document_with_figure(app_client, user_id, token, "storage-fail.pdf")
    failing_client = _PartiallyFailingStorageClient(admin, fail_bucket="figures")

    with caplog.at_level("ERROR"):
        with patch.object(documents_module, "get_service_role_client", return_value=failing_client):
            response = app_client.delete(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "STORAGE_ERROR"
    assert "retry" in body["error"]["message"].lower()

    error_messages = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert any(document_id in m and user_id in m and "bucket=figures" in m for m in error_messages)
    # Coarse context only — never the raw exception text, which isn't
    # guaranteed not to embed a path (same discipline as FEAT-007's
    # StoragePathError logging fix).
    assert not any("simulated Storage outage" in m for m in error_messages)

    # Document and its chunks are provably untouched by the failed attempt.
    assert len(admin.table("documents").select("id").eq("id", document_id).execute().data) == 1
    assert len(admin.table("chunks").select("id").eq("document_id", document_id).execute().data) == 2

    # cleanup
    figure_path = next(
        r["figure_path"]
        for r in admin.table("chunks").select("figure_path").eq("document_id", document_id).execute().data
        if r["figure_path"] is not None
    )
    admin.table("documents").delete().eq("id", document_id).execute()  # cascades chunks
    admin.storage.from_("uploads").remove([f"{user_id}/storage-fail.pdf"])
    admin.storage.from_("figures").remove([figure_path])


def test_delete_retry_after_partial_storage_failure_succeeds(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _ingest_document_with_figure(app_client, user_id, token, "retry.pdf")
    figure_path = next(
        r["figure_path"]
        for r in admin.table("chunks").select("figure_path").eq("document_id", document_id).execute().data
        if r["figure_path"] is not None
    )
    failing_client = _PartiallyFailingStorageClient(admin, fail_bucket="figures")

    with patch.object(documents_module, "get_service_role_client", return_value=failing_client):
        first_response = app_client.delete(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})
    assert first_response.status_code == 500  # sanity: the simulated failure actually fired

    # uploads object is already gone (its remove() ran and succeeded
    # before the figures failure on the first attempt).
    with pytest.raises(Exception):
        admin.storage.from_("uploads").download(f"{user_id}/retry.pdf")
    # figures object survived — that's the call that failed.
    assert len(admin.storage.from_("figures").download(figure_path)) > 0

    # Retry with the real client — no simulated failure this time.
    second_response = app_client.delete(f"/documents/{document_id}", headers={"Authorization": f"Bearer {token}"})

    assert second_response.status_code == 204
    assert admin.table("documents").select("id").eq("id", document_id).execute().data == []
    assert admin.table("chunks").select("id").eq("document_id", document_id).execute().data == []
    with pytest.raises(Exception):
        admin.storage.from_("figures").download(figure_path)
