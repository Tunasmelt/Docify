# Page preview with citation highlight (2026-10-06).
#
# "Open page N in document" in the source panel used to be a dead button
# (its handler only logged to the console). GET
# /documents/{id}/pages/{n}/image renders the PDF page server-side
# (pdfplumber, no new dependency) and draws the cited chunk's bbox on it.

from io import BytesIO

import pytest
from PIL import Image

from routes.documents import PAGE_IMAGE_RESOLUTION
from tests._local_supabase import upload_via_rest

FIXTURE = "tests/fixtures/clean_digital.pdf"


def _seed_pdf(admin, user_id: str, token: str, *, mime_type: str = "application/pdf") -> str:
    path = f"{user_id}/preview.pdf"
    with open(FIXTURE, "rb") as f:
        resp = upload_via_rest(token, "uploads", path, f.read(), "application/pdf")
    assert resp.status_code in (200, 201), resp.text
    return (
        admin.table("documents")
        .insert(
            {
                "user_id": user_id,
                "filename": "preview.pdf",
                "storage_path": f"uploads/{path}",
                "mime_type": mime_type,
                "size_bytes": 1,
                "status": "ready",
            }
        )
        .execute()
        .data[0]["id"]
    )


@pytest.fixture
def seeded(admin, user_a):
    user_id, token = user_a
    document_id = _seed_pdf(admin, user_id, token)
    yield user_id, token, document_id
    admin.storage.from_("uploads").remove([f"{user_id}/preview.pdf"])


def _get(app_client, token, document_id, page, **bbox):
    return app_client.get(
        f"/documents/{document_id}/pages/{page}/image",
        params=bbox,
        headers={"Authorization": f"Bearer {token}"},
    )


def test_page_image_is_a_png_of_the_page_at_the_preview_resolution(app_client, seeded):
    _user_id, token, document_id = seeded

    response = _get(app_client, token, document_id, 1)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    image = Image.open(BytesIO(response.content))
    import pdfplumber

    with pdfplumber.open(FIXTURE) as pdf:
        page_width_pt = float(pdf.pages[0].width)
    expected_width = round(page_width_pt * PAGE_IMAGE_RESOLUTION / 72)
    assert abs(image.size[0] - expected_width) <= 1


def test_highlight_is_drawn_inside_the_requested_bbox_only(app_client, seeded):
    _user_id, token, document_id = seeded
    bbox = {"x0": 300, "y0": 600, "x1": 500, "y1": 700}  # an empty area near the page bottom

    plain = Image.open(BytesIO(_get(app_client, token, document_id, 1).content)).convert("RGB")
    highlighted = Image.open(BytesIO(_get(app_client, token, document_id, 1, **bbox).content)).convert("RGB")

    scale = PAGE_IMAGE_RESOLUTION / 72
    inside = (int(400 * scale), int(650 * scale))
    outside = (int(100 * scale), int(760 * scale))
    assert highlighted.getpixel(inside) != plain.getpixel(inside)
    assert highlighted.getpixel(outside) == plain.getpixel(outside)


def test_page_out_of_range_is_404(app_client, seeded):
    _user_id, token, document_id = seeded

    response = _get(app_client, token, document_id, 99)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_another_users_document_is_404(app_client, seeded, user_b):
    _user_id, _token, document_id = seeded
    _other_id, other_token = user_b

    response = _get(app_client, other_token, document_id, 1)

    assert response.status_code == 404


def test_non_pdf_document_is_422(app_client, admin, user_a):
    user_id, token = user_a
    document_id = _seed_pdf(admin, user_id, token, mime_type="text/html")
    try:
        response = _get(app_client, token, document_id, 1)
    finally:
        admin.storage.from_("uploads").remove([f"{user_id}/preview.pdf"])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_page_image_requires_auth(app_client, seeded):
    _user_id, _token, document_id = seeded

    assert app_client.get(f"/documents/{document_id}/pages/1/image").status_code == 401
