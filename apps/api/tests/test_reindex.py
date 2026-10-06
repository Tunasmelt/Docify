# Tests for [FEAT-024 follow-up, 2026-08-02]: lazy stuck-document reaper
# (GET /documents) + POST /reindex/{document_id}.
#
# Same discipline as test_ingest.py/test_documents.py: real local Supabase
# Auth+DB+Storage throughout, parser/chunker/embedder faked for speed —
# except in the one test below that deliberately does NOT fake success,
# since its whole point is proving the pipeline can be genuinely
# interrupted mid-flight, not that a fake succeeds.

from datetime import datetime, timedelta, timezone

import pytest

from routes import ingest as ingest_module
from routes.ingest import STUCK_DOCUMENT_THRESHOLD_SECONDS
from tests.conftest import FakeChunker, FakeEmbedder, clear_pipeline_override, ingest_real_document, override_pipeline, upload_placeholder


class _SimulatedProcessKill(BaseException):
    """Deliberately NOT a subclass of Exception — run_ingest_pipeline's
    own `except Exception` (routes/ingest.py) must not catch this, the
    same way a real SIGKILL/OOM-kill doesn't go through Python's normal
    exception handling at all. This is what makes the test below a real
    simulation of "the background task's process died mid-flight" rather
    than "a handled error occurred" (which run_ingest_pipeline already
    handles cleanly via _fail_document — a different, already-tested
    path). Confirmed live: `finally:` still runs (Python guarantees this
    for any BaseException), but the `except Exception as exc: ...
    _fail_document(...)` block does not — so the document is left
    exactly where mark_parsing() last committed it, nothing more."""


class _KillingParser:
    """Raises _SimulatedProcessKill partway through parsing — after
    mark_parsing() has already committed 'parsing' to the DB (it's called
    before parser.parse() in run_ingest_pipeline), but before anything
    else in the pipeline runs."""

    def parse(self, file_bytes: bytes, filename: str = "document.pdf"):
        raise _SimulatedProcessKill("simulated OOM/process kill mid-parse")


def _backdate_created_at(admin, document_id: str, *, seconds_ago: int) -> None:
    """The only direct-DB-write step in this test file, and deliberately
    narrow: it moves a timestamp into the past to make a real elapsed-time
    threshold check fire in a fast test, WITHOUT fabricating the stuck
    STATE itself (that part comes from a genuinely interrupted real
    pipeline call below, not from this). There is no other way to
    simulate 30 real minutes passing in a test that needs to run in
    milliseconds."""
    backdated = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    # updated_at is what the reaper measures 'parsing' from (when the attempt started).
    admin.table("documents").update({"created_at": backdated, "updated_at": backdated}).eq("id", document_id).execute()


# Acceptance criterion (Part 3, item 3): force a document into a stuck
# 'parsing' state by simulating the ACTUAL failure mode (a killed
# background task), not a hand-crafted row — confirm GET /documents reaps
# it to 'failed' on the next real request, and POST /reindex/{id}
# successfully recovers it to 'ready'. The single most important test in
# this feature per the task brief: it's the first real proof, anywhere in
# this project, that the app can recover from the exact failure mode that
# broke the deployed backend before FEAT-027 (.agent/GAPS.md's FEAT-024
# entry: a real Render OOM-crash left a document stuck in 'parsing'
# forever, with nothing able to move it out of that state).
def test_reaper_and_reindex_recover_a_document_whose_pipeline_was_really_killed(app_client, admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="killed.pdf")

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "killed.pdf",
            "storage_path": storage_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    # Real call into the real pipeline function (not the HTTP route —
    # BackgroundTasks has no way to let a BaseException escape past the
    # response cycle, so this is called the same direct way
    # test_run_ingest_pipeline_refuses_mismatched_user_id_and_storage_path
    # already does in test_ingest.py). The BaseException genuinely
    # propagates out of this call — that's the point, not a bug in the
    # test — so it must be caught here, exactly as a real supervisor
    # process would observe "the child process is just gone" with no
    # exception ever seen by anything.
    with pytest.raises(_SimulatedProcessKill):
        ingest_module.run_ingest_pipeline(
            document_id=document_id,
            user_id=user_id,
            storage_path=storage_path,
            client=admin,
            parser=_KillingParser(),
            chunker=FakeChunker(),
            embedder=FakeEmbedder(),
        )

    # Confirm the interruption really happened where claimed: stuck at
    # 'parsing', not 'failed' — proving the pipeline's own except/cleanup
    # block never ran (a real handled failure would have already reached
    # 'failed' with an error message; that path is covered elsewhere,
    # e.g. test_dependency_construction_failure_marks_document_failed).
    row = admin.table("documents").select("status", "error").eq("id", document_id).execute().data[0]
    assert row["status"] == "parsing"
    assert row["error"] is None

    # Backdate past the reaper's threshold — same real elapsed-time
    # semantics the reaper checks in production, just compressed into a
    # fast test via a backdated timestamp rather than a real 30-minute wait.
    _backdate_created_at(admin, document_id, seconds_ago=STUCK_DOCUMENT_THRESHOLD_SECONDS + 60)

    # The real GET /documents route — the reaper (routes/documents.py)
    # must fire opportunistically here and flip this document to 'failed'
    # with an honest, specific message before the list is even returned.
    list_response = app_client.get("/documents", headers={"Authorization": f"Bearer {token}"})
    assert list_response.status_code == 200
    reaped = next(d for d in list_response.json()["documents"] if d["id"] == document_id)
    assert reaped["status"] == "failed"
    assert reaped["error"] == "processing timed out, possibly interrupted by a service restart"

    # POST /reindex/{document_id} — real HTTP call, fake (fast, free)
    # pipeline dependencies this time, since the point of THIS step is
    # proving recovery works, not proving interruption works again.
    override_pipeline()
    try:
        reindex_response = app_client.post(f"/reindex/{document_id}", headers={"Authorization": f"Bearer {token}"})
    finally:
        clear_pipeline_override()

    assert reindex_response.status_code == 202, f"{reindex_response.status_code} {reindex_response.text}"
    body = reindex_response.json()
    assert body["document_id"] == document_id
    assert body["status"] == "uploaded"  # queued for the ingest worker

    # BackgroundTasks runs synchronously under TestClient (conftest.py's
    # own established assumption, see ingest_real_document's docstring) —
    # by the time the POST above returns, the fake pipeline has already
    # run to completion.
    final = admin.table("documents").select("status", "error", "page_count").eq("id", document_id).execute().data[0]
    assert final["status"] == "ready", f"expected reindex to fully recover the document to 'ready', got {final}"
    assert final["error"] is None, "a successful reindex must clear the stale error from the prior failed attempt"

    chunk_rows = admin.table("chunks").select("id").eq("document_id", document_id).execute().data
    assert len(chunk_rows) > 0, "expected reindex to have produced real chunk rows"


# Acceptance criterion (Part 3, item 1): only documents genuinely past the
# threshold get reaped — a document still legitimately in flight (well
# under the threshold) must be left alone.
def test_reaper_does_not_touch_a_document_still_within_the_threshold(app_client, admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="in-flight.pdf")

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "in-flight.pdf",
            "storage_path": storage_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]
    admin.table("documents").update({"status": "parsing"}).eq("id", document_id).execute()

    response = app_client.get("/documents", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    row = next(d for d in response.json()["documents"] if d["id"] == document_id)
    assert row["status"] == "parsing", f"a fresh in-flight document must not be reaped, got {row}"


# Acceptance criterion (Part 3, item 2): POST /reindex/{document_id}
# real use case #1 — recovering a reaped 'failed' document (same recovery
# path as the combined test above, isolated here as its own acceptance
# check against a document that was never actually 'parsing' to begin
# with — e.g. one that failed for an ordinary reason unrelated to a
# process kill, like a real parse error).
def test_reindex_recovers_an_ordinary_failed_document(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    admin.table("documents").update({"status": "failed", "error": "some prior real failure"}).eq("id", document_id).execute()

    override_pipeline()
    try:
        response = app_client.post(f"/reindex/{document_id}", headers={"Authorization": f"Bearer {token}"})
    finally:
        clear_pipeline_override()

    assert response.status_code == 202
    row = admin.table("documents").select("status", "error").eq("id", document_id).execute().data[0]
    assert row["status"] == "ready"
    assert row["error"] is None


# Acceptance criterion (Part 3, item 2): reindex deletes old chunks before
# re-inserting — proves the real use case of "re-embed with a fresh
# provider attempt" doesn't crash on chunks.(document_id, chunk_index)'s
# unique constraint, and that stale chunk rows from the prior attempt
# don't linger alongside the new ones.
def test_reindex_replaces_old_chunks_not_duplicates_them(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf")
    original_chunk_ids = {r["id"] for r in admin.table("chunks").select("id").eq("document_id", document_id).execute().data}
    assert original_chunk_ids

    override_pipeline()
    try:
        response = app_client.post(f"/reindex/{document_id}", headers={"Authorization": f"Bearer {token}"})
    finally:
        clear_pipeline_override()
    assert response.status_code == 202

    new_chunk_rows = admin.table("chunks").select("id").eq("document_id", document_id).execute().data
    new_chunk_ids = {r["id"] for r in new_chunk_rows}
    assert new_chunk_ids, "expected reindex to have produced fresh chunk rows"
    assert original_chunk_ids.isdisjoint(new_chunk_ids), "old chunk rows must be gone, not merely added-to"


# Acceptance criterion: a document still 'parsing'/'embedded' cannot be
# reindexed out from under its own real in-flight background task (same
# 409 conflict discipline as DELETE /documents/{id}, routes/documents.py).
def test_reindex_rejects_a_document_currently_in_flight(app_client, admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="in-flight.pdf")
    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "in-flight.pdf",
            "storage_path": storage_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    admin.table("documents").update({"status": "parsing"}).eq("id", document["id"]).execute()

    response = app_client.post(f"/reindex/{document['id']}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409


# Acceptance criterion: reindex is scoped to the requesting user exactly
# like every other document route (FEAT-007/008's ownership discipline) —
# a document that doesn't exist and one owned by someone else are
# indistinguishable from the outside.
def test_reindex_of_another_users_document_returns_404(app_client, admin, user_a, user_b):
    _, token_a = user_a
    user_id_b, token_b = user_b
    document_id = ingest_real_document(app_client, user_id_b, token_b, filename="not-yours.pdf")

    response = app_client.post(f"/reindex/{document_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert response.status_code == 404


def test_reindex_of_nonexistent_document_returns_404(app_client, admin, user_a):
    _, token = user_a
    response = app_client.post(
        "/reindex/00000000-0000-0000-0000-000000000000", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
