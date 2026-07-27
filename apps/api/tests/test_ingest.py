# Tests for [FEAT-007] `/ingest` endpoint
#
# Runs against a real local Supabase stack (Auth + Postgres + Storage via
# `supabase start`) — per STANDARDS.md, no mocking Supabase in integration
# tests. Docling parsing and Voyage embedding ARE faked here (FakeParser/
# FakeChunker/FakeEmbedder, defined in conftest.py and shared with
# test_documents.py) so this file stays fast and deterministic; the fully
# real pipeline (real Docling + real Voyage) is exercised separately in
# tests/e2e/test_ingest_e2e.py, gated behind an env var since it's slow
# and costs Voyage quota.
#
# Auth is real too: every test logs in a genuinely created local user and
# uses their real ES256 access token — never a self-forged token (see
# .agent/MEMORY.md's anti-pattern entry on circular JWT verification from
# FEAT-003). app_client/user_a/user_b/admin fixtures come from conftest.py.

from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image

from routes import ingest
from routes.ingest import StoragePathError, validate_storage_path
from services.chunker import Chunk
from services.parser import ElementType
from tests._local_supabase import rest_select, upload_via_rest
from tests.conftest import (
    FakeChunker,
    FakeEmbedder,
    FakeParser,
    clear_pipeline_override,
    fake_elements,
    override_pipeline,
    upload_placeholder,
)

# Acceptance criterion: POST /ingest with valid body returns 202 + document_id
def test_post_ingest_with_valid_body_returns_202_document_id(app_client, admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token)
    override_pipeline()

    try:
        response = app_client.post(
            "/ingest",
            json={
                "storage_path": storage_path,
                "filename": "placeholder.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 17,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        clear_pipeline_override()

    assert response.status_code == 202
    body = response.json()
    assert "document_id" in body and len(body["document_id"]) > 0
    assert body["status"] == "uploaded"  # DB default at insert time — see API_CONTRACT.md correction
    assert body["created_at"]

    row = admin.table("documents").select("*").eq("id", body["document_id"]).execute().data
    assert len(row) == 1
    assert row[0]["user_id"] == user_id


# Acceptance criterion: storage_path prefix mismatch with JWT user_id returns 403
def test_storage_path_prefix_mismatch_with_jwt_user_id_returns_403(app_client, admin, user_a, user_b):
    _, token_a = user_a
    user_id_b, _ = user_b

    response = app_client.post(
        "/ingest",
        json={
            "storage_path": f"uploads/{user_id_b}/somefile.pdf",  # belongs to user B, not the caller
            "filename": "somefile.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 17,
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"

    # No document row should have been created — the check happens before
    # anything else, per the task's explicit ordering requirement.
    rows = admin.table("documents").select("id").eq("storage_path", f"uploads/{user_id_b}/somefile.pdf").execute().data
    assert rows == []


def test_unsupported_mime_type_returns_422(app_client, user_a):
    # FEAT-020 (2026-07-27) extended SUPPORTED_MIME_TYPES to DOCX/PPTX/HTML
    # alongside PDF — this test's example must be something genuinely
    # still unsupported, not one of the newly-added formats.
    user_id, token = user_a
    storage_path = f"uploads/{user_id}/doc.txt"

    response = app_client.post(
        "/ingest",
        json={
            "storage_path": storage_path,
            "filename": "doc.txt",
            "mime_type": "text/plain",
            "size_bytes": 17,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# Acceptance criterion: Background task: download -> parse -> chunk -> embed -> insert -> update status
def test_background_task_download_parse_chunk_embed_insert_update_sta(admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="pipeline.pdf")

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "pipeline.pdf",
            "storage_path": storage_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    figure_image = Image.new("RGB", (10, 10), color="red")
    elements = fake_elements(2)
    chunks = [
        Chunk(chunk_index=0, element_type=ElementType.TEXT, page_numbers=[1], source_element_indices=[0], content="hello world"),
        Chunk(
            chunk_index=1,
            element_type=ElementType.FIGURE,
            page_numbers=[2],
            source_element_indices=[1],
            content="",
            image=figure_image,
        ),
    ]

    ingest.run_ingest_pipeline(
        document_id=document_id,
        user_id=user_id,
        storage_path=storage_path,
        client=admin,
        parser=FakeParser(elements=elements),
        chunker=FakeChunker(chunks=chunks),
        embedder=FakeEmbedder(),
    )

    doc_row = admin.table("documents").select("*").eq("id", document_id).execute().data[0]
    assert doc_row["status"] == "ready"
    assert doc_row["page_count"] == 2
    assert doc_row["parsed_at"] is not None
    assert doc_row["embedded_at"] is not None
    assert doc_row["error"] is None

    chunk_rows = admin.table("chunks").select("*").eq("document_id", document_id).order("chunk_index").execute().data
    assert len(chunk_rows) == 2
    assert chunk_rows[0]["content"] == "hello world"
    assert chunk_rows[0]["element_type"] == "text"
    assert chunk_rows[0]["figure_path"] is None
    assert chunk_rows[1]["element_type"] == "figure"
    expected_figure_path = f"{user_id}/{document_id}/1.png"
    assert chunk_rows[1]["figure_path"] == expected_figure_path

    # Figure was actually uploaded to storage, not just recorded as a path.
    figure_bytes = admin.storage.from_("figures").download(expected_figure_path)
    assert len(figure_bytes) > 0
    round_tripped = Image.open(BytesIO(figure_bytes))
    assert round_tripped.size == (10, 10)

    # FEAT-004 image ownership contract: the pipeline closed the image
    # after use — a closed PIL Image raises on further access.
    with pytest.raises(ValueError):
        figure_image.load()


# Acceptance criterion: Failure at any stage sets documents.status = 'failed' with error message
def test_failure_at_any_stage_sets_documents_status_failed_with_error(admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="fails.pdf")

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "fails.pdf",
            "storage_path": storage_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    ingest.run_ingest_pipeline(
        document_id=document_id,
        user_id=user_id,
        storage_path=storage_path,
        client=admin,
        parser=FakeParser(),
        chunker=FakeChunker(),
        embedder=FakeEmbedder(fail_after=1),
    )

    doc_row = admin.table("documents").select("*").eq("id", document_id).execute().data[0]
    assert doc_row["status"] == "failed"
    assert doc_row["error"]
    assert "embedder failure" in doc_row["error"]

    # Item 5's explicit requirement: not just status='failed', but zero
    # actual rows in `chunks` for this document.
    chunk_rows = admin.table("chunks").select("id").eq("document_id", document_id).execute().data
    assert chunk_rows == []


# Extra, beyond the scaffolded acceptance criteria: the literal scenario
# this task describes — the embedder has already computed some vectors
# internally before failing on a later one — proven with more chunks so
# "some chunks already computed" is concretely true, not just claimed.
def test_no_partial_chunk_rows_after_embedder_fails_partway(admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="partial.pdf")

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "partial.pdf",
            "storage_path": storage_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    elements = fake_elements(5)
    ingest.run_ingest_pipeline(
        document_id=document_id,
        user_id=user_id,
        storage_path=storage_path,
        client=admin,
        parser=FakeParser(elements=elements),
        chunker=FakeChunker(),  # 5 chunks, one per element
        embedder=FakeEmbedder(fail_after=3),  # "computes" 3 of 5, then raises
    )

    doc_row = admin.table("documents").select("status", "error").eq("id", document_id).execute().data[0]
    assert doc_row["status"] == "failed"

    chunk_rows = admin.table("chunks").select("id").eq("document_id", document_id).execute().data
    assert len(chunk_rows) == 0, f"expected zero partial chunk rows, found {len(chunk_rows)}"


# Acceptance criterion: Multi-tenant isolation: user A cannot see user B's document via any query path
def test_multi_tenant_isolation_user_a_cannot_see_user_b_s_document_v(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    _, token_b = user_b

    storage_path = upload_placeholder(user_id_a, token_a, filename="private.pdf")
    override_pipeline()
    try:
        response = app_client.post(
            "/ingest",
            json={
                "storage_path": storage_path,
                "filename": "private.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 17,
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )
    finally:
        clear_pipeline_override()
    assert response.status_code == 202
    document_id = response.json()["document_id"]

    # Sanity: pipeline actually produced a chunk to search for (uses the
    # default single-element FakeParser/FakeChunker/FakeEmbedder).
    admin_chunks = admin.table("chunks").select("id").eq("document_id", document_id).execute().data
    assert len(admin_chunks) >= 1, "isolation test needs at least one real chunk row to prove RLS blocks it"

    # Positive control — user A (the owner) CAN see their own data via all
    # three paths. Without this, an empty result for user B would be
    # meaningless (could just mean RLS blocks everyone, including the owner).
    list_as_a = rest_select(token_a, "documents", "select=id")
    assert list_as_a.status_code == 200
    assert document_id in [d["id"] for d in list_as_a.json()]

    lookup_as_a = rest_select(token_a, "documents", f"id=eq.{document_id}&select=id")
    assert lookup_as_a.status_code == 200
    assert len(lookup_as_a.json()) == 1

    chunks_as_a = rest_select(token_a, "chunks", f"document_id=eq.{document_id}&select=id")
    assert chunks_as_a.status_code == 200
    assert len(chunks_as_a.json()) >= 1

    # The actual isolation proof — user B, via the same three query paths.
    list_as_b = rest_select(token_b, "documents", "select=id")
    assert list_as_b.status_code == 200
    assert document_id not in [d["id"] for d in list_as_b.json()]

    lookup_as_b = rest_select(token_b, "documents", f"id=eq.{document_id}&select=id")
    assert lookup_as_b.status_code == 200
    assert lookup_as_b.json() == []

    chunks_as_b = rest_select(token_b, "chunks", f"document_id=eq.{document_id}&select=id")
    assert chunks_as_b.status_code == 200
    assert chunks_as_b.json() == []


# --- Codex review 2026-07-23: defense-in-depth hardening --------------------
#
# The review's core finding: the storage_path/user_id invariant was true for
# every *current* caller (the route checks it), but "no current caller
# violates an invariant" and "the invariant is enforced" are different
# claims — see .agent/MEMORY.md. These tests call run_ingest_pipeline()
# directly, bypassing the route entirely, to prove the function enforces
# its own precondition rather than trusting its one caller to keep doing so.


def test_run_ingest_pipeline_refuses_mismatched_user_id_and_storage_path(admin, user_a, user_b):
    user_id_a, _ = user_a
    user_id_b, _ = user_b

    document = admin.table("documents").insert(
        {
            "user_id": user_id_a,
            "filename": "mismatch.pdf",
            "storage_path": f"uploads/{user_id_b}/mismatch.pdf",  # wrong on purpose
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    # Fakes that would happily "succeed" if the invariant check didn't fire
    # first — so reaching 'ready' would prove the check was skipped, not
    # just that something eventually went wrong.
    ingest.run_ingest_pipeline(
        document_id=document_id,
        user_id=user_id_a,
        storage_path=f"uploads/{user_id_b}/mismatch.pdf",
        client=admin,
        parser=FakeParser(),
        chunker=FakeChunker(),
        embedder=FakeEmbedder(),
    )

    doc_row = admin.table("documents").select("status", "error").eq("id", document_id).execute().data[0]
    assert doc_row["status"] == "failed"
    assert "does not belong to user" in doc_row["error"]

    chunk_rows = admin.table("chunks").select("id").eq("document_id", document_id).execute().data
    assert chunk_rows == []


def test_dependency_construction_failure_marks_document_failed(admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="construction-fails.pdf")

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "construction-fails.pdf",
            "storage_path": storage_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    class FailingParser:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("simulated Parser() construction failure")

    # parser/chunker/embedder deliberately NOT injected — this exercises
    # the real default-construction path (`parser or Parser()`), with
    # Parser patched to fail the way a real misconfiguration might (e.g.
    # a missing model file). Before this fix, construction happened
    # before the try block and this would have propagated straight out
    # of run_ingest_pipeline, leaving the document stuck at 'uploaded'.
    with patch("routes.ingest.Parser", FailingParser):
        ingest.run_ingest_pipeline(
            document_id=document_id,
            user_id=user_id,
            storage_path=storage_path,
            client=admin,
        )

    doc_row = admin.table("documents").select("status", "error").eq("id", document_id).execute().data[0]
    assert doc_row["status"] == "failed"
    assert "simulated Parser() construction failure" in doc_row["error"]


def test_cleanup_failure_is_logged_and_does_not_crash_pipeline(admin, user_a, caplog):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="cleanup-fails.pdf")

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "cleanup-fails.pdf",
            "storage_path": storage_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    class AlwaysFailingClient:
        """Every operation fails — the main pipeline call fails first
        (triggering the except block), and the except block's own
        cleanup calls against this same client then fail too. Proves
        neither cleanup failure propagates out of run_ingest_pipeline."""

        def table(self, name):
            raise RuntimeError("simulated DB unavailable")

        @property
        def storage(self):
            raise RuntimeError("simulated storage unavailable")

    with caplog.at_level("ERROR"):
        # Must not raise — a crash here would be an unhandled exception
        # inside a FastAPI BackgroundTask, invisible beyond a stack trace
        # in server logs with no document-status signal at all.
        ingest.run_ingest_pipeline(
            document_id=document_id,
            user_id=user_id,
            storage_path=storage_path,
            client=AlwaysFailingClient(),
            parser=FakeParser(),
            chunker=FakeChunker(),
            embedder=FakeEmbedder(),
        )

    error_messages = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert any("delete_chunks_for_document itself failed" in m for m in error_messages)
    assert any("mark_failed itself failed" in m for m in error_messages)
    assert any(document_id in m for m in error_messages)


# --- Security fix regression tests: path traversal, 2026-07-23 -------------
#
# See routes.ingest.validate_storage_path()'s docstring for the full
# write-up. Two rounds of live testing against the real local Storage
# stack found two independently-exploitable bypasses before landing on
# the current whitelist-based check:
#   Round 1: bare startswith() — beaten by a literal "../" segment.
#   Round 2: reject literal ".." + require posixpath-canonical form —
#            beaten by a percent-encoded "../" ("%2e%2e%2f"), which
#            contains no literal ".." and normpath doesn't decode.
# Both are covered below at three levels: the validator function
# directly, the route (403), and — per this task's explicit requirement
# — a live end-to-end re-run of the exact recon proof-of-concept in
# reverse, not just trusting the unit tests.


def _traversal_paths(attacker_id: str, victim_id: str) -> list[str]:
    return [
        f"uploads/{attacker_id}/../{victim_id}/legit.pdf",  # round-1 exploit
        f"uploads/{attacker_id}/%2e%2e%2f{victim_id}/legit.pdf",  # round-2 exploit
        f"uploads/{attacker_id}/..%2f{victim_id}/legit.pdf",  # mixed literal+encoded
    ]


@pytest.mark.parametrize(
    "malicious_path",
    _traversal_paths("22222222-2222-2222-2222-222222222222", "11111111-1111-1111-1111-111111111111"),
)
def test_validate_storage_path_rejects_traversal_variants(malicious_path):
    with pytest.raises(StoragePathError):
        validate_storage_path(malicious_path, "22222222-2222-2222-2222-222222222222")


def test_validate_storage_path_rejects_absolute_path():
    with pytest.raises(StoragePathError):
        validate_storage_path("/etc/passwd", "22222222-2222-2222-2222-222222222222")


def test_validate_storage_path_rejects_non_canonical_double_slash():
    user_id = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(StoragePathError):
        validate_storage_path(f"uploads/{user_id}//legit.pdf", user_id)


def test_validate_storage_path_accepts_a_normal_canonical_path():
    user_id = "22222222-2222-2222-2222-222222222222"
    path = f"uploads/{user_id}/3f9e0a1b-1234-4567-89ab-cdef01234567.pdf"

    assert validate_storage_path(path, user_id) == path


@pytest.mark.parametrize(
    "malicious_path",
    _traversal_paths("22222222-2222-2222-2222-222222222222", "11111111-1111-1111-1111-111111111111"),
)
def test_ingest_route_rejects_path_traversal_storage_path(app_client, admin, user_a, malicious_path):
    user_id, token = user_a
    # Splice the attacker placeholder for the real logged-in user's id so
    # the path still starts with *their own* real prefix, as it must to
    # be an interesting test of the traversal specifically (not just a
    # flatly-wrong-prefix rejection, already covered elsewhere).
    malicious_path = malicious_path.replace("22222222-2222-2222-2222-222222222222", user_id)

    response = app_client.post(
        "/ingest",
        json={
            "storage_path": malicious_path,
            "filename": "legit.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 17,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    rows = admin.table("documents").select("id").eq("storage_path", malicious_path).execute().data
    assert rows == []


@pytest.mark.parametrize(
    "malicious_path",
    _traversal_paths("22222222-2222-2222-2222-222222222222", "11111111-1111-1111-1111-111111111111"),
)
def test_run_ingest_pipeline_rejects_path_traversal_storage_path(admin, user_a, malicious_path):
    user_id, _ = user_a
    malicious_path = malicious_path.replace("22222222-2222-2222-2222-222222222222", user_id)

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "legit.pdf",
            "storage_path": malicious_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    ingest.run_ingest_pipeline(
        document_id=document_id,
        user_id=user_id,
        storage_path=malicious_path,
        client=admin,
        parser=FakeParser(),
        chunker=FakeChunker(),
        embedder=FakeEmbedder(),
    )

    doc_row = admin.table("documents").select("status", "error").eq("id", document_id).execute().data[0]
    assert doc_row["status"] == "failed"
    assert "refusing" in doc_row["error"]

    chunk_rows = admin.table("chunks").select("id").eq("document_id", document_id).execute().data
    assert chunk_rows == []


def test_path_traversal_exploit_is_closed_end_to_end(app_client, admin, user_a, user_b):
    """Live re-run of the exact recon proof-of-concept, in reverse: a
    real user A uploads a real file with real, identifiable content; a
    real, unrelated user B attempts to ingest it via a crafted
    storage_path that starts with B's own prefix but traverses into A's.
    Before the fix, the equivalent raw request returned A's real content
    (confirmed in the recon report via both the SDK and raw HTTP) and
    would have been ingested into B's own chunks. Confirms both
    traversal spellings proven independently exploitable during the fix
    (literal ".." and percent-encoded) are now blocked before any
    Storage access is attempted at all."""
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b

    secret_content = b"USER A CONFIDENTIAL CONTENT - regression test for closed path traversal"
    upload_resp = upload_via_rest(token_a, "uploads", f"{user_id_a}/legit.pdf", secret_content, "application/pdf")
    assert upload_resp.status_code in (200, 201)

    try:
        malicious_paths = [
            f"uploads/{user_id_b}/../{user_id_a}/legit.pdf",
            f"uploads/{user_id_b}/%2e%2e%2f{user_id_a}/legit.pdf",
        ]

        for malicious_path in malicious_paths:
            response = app_client.post(
                "/ingest",
                json={
                    "storage_path": malicious_path,
                    "filename": "legit.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": len(secret_content),
                },
                headers={"Authorization": f"Bearer {token_b}"},
            )
            assert response.status_code == 403, f"traversal path {malicious_path!r} was not rejected"

        # No document row was ever created for user B referencing A's
        # file — the check happens before document creation, so there's
        # nothing that could have carried A's content into B's account.
        b_documents = admin.table("documents").select("id").eq("user_id", user_id_b).execute().data
        assert b_documents == []
    finally:
        admin.storage.from_("uploads").remove([f"{user_id_a}/legit.pdf"])


# --- Security fix regression tests: raw storage_path leaking into logs, --
# --- 2026-07-23 -------------------------------------------------------------
#
# Security review found post_ingest() and run_ingest_pipeline() both
# logged the full attacker-supplied storage_path on a rejected traversal
# attempt — safe from the client's perspective (the 403 body stays
# generic) but a real concern if logs are aggregated/hosted externally,
# since the raw string can embed another user's UUID or arbitrary
# attacker-chosen text. Fixed by giving StoragePathError a coarse
# `reason` separate from its detailed message, and logging only that
# (plus document_id/user_id) at both call sites. These tests use the
# same targeted log-output pattern the security review itself used
# (capture real log output after a triggered traversal attempt, inspect
# it directly) rather than trusting the code change by inspection alone.


def test_post_ingest_traversal_rejection_does_not_log_raw_storage_path(app_client, user_a, caplog):
    user_id, token = user_a
    malicious_path = f"uploads/{user_id}/../11111111-1111-1111-1111-111111111111/legit.pdf"

    with caplog.at_level("WARNING"):
        response = app_client.post(
            "/ingest",
            json={
                "storage_path": malicious_path,
                "filename": "legit.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 17,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403

    all_log_text = "\n".join(r.message for r in caplog.records)
    assert malicious_path not in all_log_text, "raw storage_path leaked into logs"
    assert ".." not in all_log_text, "a traversal fragment leaked into logs even if not the full path"

    # The log line still fires with useful, non-sensitive context.
    assert user_id in all_log_text
    assert "traversal_segment" in all_log_text


def test_run_ingest_pipeline_traversal_rejection_does_not_log_raw_storage_path(admin, user_a, caplog):
    user_id, _ = user_a
    malicious_path = f"uploads/{user_id}/../11111111-1111-1111-1111-111111111111/legit.pdf"

    document = admin.table("documents").insert(
        {
            "user_id": user_id,
            "filename": "legit.pdf",
            "storage_path": malicious_path,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        }
    ).execute().data[0]
    document_id = document["id"]

    with caplog.at_level("WARNING"):
        ingest.run_ingest_pipeline(
            document_id=document_id,
            user_id=user_id,
            storage_path=malicious_path,
            client=admin,
            parser=FakeParser(),
            chunker=FakeChunker(),
            embedder=FakeEmbedder(),
        )

    all_log_text = "\n".join(r.message for r in caplog.records)
    assert malicious_path not in all_log_text, "raw storage_path leaked into logs"
    assert ".." not in all_log_text, "a traversal fragment leaked into logs even if not the full path"

    # document_id/user_id + coarse reason are exactly what should appear.
    assert document_id in all_log_text
    assert user_id in all_log_text
    assert "traversal_segment" in all_log_text

    # documents.error (RLS-scoped to this same user, not a "log" in the
    # sense the security review meant) still gets the full detail — that
    # part is deliberately unchanged.
    doc_row = admin.table("documents").select("error").eq("id", document_id).execute().data[0]
    assert malicious_path in doc_row["error"]
