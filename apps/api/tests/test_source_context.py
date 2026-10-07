"""Source preview for non-PDF citations (2026-10-06).

PDF citations open the rendered page. DOCX, PPTX and HTML have no page
image, so GET /documents/{id}/chunks/{chunk_id}/context returns the cited
chunk with its surroundings: the whole slide for PPTX, the nearby chunks of
the same section for DOCX/HTML."""

import pytest

from routes.documents import SECTION_CONTEXT_RADIUS

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _document(admin, user_id: str, mime_type: str, filename: str = "source") -> str:
    return (
        admin.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": filename,
                "storage_path": f"uploads/{user_id}/{filename}",
                "mime_type": mime_type,
                "size_bytes": 1,
                "status": "ready",
            }
        )
        .execute()
        .data[0]["id"]
    )


def _chunks(admin, user_id: str, document_id: str, specs: list[dict]) -> list[str]:
    """specs: dicts with content, and optionally page, element_type, section, figure_path."""
    rows = [
        {
            "document_id": document_id,
            "user_id": user_id,
            "chunk_index": i,
            "element_type": spec.get("element_type", "text"),
            "page_number": spec.get("page", 1),
            "content": spec["content"],
            "figure_path": spec.get("figure_path"),
            "embedding": [0.001 * (i + 1)] * 1024,
            "embedding_provider": "voyage",
            "metadata": {"section_heading": spec.get("section")},
        }
        for i, spec in enumerate(specs)
    ]
    inserted = admin.table("chunks").insert(rows).execute().data
    return [row["id"] for row in sorted(inserted, key=lambda r: r["chunk_index"])]


def _context(app_client, token, document_id, chunk_id):
    return app_client.get(
        f"/documents/{document_id}/chunks/{chunk_id}/context", headers={"Authorization": f"Bearer {token}"}
    )


def test_pptx_citation_returns_every_chunk_on_its_slide_in_order(app_client, admin, user_a):
    user_id, token = user_a
    doc = _document(admin, user_id, PPTX, "deck.pptx")
    ids = _chunks(
        admin,
        user_id,
        doc,
        [
            {"content": "Title slide", "page": 1},
            {"content": "Q3 2026 Highlights", "page": 2, "element_type": "heading"},
            {"content": "Revenue reached $1,410,000", "page": 2},
            {"content": "Customer count grew to 5,200", "page": 2},
            {"content": "Thank you", "page": 3},
        ],
    )

    response = _context(app_client, token, doc, ids[2])

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "slide" and body["label"] == "Slide 2"
    assert [b["content"] for b in body["blocks"]] == [
        "Q3 2026 Highlights",
        "Revenue reached $1,410,000",
        "Customer count grew to 5,200",
    ]
    assert [b["cited"] for b in body["blocks"]] == [False, True, False]


def test_docx_citation_returns_its_section_without_repeating_the_heading(app_client, admin, user_a):
    user_id, token = user_a
    doc = _document(admin, user_id, DOCX, "report.docx")
    ids = _chunks(
        admin,
        user_id,
        doc,
        [
            {"content": "Introduction\nWhy this report exists.", "section": "Introduction"},
            {"content": "Quarterly Results", "element_type": "heading", "section": "Introduction"},
            {"content": "Quarterly Results\nRevenue grew every quarter.", "section": "Quarterly Results"},
            {
                "content": "Quarterly Results\n\n| Quarter | Revenue |\n|---|---|\n| Q3 | 1,410,000 |",
                "element_type": "table",
                "section": "Quarterly Results",
            },
            {"content": "Conclusion\nAll good.", "section": "Conclusion"},
        ],
    )

    body = _context(app_client, token, doc, ids[3]).json()

    assert body["kind"] == "section" and body["label"] == "Quarterly Results"
    assert [b["content"] for b in body["blocks"]] == [
        "Revenue grew every quarter.",
        "| Quarter | Revenue |\n|---|---|\n| Q3 | 1,410,000 |",
    ]
    assert [b["cited"] for b in body["blocks"]] == [False, True]
    assert body["blocks"][1]["element_type"] == "table"


def test_long_section_is_cut_to_a_window_around_the_cited_chunk(app_client, admin, user_a):
    user_id, token = user_a
    doc = _document(admin, user_id, "text/html", "page.html")
    ids = _chunks(admin, user_id, doc, [{"content": f"Part {i}", "section": None} for i in range(20)])

    body = _context(app_client, token, doc, ids[10]).json()

    contents = [b["content"] for b in body["blocks"]]
    r = SECTION_CONTEXT_RADIUS
    assert contents == [f"Part {i}" for i in range(10 - r, 10 + r + 1)]
    assert body["label"] is None


def test_figure_blocks_carry_a_signed_image_url(app_client, admin, user_a):
    user_id, token = user_a
    doc = _document(admin, user_id, PPTX, "deck.pptx")
    admin.storage.from_("figures").upload(f"{user_id}/{doc}/0.png", b"\x89PNG fake", {"content-type": "image/png"})
    try:
        ids = _chunks(
            admin,
            user_id,
            doc,
            [{"content": "Revenue Chart", "page": 3, "element_type": "figure", "figure_path": f"{user_id}/{doc}/0.png"}],
        )
        block = _context(app_client, token, doc, ids[0]).json()["blocks"][0]
        assert block["figure_url"].startswith("http")
    finally:
        admin.storage.from_("figures").remove([f"{user_id}/{doc}/0.png"])


def test_pdf_documents_use_the_page_image_instead(app_client, admin, user_a):
    user_id, token = user_a
    doc = _document(admin, user_id, "application/pdf", "a.pdf")
    ids = _chunks(admin, user_id, doc, [{"content": "x"}])

    response = _context(app_client, token, doc, ids[0])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("case", ["other user", "chunk from another document"])
def test_context_is_404_outside_the_callers_own_document(app_client, admin, user_a, user_b, case):
    user_id, token = user_a
    doc = _document(admin, user_id, DOCX, "mine.docx")
    other_doc = _document(admin, user_id, DOCX, "other.docx")
    ids = _chunks(admin, user_id, doc, [{"content": "secret"}])
    if case == "other user":
        _, other_token = user_b
        response = _context(app_client, other_token, doc, ids[0])
    else:
        response = _context(app_client, token, other_doc, ids[0])
    assert response.status_code == 404


@pytest.mark.parametrize("mime,filename", [(DOCX, "history.docx"), (PPTX, "history.pptx")])
def test_archived_source_shows_original_evidence_without_current_version_neighbours(app_client, admin, user_a, mime, filename):
    user_id, token = user_a
    doc = _document(admin, user_id, mime, filename)
    old = _chunks(admin, user_id, doc, [{"content": "Original revenue was 100.", "section": "Revenue"}])[0]
    admin.table("chunks").update({"archived": True}).eq("id", old).execute()
    _chunks(admin, user_id, doc, [{"content": "New extraction says 200.", "section": "Revenue"}])
    response = _context(app_client, token, doc, old)
    assert response.status_code == 200
    assert [b["content"] for b in response.json()["blocks"]] == ["Original revenue was 100."]
    assert response.json()["blocks"][0]["cited"] is True
