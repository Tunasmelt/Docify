# Tests for [FEAT-007] `/ingest` endpoint
#
# Runs against a real local Supabase stack (Auth + Postgres + Storage via
# `supabase start`) — per STANDARDS.md, no mocking Supabase in integration
# tests. Docling parsing and Voyage embedding ARE faked here (FakeParser/
# FakeChunker/FakeEmbedder below) so this file stays fast and deterministic;
# the fully real pipeline (real Docling + real Voyage) is exercised
# separately in tests/e2e/test_ingest_e2e.py, gated behind an env var since
# it's slow and costs Voyage quota.
#
# Auth is real too: every test logs in a genuinely created local user and
# uses their real ES256 access token — never a self-forged token (see
# .agent/MEMORY.md's anti-pattern entry on circular JWT verification from
# FEAT-003).

from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image

from main import app
from routes import ingest
from services.chunker import Chunk
from services.embedder import EmbedError
from services.parser import BBox, ElementType, ParsedDocument, ParsedElement
from tests._local_supabase import create_test_user, delete_test_user, login, rest_select, upload_via_rest


def _fake_elements(n: int = 2) -> list[ParsedElement]:
    return [
        ParsedElement(
            element_type=ElementType.TEXT,
            page_number=i + 1,
            bbox=BBox(x0=0, y0=0, x1=100, y1=20),
            content=f"fake element {i}",
            element_id=f"#/texts/{i}",
        )
        for i in range(n)
    ]


class FakeParser:
    def __init__(self, elements=None):
        self.elements = elements if elements is not None else _fake_elements()

    def parse(self, pdf_bytes: bytes) -> ParsedDocument:
        return ParsedDocument(elements=self.elements, dropped_elements=0)


class FakeChunker:
    """Ignores its ParsedDocument argument and returns a fixed chunk list
    if one was given, otherwise derives one chunk per input element —
    keeps FakeParser/FakeChunker index-consistent by construction."""

    def __init__(self, chunks=None):
        self._chunks = chunks

    def chunk(self, parsed_document: ParsedDocument) -> list[Chunk]:
        if self._chunks is not None:
            return self._chunks
        return [
            Chunk(
                chunk_index=i,
                element_type=ElementType.TEXT,
                page_numbers=[e.page_number],
                source_element_indices=[i],
                content=e.content,
            )
            for i, e in enumerate(parsed_document.elements)
        ]


class FakeEmbedder:
    """Returns real 1024-dim vectors (pgvector enforces exact dimension on
    insert) with distinct values per chunk, matching FEAT-006's
    correspondence-testing discipline. `fail_after` simulates the
    scenario this task calls out explicitly: the embedder computes some
    vectors internally, then fails — and the caller must still see zero
    partial data, never the vectors that were already computed."""

    def __init__(self, fail_after: int | None = None):
        self.fail_after = fail_after

    def embed(self, chunks):
        if self.fail_after is not None:
            partially_computed = [[0.5] * 1024 for _ in range(min(self.fail_after, len(chunks)))]
            del partially_computed  # never returned — this is the point of the test
            raise EmbedError(f"simulated embedder failure after computing {self.fail_after} of {len(chunks)} chunks")
        return [[float(i) / 1000] * 1024 for i in range(len(chunks))]


@pytest.fixture
def app_client():
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def user_a(admin):
    user_id, email = create_test_user(admin)
    token = login(email)
    yield user_id, token
    delete_test_user(admin, user_id)


@pytest.fixture
def user_b(admin):
    user_id, email = create_test_user(admin)
    token = login(email)
    yield user_id, token
    delete_test_user(admin, user_id)


def _upload_placeholder(user_id: str, token: str, filename: str = "placeholder.pdf") -> str:
    storage_path = f"uploads/{user_id}/{filename}"
    resp = upload_via_rest(token, "uploads", f"{user_id}/{filename}", b"placeholder bytes", "application/pdf")
    assert resp.status_code in (200, 201), f"setup upload failed: {resp.status_code} {resp.text}"
    return storage_path


def _override_pipeline(parser=None, chunker=None, embedder=None):
    import functools

    fake_runner = functools.partial(
        ingest.run_ingest_pipeline,
        parser=parser or FakeParser(),
        chunker=chunker or FakeChunker(),
        embedder=embedder or FakeEmbedder(),
    )
    app.dependency_overrides[ingest.get_pipeline_runner] = lambda: fake_runner


def _clear_override():
    app.dependency_overrides.pop(ingest.get_pipeline_runner, None)


# Acceptance criterion: POST /ingest with valid body returns 202 + document_id
def test_post_ingest_with_valid_body_returns_202_document_id(app_client, admin, user_a):
    user_id, token = user_a
    storage_path = _upload_placeholder(user_id, token)
    _override_pipeline()

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
        _clear_override()

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
    user_id, token = user_a
    storage_path = f"uploads/{user_id}/doc.docx"

    response = app_client.post(
        "/ingest",
        json={
            "storage_path": storage_path,
            "filename": "doc.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": 17,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# Acceptance criterion: Background task: download -> parse -> chunk -> embed -> insert -> update status
def test_background_task_download_parse_chunk_embed_insert_update_sta(admin, user_a):
    user_id, token = user_a
    storage_path = _upload_placeholder(user_id, token, filename="pipeline.pdf")

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
    elements = _fake_elements(2)
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
    storage_path = _upload_placeholder(user_id, token, filename="fails.pdf")

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
    storage_path = _upload_placeholder(user_id, token, filename="partial.pdf")

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

    elements = _fake_elements(5)
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

    storage_path = _upload_placeholder(user_id_a, token_a, filename="private.pdf")
    _override_pipeline()
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
        _clear_override()
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
    storage_path = _upload_placeholder(user_id, token, filename="construction-fails.pdf")

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
    storage_path = _upload_placeholder(user_id, token, filename="cleanup-fails.pdf")

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
