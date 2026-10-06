"""Original-file viewing: real local Auth, Postgres and Storage."""
import uuid
import httpx
from tests.test_page_image import seeded  # noqa: F401


def test_original_pdf_is_viewable(app_client, seeded):
    _, token, document_id = seeded
    response = app_client.get(f"/documents/{document_id}/file", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["mime_type"] == "application/pdf"
    original = httpx.get(response.json()["url"])
    assert original.status_code == 200
    assert original.content.startswith(b"%PDF")


def test_original_file_is_private(app_client, seeded, user_b):
    _, _, document_id = seeded
    _, other_token = user_b
    headers = {"Authorization": f"Bearer {other_token}"}
    foreign = app_client.get(f"/documents/{document_id}/file", headers=headers)
    missing = app_client.get(f"/documents/{uuid.uuid4()}/file", headers=headers)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert app_client.get(f"/documents/{document_id}/file").status_code == 401
