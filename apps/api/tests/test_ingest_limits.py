"""Per-document limits (2026-10-06): file size, pages, pages needing OCR,
and the job time limit. Each fails the document with a message the user can
act on, and none is retried (the same file would fail the same way)."""

import time
from pathlib import Path

import pytest

from routes import ingest
from services import parser as parser_module
from services.document_model import DocumentLimitError, IngestTimeoutError
from services.parser import Parser
from tests.conftest import FakeChunker, FakeEmbedder, FakeParser, upload_placeholder

FIXTURES = Path(__file__).parent / "fixtures"


class _CountingOcr:
    def __init__(self):
        self.calls = 0

    def transcribe_page(self, image):
        self.calls += 1
        return "recovered text"


def _document(admin, user_id, token, filename="doc.pdf"):
    storage_path = upload_placeholder(user_id, token, filename=filename)
    doc = admin.table("documents").insert(
        {"user_id": user_id, "filename": filename, "storage_path": storage_path, "mime_type": "application/pdf", "size_bytes": 17}
    ).execute().data[0]
    return doc["id"], storage_path


def _doc(admin, document_id):
    return admin.table("documents").select("status,error").eq("id", document_id).execute().data[0]


# ── Size ──────────────────────────────────────────────────────────────────────


def test_ingest_refuses_a_declared_size_over_the_limit(app_client, admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="huge.pdf")

    response = app_client.post(
        "/ingest",
        json={
            "storage_path": storage_path,
            "filename": "huge.pdf",
            "mime_type": "application/pdf",
            "size_bytes": ingest.MAX_UPLOAD_BYTES + 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert "50 MB" in response.json()["error"]["message"]
    assert not admin.table("documents").select("id").eq("filename", "huge.pdf").eq("user_id", user_id).execute().data


def test_the_real_file_size_is_checked_after_download(admin, user_a, monkeypatch):
    # The browser's size_bytes isn't trusted: the downloaded bytes are measured.
    monkeypatch.setattr(ingest, "MAX_UPLOAD_BYTES", 5)
    user_id, token = user_a
    document_id, storage_path = _document(admin, user_id, token)  # placeholder upload is 17 bytes

    ok = ingest.run_ingest_pipeline(
        document_id=document_id, user_id=user_id, storage_path=storage_path, client=admin,
        parser=FakeParser(), chunker=FakeChunker(), embedder=FakeEmbedder(), final_attempt=False,
    )

    assert ok is False  # failed outright, not raised for a retry
    row = _doc(admin, document_id)
    assert row["status"] == "failed" and "the limit is" in row["error"]


# ── Pages ─────────────────────────────────────────────────────────────────────


def test_pdf_over_the_page_limit_is_refused_before_parsing(monkeypatch):
    monkeypatch.setattr(parser_module, "MAX_PAGES", 1)
    with pytest.raises(DocumentLimitError, match="This document has 2 pages; the limit is 1."):
        Parser(ocr_tiers=[]).parse((FIXTURES / "split_table.pdf").read_bytes(), filename="split.pdf")


def test_pptx_over_the_slide_limit_is_refused(monkeypatch):
    monkeypatch.setattr(parser_module, "MAX_PAGES", 2)
    with pytest.raises(DocumentLimitError, match="This document has 3 slides; the limit is 2."):
        Parser(ocr_tiers=[]).parse((FIXTURES / "slides.pptx").read_bytes(), filename="slides.pptx")


def test_documents_within_the_page_limit_are_unaffected():
    doc = Parser(ocr_tiers=[]).parse((FIXTURES / "split_table.pdf").read_bytes(), filename="split.pdf")
    assert doc.elements


# ── OCR budget ────────────────────────────────────────────────────────────────


def test_too_many_scanned_pages_fails_before_any_ocr_call(monkeypatch):
    monkeypatch.setattr(parser_module, "MAX_OCR_PAGES", 2)
    ocr = _CountingOcr()

    with pytest.raises(DocumentLimitError, match=r"3 pages .* need text recognition \(OCR\); the limit is 2"):
        Parser(ocr_tiers=[("fake", ocr)]).parse((FIXTURES / "scanned.pdf").read_bytes(), filename="scanned.pdf")
    assert ocr.calls == 0


def test_scanned_pages_within_the_budget_are_ocrd():
    ocr = _CountingOcr()
    Parser(ocr_tiers=[("fake", ocr)]).parse((FIXTURES / "scanned.pdf").read_bytes(), filename="scanned.pdf")
    assert ocr.calls == 3


# ── Time limit ────────────────────────────────────────────────────────────────


def test_ocr_stops_once_the_job_deadline_passes():
    ocr = _CountingOcr()
    with pytest.raises(IngestTimeoutError):
        Parser(ocr_tiers=[("fake", ocr)], deadline=time.monotonic() - 1).parse(
            (FIXTURES / "scanned.pdf").read_bytes(), filename="scanned.pdf"
        )
    assert ocr.calls == 0


def test_pipeline_past_its_deadline_fails_permanently(admin, user_a):
    user_id, token = user_a
    document_id, storage_path = _document(admin, user_id, token)

    ok = ingest.run_ingest_pipeline(
        document_id=document_id, user_id=user_id, storage_path=storage_path, client=admin,
        parser=FakeParser(), chunker=FakeChunker(), embedder=FakeEmbedder(),
        deadline=time.monotonic() - 1, final_attempt=False,
    )

    assert ok is False
    row = _doc(admin, document_id)
    assert row["status"] == "failed" and "time limit (20 minutes)" in row["error"]
    assert not admin.table("chunks").select("id").eq("document_id", document_id).execute().data


def test_a_missing_upload_fails_at_once_instead_of_being_retried(admin, user_a):
    user_id, _token = user_a
    doc = admin.table("documents").insert(
        {"user_id": user_id, "filename": "gone.pdf", "storage_path": f"uploads/{user_id}/never-uploaded.pdf",
         "mime_type": "application/pdf", "size_bytes": 17}
    ).execute().data[0]

    ok = ingest.run_ingest_pipeline(
        document_id=doc["id"], user_id=user_id, storage_path=f"uploads/{user_id}/never-uploaded.pdf", client=admin,
        parser=FakeParser(), chunker=FakeChunker(), embedder=FakeEmbedder(), final_attempt=False,
    )

    assert ok is False
    assert _doc(admin, doc["id"]) == {"status": "failed", "error": "The uploaded file is missing from storage. Upload it again."}
