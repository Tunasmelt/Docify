"""Per-user cap on documents processing at once (2026-10-06).

Ingest runs as a background task in the same process that serves requests,
with no queue. One upload stuck in the OCR chain can take about 3 minutes
and most of Render's 512MB, so a user may only have a few documents
processing at a time. The count comes from the documents table, so it holds
across restarts and instances."""

from datetime import datetime, timedelta, timezone

from routes.ingest import MAX_CONCURRENT_INGESTS_PER_USER, STUCK_DOCUMENT_THRESHOLD_SECONDS
from tests.conftest import clear_pipeline_override, ingest_real_document, override_pipeline, upload_placeholder


def _insert_document(admin, user_id: str, *, status: str, started_seconds_ago: int = 0) -> str:
    started = (datetime.now(timezone.utc) - timedelta(seconds=started_seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    row = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": f"{status}.pdf",
            "storage_path": f"uploads/{user_id}/{status}.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 17,
            "status": status,
            "created_at": started,
            "updated_at": started,
        }
    ).execute()
    return row.data[0]["id"]


def _post_ingest(app_client, user_id: str, token: str, filename: str):
    storage_path = upload_placeholder(user_id, token, filename=filename)
    override_pipeline()
    try:
        return app_client.post(
            "/ingest",
            json={"storage_path": storage_path, "filename": filename, "mime_type": "application/pdf", "size_bytes": 17},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        clear_pipeline_override()


def test_ingest_is_refused_while_the_user_already_has_the_maximum_processing(app_client, admin, user_a):
    user_id, token = user_a
    for status in ["uploaded", "parsing", "embedded"][:MAX_CONCURRENT_INGESTS_PER_USER]:
        _insert_document(admin, user_id, status=status)

    response = _post_ingest(app_client, user_id, token, "one-too-many.pdf")

    assert response.status_code == 429
    body = response.json()
    assert body["error"]["code"] == "TOO_MANY_PROCESSING"
    assert f"{MAX_CONCURRENT_INGESTS_PER_USER} documents" in body["error"]["message"]
    # Nothing was created for the refused upload.
    filenames = [d["filename"] for d in admin.table("documents").select("filename").eq("user_id", user_id).execute().data]
    assert "one-too-many.pdf" not in filenames


def test_ingest_is_allowed_below_the_cap(app_client, admin, user_a):
    user_id, token = user_a
    for _ in range(MAX_CONCURRENT_INGESTS_PER_USER - 1):
        _insert_document(admin, user_id, status="parsing")

    assert _post_ingest(app_client, user_id, token, "fits.pdf").status_code == 202


def test_finished_and_stale_documents_do_not_count_toward_the_cap(app_client, admin, user_a):
    user_id, token = user_a
    for _ in range(MAX_CONCURRENT_INGESTS_PER_USER):
        _insert_document(admin, user_id, status="ready")
        _insert_document(admin, user_id, status="failed")
        # Killed mid-ingest long ago; the reaper hasn't run yet. Must not block the user forever.
        _insert_document(admin, user_id, status="parsing", started_seconds_ago=STUCK_DOCUMENT_THRESHOLD_SECONDS + 60)

    assert _post_ingest(app_client, user_id, token, "after-stale.pdf").status_code == 202


def test_other_users_processing_documents_do_not_count(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, _ = user_b
    for _ in range(MAX_CONCURRENT_INGESTS_PER_USER):
        _insert_document(admin, user_id_b, status="parsing")

    assert _post_ingest(app_client, user_id_a, token_a, "mine.pdf").status_code == 202


def test_reindex_is_refused_at_the_cap_and_counts_from_when_reprocessing_started(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="old.pdf")
    # Uploaded long ago: reindexing must restart its "processing since" clock.
    old = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    admin.table("documents").update({"created_at": old, "updated_at": old}).eq("id", document_id).execute()

    override_pipeline()
    try:
        # The pipeline runs synchronously under TestClient, so check the row
        # mark_parsing() wrote by reading updated_at after the call.
        response = app_client.post(f"/reindex/{document_id}", headers={"Authorization": f"Bearer {token}"})
    finally:
        clear_pipeline_override()
    assert response.status_code == 202
    updated_at = admin.table("documents").select("updated_at").eq("id", document_id).execute().data[0]["updated_at"]
    assert datetime.fromisoformat(updated_at) > datetime.now(timezone.utc) - timedelta(minutes=5)

    for _ in range(MAX_CONCURRENT_INGESTS_PER_USER):
        _insert_document(admin, user_id, status="parsing")
    response = app_client.post(f"/reindex/{document_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "TOO_MANY_PROCESSING"
