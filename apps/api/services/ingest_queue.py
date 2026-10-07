"""Postgres-backed queue for document ingest (2026-10-06).

/ingest and /reindex insert a row into ingest_jobs; a single worker thread
per API process claims jobs through the claim_ingest_job() RPC and runs
the ingest pipeline. Before this, each upload ran as a FastAPI
BackgroundTask in the request-serving process, which meant:

- no global limit: several users' uploads could run at once and exhaust
  the 512MB instance. Now at most INGEST_MAX_CONCURRENT_JOBS (default 1)
  jobs hold a live lease, across all users and instances;
- lost work on restart: the reaper marked the document failed 30 minutes
  later. Now a running job sends a heartbeat every HEARTBEAT_SECONDS; once
  it misses JOB_LEASE_SECONDS the job is claimed again and resumes;
- no automatic retry: a Voyage 429 meant the user pressing Retry. Now a
  transient failure is retried with backoff, up to max_attempts;
- no time limit. Now each attempt gets JOB_TIME_LIMIT_SECONDS, checked
  between pipeline stages and between OCR'd pages (cooperative: Python
  can't kill a thread, so one OCR call already in flight can overrun the
  limit by up to its own 60s timeout per tier).
"""

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from db.client import get_service_role_client

logger = logging.getLogger(__name__)

JOB_LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
JOB_TIME_LIMIT_SECONDS = 20 * 60
# Delay before attempt 2, attempt 3, ... (the last value repeats).
RETRY_BACKOFF_SECONDS = (60, 300)
# How often an idle worker looks for due retries and abandoned jobs; new
# jobs wake it immediately.
IDLE_POLL_SECONDS = 15

INTERRUPTED_TOO_OFTEN = (
    "Processing was interrupted repeatedly (the server restarted or ran out of memory) and was stopped."
)


class TransientIngestError(Exception):
    """Raised by the pipeline for a failure worth retrying (rate limits,
    network or storage hiccups) when attempts remain."""


def max_concurrent_jobs() -> int:
    return max(1, int(os.environ.get("INGEST_MAX_CONCURRENT_JOBS", "1")))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def enqueue(client, *, document_id: str, user_id: str, storage_path: str) -> dict:
    return (
        client.table("ingest_jobs")
        .insert({"document_id": document_id, "user_id": user_id, "storage_path": storage_path})
        .execute()
        .data[0]
    )


def has_active_job(client, document_id: str) -> bool:
    rows = (
        client.table("ingest_jobs")
        .select("id")
        .eq("document_id", document_id)
        .in_("status", ["queued", "running"])
        .limit(1)
        .execute()
        .data
    )
    return bool(rows)


def claim(client, worker_id: str, *, max_running: int) -> dict | None:
    rows = (
        client.rpc(
            "claim_ingest_job",
            {"p_worker_id": worker_id, "p_lease_seconds": JOB_LEASE_SECONDS, "p_max_running": max_running},
        )
        .execute()
        .data
    )
    return rows[0] if rows else None


class LostIngestLease(Exception):
    """The attempt was reclaimed; this worker must stop without mutating it."""


class IngestLease:
    def __init__(self, client, job: dict, worker_id: str):
        self.client, self.job, self.worker_id = client, job, worker_id

    def mutate(self, action: str, values: dict | None = None, rows: list | None = None) -> None:
        accepted = self.client.rpc("mutate_ingest_attempt", {
            "p_job_id": self.job["id"], "p_user_id": self.job["user_id"],
            "p_worker_id": self.worker_id, "p_attempt": self.job["attempts"],
            "p_action": action, "p_values": values or {}, "p_rows": rows or [],
        }).execute().data
        if not accepted:
            raise LostIngestLease(self.job["id"])


class _Heartbeat:
    """Refreshes the job's lease while it runs, on its own client and thread."""

    def __init__(self, job_id: str, worker_id: str, attempt: int):
        self._job_id = job_id
        self._worker_id = worker_id
        self._attempt = attempt
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"ingest-heartbeat-{job_id[:8]}", daemon=True)

    def _run(self) -> None:
        client = None
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                client = client or get_service_role_client()
                client.table("ingest_jobs").update({"locked_at": _iso(datetime.now(timezone.utc))}).eq(
                    "id", self._job_id
                ).eq("locked_by", self._worker_id).eq("attempts", self._attempt).eq("status", "running").execute()
            except Exception:
                logger.warning("ingest heartbeat failed for job %s", self._job_id, exc_info=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


def process_job(client, job: dict, runner, worker_id: str) -> str:
    """Runs one claimed job. Returns "succeeded", "failed", "retry" or "lost"."""
    lease = IngestLease(client, job, worker_id)
    if job["attempts"] > job["max_attempts"]:
        # Claimed again after its lease expired more times than allowed: the
        # process keeps dying on this document (e.g. out of memory).
        lease.mutate("progress", {"status": "failed", "error": INTERRUPTED_TOO_OFTEN})
        lease.mutate("finish", {"status": "failed", "error": INTERRUPTED_TOO_OFTEN})
        return "failed"

    final_attempt = job["attempts"] >= job["max_attempts"]
    deadline = time.monotonic() + JOB_TIME_LIMIT_SECONDS
    with _Heartbeat(job["id"], worker_id, job["attempts"]):
        try:
            ok = runner(
                document_id=job["document_id"],
                user_id=job["user_id"],
                storage_path=job["storage_path"],
                deadline=deadline,
                final_attempt=final_attempt,
                lease=lease,
            )
        except TransientIngestError as exc:
            delay = RETRY_BACKOFF_SECONDS[min(job["attempts"] - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            try:
                lease.mutate("retry", {"error": str(exc), "run_after": _iso(datetime.now(timezone.utc) + timedelta(seconds=delay))})
            except LostIngestLease:
                return "lost"
            logger.warning(
                "ingest job %s (document %s) attempt %s/%s failed transiently, retrying in %ss: %s",
                job["id"], job["document_id"], job["attempts"], job["max_attempts"], delay, exc,
            )
            return "retry"
        except LostIngestLease:
            return "lost"
    status = "succeeded" if ok else "failed"
    try:
        lease.mutate("finish", {"status": status})
    except LostIngestLease:
        return "lost"
    return status


def drain(runner, *, client=None, worker_id: str | None = None, max_running: int | None = None) -> int:
    """Claims and runs jobs until none is runnable. Returns how many ran."""
    client = client or get_service_role_client()
    worker_id = worker_id or f"drain-{uuid.uuid4().hex[:8]}"
    ran = 0
    while True:
        job = claim(client, worker_id, max_running=max_running or max_concurrent_jobs())
        if job is None:
            return ran
        process_job(client, job, runner, worker_id)
        ran += 1


class IngestWorker:
    """One background thread per API process that drains the queue, then
    sleeps until woken by a new job or IDLE_POLL_SECONDS pass."""

    def __init__(self, runner):
        self._runner = runner
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.worker_id = f"{os.environ.get('RENDER_INSTANCE_ID', 'local')}-{uuid.uuid4().hex[:8]}"

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="ingest-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        client = None
        while not self._stop.is_set():
            try:
                client = client or get_service_role_client()
                drain(self._runner, client=client, worker_id=self.worker_id)
            except Exception:
                # Never let the worker die: log, drop the client (it may be
                # the problem), and try again after the idle wait.
                logger.exception("ingest worker loop error")
                client = None
            self._wake.wait(IDLE_POLL_SECONDS)
            self._wake.clear()


_worker: IngestWorker | None = None


def start_worker(runner) -> IngestWorker:
    global _worker
    if _worker is None:
        _worker = IngestWorker(runner)
        _worker.start()
    return _worker


def stop_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def wake_worker() -> None:
    if _worker is not None:
        _worker.wake()
