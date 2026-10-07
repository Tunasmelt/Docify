"""Reindex swaps chunks atomically (migration 20261006_003).

Reindex used to delete a document's chunks before reprocessing it, so the
document answered nothing while it ran and stayed empty if reprocessing
failed. New chunks are now staged and swapped in one transaction."""

import pytest
from PIL import Image

from services.chunker import Chunk
from services.document_model import ElementType, ParseError
from tests.conftest import FakeChunker, clear_pipeline_override, fake_elements, ingest_real_document, override_pipeline


def _chunk_ids(admin, document_id: str) -> set[str]:
    return {r["id"] for r in admin.table("chunks").select("id").eq("document_id", document_id).execute().data}


def _staged(admin, document_id: str) -> list:
    return admin.table("chunks_staging").select("id").eq("document_id", document_id).execute().data


def _reindex(app_client, token, document_id, **pipeline):
    override_pipeline(**pipeline)
    try:
        return app_client.post(f"/reindex/{document_id}", headers={"Authorization": f"Bearer {token}"})
    finally:
        clear_pipeline_override()


def test_old_chunks_stay_live_until_the_new_ones_are_swapped_in(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="live.pdf")
    old_ids = _chunk_ids(admin, document_id)
    assert old_ids

    seen_during_reindex: list[set[str]] = []

    class _ObservingChunker(FakeChunker):
        def chunk(self, parsed_document):
            seen_during_reindex.append(_chunk_ids(admin, document_id))
            return super().chunk(parsed_document)

    assert _reindex(app_client, token, document_id, chunker=_ObservingChunker()).status_code == 202

    assert seen_during_reindex == [old_ids]  # still searchable mid-reindex
    new_ids = _chunk_ids(admin, document_id)
    assert new_ids and not (new_ids & old_ids)
    assert _staged(admin, document_id) == []


def test_failed_reindex_keeps_the_old_chunks(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="keep.pdf")
    old_ids = _chunk_ids(admin, document_id)

    class _BrokenParser:
        def parse(self, file_bytes, filename="x.pdf"):
            raise ParseError("Failed to parse PDF: simulated corruption")

    assert _reindex(app_client, token, document_id, parser=_BrokenParser()).status_code == 202

    row = admin.table("documents").select("status,error").eq("id", document_id).execute().data[0]
    assert row["status"] == "failed" and "simulated corruption" in row["error"]
    assert _chunk_ids(admin, document_id) == old_ids  # used to be empty
    assert _staged(admin, document_id) == []


class _FigureChunker:
    """One text chunk and one figure chunk, with a fresh image each run (the
    pipeline closes chunk images when it's done)."""

    def chunk(self, parsed_document):
        return [
            Chunk(chunk_index=0, element_type=ElementType.TEXT, page_numbers=[1], source_element_indices=[0], content="text"),
            Chunk(
                chunk_index=1,
                element_type=ElementType.FIGURE,
                page_numbers=[1],
                source_element_indices=[1],
                content="Figure 1",
                image=Image.new("RGB", (8, 8), "red"),
            ),
        ]


@pytest.mark.parametrize("cited", [False, True])
def test_reindex_replaces_figures_and_removes_the_old_objects(app_client, admin, user_a, cited):
    user_id, token = user_a
    override_pipeline(chunker=_FigureChunker())
    try:
        from tests.conftest import upload_placeholder

        storage_path = upload_placeholder(user_id, token, filename="fig.pdf")
        response = app_client.post(
            "/ingest",
            json={"storage_path": storage_path, "filename": "fig.pdf", "mime_type": "application/pdf", "size_bytes": 17},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        clear_pipeline_override()
    document_id = response.json()["document_id"]

    def figure_path():
        rows = admin.table("chunks").select("figure_path").eq("document_id", document_id).eq("archived", False).execute().data
        return next(r["figure_path"] for r in rows if r["figure_path"])

    old_path = figure_path()
    if cited:
        from tests.test_conversations import _ask_real_question
        figure = admin.table("chunks").select("*").eq("document_id", document_id).eq("element_type", "figure").execute().data[0]
        _ask_real_question(app_client, admin, user_id, token, document_id, figure, "Figure 1 shows the result [1].")
    assert admin.storage.from_("figures").download(old_path)

    assert _reindex(app_client, token, document_id, chunker=_FigureChunker()).status_code == 202

    new_path = figure_path()
    assert new_path != old_path
    assert admin.storage.from_("figures").download(new_path)
    listing = admin.storage.from_("figures").list(old_path.rsplit("/", 1)[0])
    assert any(item["name"] == old_path.rsplit("/", 1)[1] for item in listing) is cited
    if cited:
        assert admin.storage.from_("figures").download(old_path)
    admin.storage.from_("figures").remove([new_path, old_path] if cited else [new_path])


def test_first_ingest_also_goes_through_staging(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="fresh.pdf", elements=fake_elements(3))

    assert len(_chunk_ids(admin, document_id)) == 3
    assert _staged(admin, document_id) == []


def test_a_failure_after_the_swap_keeps_the_new_figures_the_live_chunks_use(app_client, admin, user_a, monkeypatch):
    from db import queries
    from routes import ingest
    from tests.conftest import FakeEmbedder, FakeParser, upload_placeholder

    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="late-fail.pdf")
    doc = admin.table("documents").insert(
        {"user_id": user_id, "filename": "late-fail.pdf", "storage_path": storage_path, "mime_type": "application/pdf", "size_bytes": 17}
    ).execute().data[0]

    def flaky_mark_ready(client, document_id):
        raise ConnectionError("simulated DB blip after the swap")

    monkeypatch.setattr(queries, "mark_ready", flaky_mark_ready)
    with pytest.raises(ingest.TransientIngestError):
        ingest.run_ingest_pipeline(
            document_id=doc["id"], user_id=user_id, storage_path=storage_path, client=admin,
            parser=FakeParser(), chunker=_FigureChunker(), embedder=FakeEmbedder(), final_attempt=False,
        )

    rows = admin.table("chunks").select("figure_path").eq("document_id", doc["id"]).execute().data
    live_figure = next(r["figure_path"] for r in rows if r["figure_path"])
    assert admin.storage.from_("figures").download(live_figure)  # not cleaned up
    admin.storage.from_("figures").remove([live_figure])


def test_reindex_preserves_saved_citations_and_excludes_old_evidence_from_search(app_client, admin, user_a):
    from tests.test_conversations import _ask_real_question
    from db import queries

    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="versions.pdf")
    old = admin.table("chunks").select("*").eq("document_id", document_id).execute().data[0]
    turn = _ask_real_question(app_client, admin, user_id, token, document_id, old, "Revenue grew 12% [1].")
    before = app_client.get(f"/conversations/{turn['conversation_id']}/messages", headers={"Authorization": f"Bearer {token}"}).json()
    assert _reindex(app_client, token, document_id).status_code == 202
    after = app_client.get(f"/conversations/{turn['conversation_id']}/messages", headers={"Authorization": f"Bearer {token}"}).json()
    assert after["messages"][-1]["citations"] == before["messages"][-1]["citations"]
    archived = admin.table("chunks").select("archived").eq("id", old["id"]).execute().data[0]
    assert archived["archived"] is True
    current = queries.list_document_chunks(admin, document_id=document_id, user_id=user_id)
    assert current and all(c["id"] != old["id"] for c in current)
    for name, params in [
        ("match_chunks_by_fts", {"query_text": old["content"]}),
        ("match_chunks_by_vector", {"query_embedding": old["embedding"], "match_provider": old["embedding_provider"]}),
    ]:
        rows = admin.rpc(name, dict(params, match_user_id=user_id, match_document_ids=[document_id], match_limit=100)).execute().data
        assert rows and all(r["id"] != old["id"] for r in rows)

    admin.table("chunks").update({"embedding_provider": "cohere"}).eq("id", old["id"]).execute()
    providers = admin.rpc("distinct_embedding_providers", {"match_user_id": user_id, "match_document_ids": [document_id]}).execute().data
    assert providers == [{"embedding_provider": "voyage"}]
