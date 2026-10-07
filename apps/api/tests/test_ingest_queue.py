"""Ingest job queue (services/ingest_queue.py, migration 20261006_003).

Runs against the local Supabase stack: the claim RPC, the global
concurrency cap, lease expiry, retries and the worker thread are all real;
only the pipeline's parser/chunker/embedder are faked."""

import functools
import time
from datetime import datetime, timedelta, timezone

import pytest

from routes import ingest
from services import ingest_queue
from services.embedder import EmbedError
from tests.conftest import FakeChunker, FakeEmbedder, FakeParser, upload_placeholder

ISO = "%Y-%m-%dT%H:%M:%S.%fZ"


def _ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(ISO)


@pytest.fixture(autouse=True)
def isolated_queue(admin):
    """Each test sees only its own jobs: park everyone else's queued and
    running jobs (left by earlier tests) by finishing them."""
    admin.table("ingest_jobs").update({"status": "failed"}).in_("status", ["queued", "running"]).execute()
    yield


def _document(admin, user_id: str, token: str, filename: str) -> tuple[str, str]:
    storage_path = upload_placeholder(user_id, token, filename=filename)
    doc = admin.table("documents").insert(
        {"user_id": user_id, "filename": filename, "storage_path": storage_path, "mime_type": "application/pdf", "size_bytes": 17}
    ).execute().data[0]
    return doc["id"], storage_path


def _job(admin, job_id: str) -> dict:
    return admin.table("ingest_jobs").select("*").eq("id", job_id).execute().data[0]


def _doc(admin, document_id: str) -> dict:
    return admin.table("documents").select("status,error").eq("id", document_id).execute().data[0]


def _runner(**fakes):
    return functools.partial(
        ingest.run_ingest_pipeline,
        parser=fakes.get("parser", FakeParser()),
        chunker=fakes.get("chunker", FakeChunker()),
        embedder=fakes.get("embedder", FakeEmbedder()),
    )


class _FailingEmbedder:
    def embed(self, chunks):
        raise EmbedError("simulated Voyage 429 and Gemini fallback failure")


def test_ingest_enqueues_a_job_that_the_worker_runs_to_ready(app_client, admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="queued.pdf")
    from tests.conftest import clear_pipeline_override, override_pipeline

    override_pipeline()
    try:
        response = app_client.post(
            "/ingest",
            json={"storage_path": storage_path, "filename": "queued.pdf", "mime_type": "application/pdf", "size_bytes": 17},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        clear_pipeline_override()
    assert response.status_code == 202
    assert response.json()["status"] == "uploaded"  # queued at response time

    document_id = response.json()["document_id"]
    jobs = admin.table("ingest_jobs").select("*").eq("document_id", document_id).execute().data
    assert [(j["status"], j["attempts"]) for j in jobs] == [("succeeded", 1)]
    assert _doc(admin, document_id)["status"] == "ready"


def test_claim_respects_the_global_running_limit_and_fifo_order(admin, user_a):
    user_id, token = user_a
    first_doc, path1 = _document(admin, user_id, token, "first.pdf")
    second_doc, path2 = _document(admin, user_id, token, "second.pdf")
    first = ingest_queue.enqueue(admin, document_id=first_doc, user_id=user_id, storage_path=path1)
    second = ingest_queue.enqueue(admin, document_id=second_doc, user_id=user_id, storage_path=path2)

    claimed = ingest_queue.claim(admin, "worker-a", max_running=1)
    assert claimed["id"] == first["id"] and claimed["attempts"] == 1 and claimed["status"] == "running"
    # One job holds a live lease: nothing more until it finishes.
    assert ingest_queue.claim(admin, "worker-b", max_running=1) is None
    assert ingest_queue.claim(admin, "worker-b", max_running=2)["id"] == second["id"]


def test_a_job_whose_worker_died_is_claimed_again_after_its_lease(admin, user_a):
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "orphan.pdf")
    job = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)
    assert ingest_queue.claim(admin, "dead-worker", max_running=5)["id"] == job["id"]

    # Lease still live: not reclaimable.
    assert ingest_queue.claim(admin, "new-worker", max_running=5) is None
    # The dead worker stopped heartbeating long ago.
    admin.table("ingest_jobs").update({"locked_at": _ago(ingest_queue.JOB_LEASE_SECONDS + 5)}).eq("id", job["id"]).execute()

    reclaimed = ingest_queue.claim(admin, "new-worker", max_running=5)
    assert reclaimed["id"] == job["id"] and reclaimed["attempts"] == 2 and reclaimed["locked_by"] == "new-worker"


def test_transient_failure_is_retried_with_backoff_then_succeeds(admin, user_a):
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "flaky.pdf")
    job = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)

    assert ingest_queue.drain(_runner(embedder=_FailingEmbedder()), client=admin, max_running=5) == 1
    retried = _job(admin, job["id"])
    assert retried["status"] == "queued" and retried["attempts"] == 1
    assert "EmbedError" in retried["last_error"]
    run_after = datetime.fromisoformat(retried["run_after"])
    assert run_after > datetime.now(timezone.utc) + timedelta(seconds=ingest_queue.RETRY_BACKOFF_SECONDS[0] - 10)
    assert _doc(admin, doc) == {"status": "uploaded", "error": None}  # waiting, not failed

    # Not due yet: draining again does nothing.
    assert ingest_queue.drain(_runner(), client=admin, max_running=5) == 0
    admin.table("ingest_jobs").update({"run_after": _ago(1)}).eq("id", job["id"]).execute()
    assert ingest_queue.drain(_runner(), client=admin, max_running=5) == 1
    assert _job(admin, job["id"])["status"] == "succeeded" and _job(admin, job["id"])["attempts"] == 2
    assert _doc(admin, doc)["status"] == "ready"


def test_transient_failure_on_the_last_attempt_fails_the_document(admin, user_a):
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "always-429.pdf")
    job = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)
    admin.table("ingest_jobs").update({"attempts": 2}).eq("id", job["id"]).execute()  # next claim is attempt 3 of 3

    ingest_queue.drain(_runner(embedder=_FailingEmbedder()), client=admin, max_running=5)

    assert _job(admin, job["id"])["status"] == "failed"
    row = _doc(admin, doc)
    assert row["status"] == "failed" and "simulated Voyage 429" in row["error"]


def test_permanent_failure_is_not_retried(admin, user_a):
    from services.document_model import ParseError

    class _BrokenParser:
        def parse(self, file_bytes, filename="x.pdf"):
            raise ParseError("Failed to parse PDF: not a PDF")

    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "garbage.pdf")
    job = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)

    ingest_queue.drain(_runner(parser=_BrokenParser()), client=admin, max_running=5)

    assert (_job(admin, job["id"])["status"], _job(admin, job["id"])["attempts"]) == ("failed", 1)
    assert _doc(admin, doc) == {"status": "failed", "error": "Failed to parse PDF: not a PDF"}


def test_a_document_that_keeps_killing_the_worker_is_eventually_failed(admin, user_a):
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "oom.pdf")
    job = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)
    # Three attempts each ended with the process dying mid-job (lease expired).
    admin.table("ingest_jobs").update(
        {"status": "running", "attempts": 3, "locked_at": _ago(ingest_queue.JOB_LEASE_SECONDS + 5)}
    ).eq("id", job["id"]).execute()

    def _must_not_run(**_kwargs):
        raise AssertionError("the pipeline must not run a 4th time")

    ingest_queue.drain(_must_not_run, client=admin, max_running=5)

    assert _job(admin, job["id"])["status"] == "failed"
    assert _doc(admin, doc) == {"status": "failed", "error": ingest_queue.INTERRUPTED_TOO_OFTEN}


def test_heartbeat_keeps_the_lease_fresh_while_a_job_runs(admin, user_a, monkeypatch):
    monkeypatch.setattr(ingest_queue, "HEARTBEAT_SECONDS", 0.1)
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "slow.pdf")
    job = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)
    claimed = ingest_queue.claim(admin, "hb-worker", max_running=5)
    admin.table("ingest_jobs").update({"locked_at": _ago(3600)}).eq("id", job["id"]).execute()

    with ingest_queue._Heartbeat(claimed["id"], "hb-worker", claimed["attempts"]):
        time.sleep(1.0)

    locked_at = datetime.fromisoformat(_job(admin, job["id"])["locked_at"])
    assert locked_at > datetime.now(timezone.utc) - timedelta(seconds=30)


def test_worker_thread_processes_a_queued_job_when_woken(admin, user_a):
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "threaded.pdf")
    worker = ingest_queue.IngestWorker(_runner())
    worker.start()
    try:
        job = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)
        worker.wake()
        for _ in range(100):
            if _job(admin, job["id"])["status"] == "succeeded":
                break
            time.sleep(0.1)
    finally:
        worker.stop()

    assert _job(admin, job["id"])["status"] == "succeeded"
    assert _doc(admin, doc)["status"] == "ready"


def test_reindex_is_refused_while_a_job_for_the_document_is_queued(app_client, admin, user_a):
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "busy.pdf")
    admin.table("documents").update({"status": "ready"}).eq("id", doc).execute()
    ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)

    response = app_client.post(f"/reindex/{doc}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 409


@pytest.mark.parametrize("action,values", [
    ("progress", {"status": "failed", "error": "stale error"}),
    ("discard", {}), ("stage", {}), ("publish", {}),
    ("retry", {"run_after": _ago(1)}), ("finish", {"status": "failed"}),
])
def test_reclaimed_attempt_cannot_mutate_job_document_or_chunks(admin, user_a, action, values):
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "fenced.pdf")
    queued = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)
    old = ingest_queue.claim(admin, "old-worker", max_running=5)
    admin.table("ingest_jobs").update({"locked_at": _ago(121)}).eq("id", queued["id"]).execute()
    new = ingest_queue.claim(admin, "new-worker", max_running=5)
    with pytest.raises(ingest_queue.LostIngestLease):
        ingest_queue.IngestLease(admin, old, "old-worker").mutate(action, values)
    assert _job(admin, queued["id"])["locked_by"] == "new-worker"
    assert _doc(admin, doc) == {"status": "uploaded", "error": None}
    assert ingest_queue.process_job(admin, new, _runner(), "new-worker") == "succeeded"
    assert _doc(admin, doc)["status"] == "ready"


def test_worker_that_loses_lease_during_embedding_cannot_publish(admin, user_a):
    user_id, token = user_a
    doc, path = _document(admin, user_id, token, "takeover.pdf")
    queued = ingest_queue.enqueue(admin, document_id=doc, user_id=user_id, storage_path=path)
    old = ingest_queue.claim(admin, "old-worker", max_running=5)

    class ReclaimingEmbedder(FakeEmbedder):
        def embed(self, chunks):
            admin.table("ingest_jobs").update({"locked_at": _ago(121)}).eq("id", queued["id"]).execute()
            assert ingest_queue.claim(admin, "new-worker", max_running=5)
            return super().embed(chunks)

    assert ingest_queue.process_job(admin, old, _runner(embedder=ReclaimingEmbedder()), "old-worker") == "lost"
    assert _job(admin, queued["id"])["locked_by"] == "new-worker"
    assert admin.table("chunks").select("id").eq("document_id", doc).execute().data == []
