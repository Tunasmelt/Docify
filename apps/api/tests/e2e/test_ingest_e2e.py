# [FEAT-007] `/ingest` full end-to-end pipeline test.
#
# This is the ONE place the real pipeline runs together: real Docling
# parse, real Voyage embed call, real chunk insert into a real local
# Postgres, real figure upload to real local Storage, and a raw pgvector
# similarity query against the inserted rows — no fakes anywhere. Gated
# behind RUN_INGEST_E2E_TEST=1 (skipped by default) since it's slow
# (real Docling parse) and spends real Voyage quota, matching the
# established pattern for test_embedder.py's real-API test.
#
# Run explicitly with:
#   RUN_INGEST_E2E_TEST=1 uv run pytest tests/e2e/test_ingest_e2e.py -v -s

import os
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

from main import app
from tests._local_supabase import LOCAL_POSTGRES_DSN, create_test_user, delete_test_user, login, upload_via_rest

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INGEST_E2E_TEST") != "1",
    reason="set RUN_INGEST_E2E_TEST=1 to run the full real Docling+Voyage ingest pipeline (slow, uses Voyage quota)",
)


def test_full_ingest_pipeline_real_docling_real_voyage_real_db(admin):
    with open(os.path.join(FIXTURES, "table_heavy.pdf"), "rb") as f:
        pdf_bytes = f.read()

    user_id, email = create_test_user(admin)
    token = login(email)
    client = TestClient(app)

    try:
        filename = "table_heavy.pdf"
        upload_resp = upload_via_rest(token, "uploads", f"{user_id}/{filename}", pdf_bytes, "application/pdf")
        assert upload_resp.status_code in (200, 201), f"upload failed: {upload_resp.status_code} {upload_resp.text}"

        started = time.monotonic()
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
        elapsed = time.monotonic() - started

        assert response.status_code == 202
        document_id = response.json()["document_id"]

        doc_row = admin.table("documents").select("*").eq("id", document_id).execute().data[0]
        assert doc_row["status"] == "ready", f"pipeline did not reach ready — status={doc_row['status']!r} error={doc_row['error']!r}"

        chunk_rows = (
            admin.table("chunks").select("id, chunk_index, element_type, figure_path").eq("document_id", document_id).execute().data
        )
        figure_chunks = [c for c in chunk_rows if c["figure_path"] is not None]

        print("\n--- FEAT-007 real end-to-end ingest: table_heavy.pdf ---")
        print(f"elapsed (HTTP POST -> pipeline complete, synchronous under TestClient): {elapsed:.2f}s")
        print(f"document status: {doc_row['status']}")
        print(f"page_count: {doc_row['page_count']}")
        print(f"chunks inserted: {len(chunk_rows)}")
        print(f"figure chunks (uploaded to storage): {len(figure_chunks)}")

        assert len(chunk_rows) > 0

        for fig in figure_chunks:
            figure_bytes = admin.storage.from_("figures").download(fig["figure_path"])
            assert len(figure_bytes) > 0, f"figure_path {fig['figure_path']} recorded but object is empty/missing"

        # Raw pgvector similarity query against the actually-inserted rows —
        # not just "insert succeeded". Uses one inserted chunk's own
        # embedding as the query vector: it must be its own nearest
        # neighbor (cosine distance ~0), proving the stored vectors are
        # real, queryable, and not corrupted by the insert round-trip.
        conn = psycopg.connect(LOCAL_POSTGRES_DSN, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select id, embedding from chunks where document_id = %s order by chunk_index limit 1",
                    (document_id,),
                )
                probe_id, probe_embedding = cur.fetchone()

                cur.execute(
                    """
                    select id, embedding <=> %s as distance
                    from chunks
                    where document_id = %s
                    order by distance
                    limit 5
                    """,
                    (probe_embedding, document_id),
                )
                nearest = cur.fetchall()
        finally:
            conn.close()

        print("nearest-neighbor query (self as probe):")
        for row_id, distance in nearest:
            print(f"  chunk {row_id}  cosine_distance={distance:.6f}" + ("  <- probe itself" if row_id == probe_id else ""))

        assert nearest[0][0] == probe_id, "a chunk's own embedding should be its own nearest neighbor"
        assert nearest[0][1] < 1e-6, f"self-distance should be ~0, got {nearest[0][1]}"

    finally:
        delete_test_user(admin, user_id)
