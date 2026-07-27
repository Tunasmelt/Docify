# Must run (and set env vars) before any test module can `from main
# import app` — JWTAuthMiddleware resolves SUPABASE_URL from os.environ
# lazily, on the first request through TestClient, so this needs to win
# before that happens, not before app startup specifically.
# main.py's load_dotenv() defaults to override=False, so these values
# win regardless of import order (see FEAT-007 CHANGELOG entry for why
# this override exists at all: apps/api/.env points at the LIVE Supabase
# project, but STANDARDS.md requires integration tests to run against a
# local `supabase start` instance instead).
import functools
import os

from tests._local_supabase import LOCAL_SUPABASE_SERVICE_ROLE_KEY, LOCAL_SUPABASE_URL

os.environ["SUPABASE_URL"] = LOCAL_SUPABASE_URL
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = LOCAL_SUPABASE_SERVICE_ROLE_KEY

import psycopg
import pytest
from fastapi.testclient import TestClient

from main import app
from routes import ingest
from services.chunker import Chunk
from services.embedder import EmbedError
from services.parser import BBox, ElementType, ParsedDocument, ParsedElement
from tests._local_supabase import LOCAL_POSTGRES_DSN, admin_client, create_test_user, delete_test_user, login, upload_via_rest


def _local_supabase_reachable() -> bool:
    try:
        conn = psycopg.connect(LOCAL_POSTGRES_DSN, connect_timeout=3)
        conn.close()
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def require_local_supabase():
    if not _local_supabase_reachable():
        pytest.skip(f"no local Supabase reachable at {LOCAL_POSTGRES_DSN} — run `supabase start` first")


@pytest.fixture
def admin(require_local_supabase):
    return admin_client()


@pytest.fixture
def app_client():
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


# --- Shared FEAT-007/FEAT-008 test doubles and helpers ----------------------
#
# Docling/Voyage are faked throughout the integration suite (fast,
# deterministic); everything else — Auth, Postgres, Storage, RLS — is
# real. See test_ingest.py's module docstring for the full rationale.


def fake_elements(n: int = 2) -> list[ParsedElement]:
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
        self.elements = elements if elements is not None else fake_elements()

    def parse(self, pdf_bytes: bytes, filename: str = "document.pdf") -> ParsedDocument:
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
    scenario FEAT-007's task called out explicitly: the embedder
    computes some vectors internally, then fails — and the caller must
    still see zero partial data, never the vectors already computed."""

    def __init__(self, fail_after: int | None = None):
        self.fail_after = fail_after

    def embed(self, chunks):
        if self.fail_after is not None:
            partially_computed = [[0.5] * 1024 for _ in range(min(self.fail_after, len(chunks)))]
            del partially_computed  # never returned — this is the point of the test
            raise EmbedError(f"simulated embedder failure after computing {self.fail_after} of {len(chunks)} chunks")
        return [[float(i) / 1000] * 1024 for i in range(len(chunks))]


def upload_placeholder(user_id: str, token: str, filename: str = "placeholder.pdf") -> str:
    storage_path = f"uploads/{user_id}/{filename}"
    resp = upload_via_rest(token, "uploads", f"{user_id}/{filename}", b"placeholder bytes", "application/pdf")
    assert resp.status_code in (200, 201), f"setup upload failed: {resp.status_code} {resp.text}"
    return storage_path


def override_pipeline(parser=None, chunker=None, embedder=None):
    fake_runner = functools.partial(
        ingest.run_ingest_pipeline,
        parser=parser or FakeParser(),
        chunker=chunker or FakeChunker(),
        embedder=embedder or FakeEmbedder(),
    )
    app.dependency_overrides[ingest.get_pipeline_runner] = lambda: fake_runner


def clear_pipeline_override():
    app.dependency_overrides.pop(ingest.get_pipeline_runner, None)


def ingest_real_document(
    app_client, user_id: str, token: str, *, filename: str = "doc.pdf", chunks=None, elements=None,
    mime_type: str = "application/pdf",
) -> str:
    """Runs a real document through the real /ingest endpoint (real Auth,
    real DB writes, real Storage upload/download) with a faked Docling/
    Voyage pipeline, and returns the resulting document_id once the
    background task (synchronous under TestClient) has completed.
    Used by test_documents.py so GET/DELETE are tested against a
    document that actually went through the real pipeline, not a
    hand-inserted row — same discipline as FEAT-007."""
    storage_path = upload_placeholder(user_id, token, filename=filename)
    override_pipeline(parser=FakeParser(elements=elements), chunker=FakeChunker(chunks=chunks))
    try:
        response = app_client.post(
            "/ingest",
            json={
                "storage_path": storage_path,
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": 17,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        clear_pipeline_override()
    assert response.status_code == 202, f"setup ingest failed: {response.status_code} {response.text}"
    return response.json()["document_id"]
