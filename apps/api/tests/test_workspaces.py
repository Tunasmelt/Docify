"""Personal workspaces (routes/workspaces.py, migrations/20261007_002).
Real Auth/Postgres/Storage like the rest of the integration suite; only
Docling and Voyage are faked."""

import pytest

from tests.conftest import FakeChunker, FakeEmbedder, FakeParser, clear_pipeline_override, override_pipeline, upload_placeholder


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _workspaces(app_client, token):
    response = app_client.get("/workspaces", headers=_auth(token))
    assert response.status_code == 200
    return response.json()["workspaces"]


def _create(app_client, token, name):
    return app_client.post("/workspaces", json={"name": name}, headers=_auth(token))


def _ingest(app_client, user_id, token, *, workspace_id=None, filename="doc.pdf"):
    storage_path = upload_placeholder(user_id, token, filename=filename)
    override_pipeline(parser=FakeParser(), chunker=FakeChunker(), embedder=FakeEmbedder())
    body = {"storage_path": storage_path, "filename": filename, "mime_type": "application/pdf", "size_bytes": 17}
    if workspace_id is not None:
        body["workspace_id"] = workspace_id
    try:
        return app_client.post("/ingest", json=body, headers=_auth(token))
    finally:
        clear_pipeline_override()


def test_a_new_user_gets_one_default_workspace_and_listing_is_idempotent(app_client, user_a):
    _, token = user_a

    first = _workspaces(app_client, token)
    second = _workspaces(app_client, token)

    assert [w["name"] for w in first] == ["My workspace"]
    assert first == second
    assert first[0]["document_count"] == 0


def test_create_and_rename_a_workspace(app_client, user_a):
    _, token = user_a

    created = _create(app_client, token, "  Research  ")
    assert created.status_code == 201 and created.json()["name"] == "Research"

    renamed = app_client.patch(f"/workspaces/{created.json()['id']}", json={"name": "Taxes 2026"}, headers=_auth(token))
    assert renamed.status_code == 200 and renamed.json()["name"] == "Taxes 2026"
    assert sorted(w["name"] for w in _workspaces(app_client, token)) == ["My workspace", "Taxes 2026"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 61, "bad\nname"])
def test_workspace_names_are_validated(app_client, user_a, name):
    _, token = user_a

    assert _create(app_client, token, name).status_code == 422


def test_workspace_names_are_unique_per_user_ignoring_case(app_client, user_a, user_b):
    _, token_a = user_a
    _, token_b = user_b
    assert _create(app_client, token_a, "Research").status_code == 201

    assert _create(app_client, token_a, "research").status_code == 409
    assert _create(app_client, token_a, "my WORKSPACE").status_code == 409  # the default one counts too
    assert _create(app_client, token_b, "Research").status_code == 201  # another user may reuse a name


def test_rename_to_an_existing_name_conflicts_but_keeping_your_own_name_is_fine(app_client, user_a):
    _, token = user_a
    other = _create(app_client, token, "Other").json()
    assert _create(app_client, token, "Research").status_code == 201

    assert app_client.patch(f"/workspaces/{other['id']}", json={"name": "RESEARCH"}, headers=_auth(token)).status_code == 409
    assert app_client.patch(f"/workspaces/{other['id']}", json={"name": "OTHER"}, headers=_auth(token)).status_code == 200


def test_a_user_can_have_at_most_twenty_workspaces(app_client, user_a):
    _, token = user_a
    for index in range(19):  # plus the default one
        assert _create(app_client, token, f"w{index}").status_code == 201

    assert _create(app_client, token, "one too many").status_code == 409


def test_workspaces_are_invisible_and_untouchable_to_other_users(app_client, user_a, user_b):
    _, token_a = user_a
    _, token_b = user_b
    mine = _create(app_client, token_a, "Private").json()
    _workspaces(app_client, token_b)

    assert "Private" not in [w["name"] for w in _workspaces(app_client, token_b)]
    assert app_client.patch(f"/workspaces/{mine['id']}", json={"name": "Hijacked"}, headers=_auth(token_b)).status_code == 404
    assert app_client.delete(f"/workspaces/{mine['id']}", headers=_auth(token_b)).status_code == 404
    assert "Private" in [w["name"] for w in _workspaces(app_client, token_a)]


def test_a_document_without_a_workspace_goes_to_the_default_one(app_client, user_a):
    user_id, token = user_a
    default_id = _workspaces(app_client, token)[0]["id"]

    response = _ingest(app_client, user_id, token)

    assert response.status_code == 202
    document = app_client.get(f"/documents/{response.json()['document_id']}", headers=_auth(token)).json()
    assert document["workspace_id"] == default_id


def test_documents_are_filed_and_listed_by_workspace(app_client, user_a):
    user_id, token = user_a
    research = _create(app_client, token, "Research").json()["id"]
    default_id = next(w["id"] for w in _workspaces(app_client, token) if w["name"] == "My workspace")

    in_research = _ingest(app_client, user_id, token, workspace_id=research, filename="a.pdf").json()["document_id"]
    in_default = _ingest(app_client, user_id, token, filename="b.pdf").json()["document_id"]

    def listed(**params):
        response = app_client.get("/documents", params=params, headers=_auth(token))
        assert response.status_code == 200
        return {d["id"] for d in response.json()["documents"]}

    assert listed(workspace_id=research) == {in_research}
    assert listed(workspace_id=default_id) == {in_default}
    assert listed() == {in_research, in_default}  # no filter: every workspace
    counts = {w["id"]: w["document_count"] for w in _workspaces(app_client, token)}
    assert counts == {research: 1, default_id: 1}


def test_ingesting_into_someone_elses_workspace_is_refused_and_creates_nothing(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    _, token_b = user_b
    theirs = _create(app_client, token_b, "Theirs").json()["id"]

    response = _ingest(app_client, user_id_a, token_a, workspace_id=theirs)

    assert response.status_code == 404
    assert admin.table("documents").select("id").eq("user_id", user_id_a).execute().data == []


def test_a_malformed_workspace_id_is_a_validation_error(app_client, user_a):
    _, token = user_a

    assert app_client.get("/documents", params={"workspace_id": "nope"}, headers=_auth(token)).status_code == 422
    assert app_client.get("/conversations", params={"workspace_id": "nope"}, headers=_auth(token)).status_code == 422
    assert app_client.delete("/workspaces/nope", headers=_auth(token)).status_code == 422


def test_conversations_follow_their_documents_workspace(app_client, admin, user_a):
    user_id, token = user_a
    research = _create(app_client, token, "Research").json()["id"]
    default_id = next(w["id"] for w in _workspaces(app_client, token) if w["name"] == "My workspace")
    research_doc = _ingest(app_client, user_id, token, workspace_id=research, filename="a.pdf").json()["document_id"]

    # Inserted the way create_query_turn does: no workspace_id given.
    in_research = admin.table("conversations").insert({"user_id": user_id, "title": "r", "document_ids": [research_doc]}).execute().data[0]
    in_default = admin.table("conversations").insert({"user_id": user_id, "title": "d", "document_ids": []}).execute().data[0]

    assert in_research["workspace_id"] == research
    assert in_default["workspace_id"] == default_id

    def listed(**params):
        response = app_client.get("/conversations", params=params, headers=_auth(token))
        assert response.status_code == 200
        return {c["id"]: c["workspace_id"] for c in response.json()["conversations"]}

    assert listed(workspace_id=research) == {in_research["id"]: research}
    assert listed(workspace_id=default_id) == {in_default["id"]: default_id}
    assert set(listed()) == {in_research["id"], in_default["id"]}


def test_you_cannot_delete_your_only_workspace(app_client, user_a):
    _, token = user_a
    only = _workspaces(app_client, token)[0]["id"]

    assert app_client.delete(f"/workspaces/{only}", headers=_auth(token)).status_code == 409


def test_a_workspace_with_documents_is_not_deleted_until_they_are(app_client, user_a):
    user_id, token = user_a
    research = _create(app_client, token, "Research").json()["id"]
    document_id = _ingest(app_client, user_id, token, workspace_id=research).json()["document_id"]

    assert app_client.delete(f"/workspaces/{research}", headers=_auth(token)).status_code == 409

    assert app_client.delete(f"/documents/{document_id}", headers=_auth(token)).status_code == 204
    assert app_client.delete(f"/workspaces/{research}", headers=_auth(token)).status_code == 204
    assert research not in [w["id"] for w in _workspaces(app_client, token)]


def test_deleting_an_empty_workspace_deletes_its_conversations(app_client, admin, user_a):
    user_id, token = user_a
    _workspaces(app_client, token)
    scratch = _create(app_client, token, "Scratch").json()["id"]
    conversation = admin.table("conversations").insert(
        {"user_id": user_id, "title": "t", "document_ids": [], "workspace_id": scratch}
    ).execute().data[0]

    assert app_client.delete(f"/workspaces/{scratch}", headers=_auth(token)).status_code == 204
    assert admin.table("conversations").select("id").eq("id", conversation["id"]).execute().data == []


def test_workspace_routes_require_auth(app_client):
    assert app_client.get("/workspaces").status_code == 401
    assert app_client.post("/workspaces", json={"name": "x"}).status_code == 401


@pytest.mark.parametrize("endpoint", ["/query", "/query/stream"])
def test_query_refuses_documents_from_different_workspaces(app_client, user_a, endpoint):
    user_id, token = user_a
    research = _create(app_client, token, "Research").json()["id"]
    first = _ingest(app_client, user_id, token, filename="a.pdf").json()["document_id"]
    second = _ingest(app_client, user_id, token, workspace_id=research, filename="b.pdf").json()["document_id"]
    response = app_client.post(endpoint, json={"question": "Compare", "document_ids": [first, second]}, headers=_auth(token))
    assert response.status_code == 422
    assert "one workspace" in response.json()["error"]["message"]


def test_database_refuses_a_document_in_another_users_workspace(app_client, admin, user_a, user_b):
    user_id, token = user_a
    _, other_token = user_b
    theirs = _create(app_client, other_token, "Private").json()["id"]
    document = _ingest(app_client, user_id, token).json()["document_id"]
    with pytest.raises(Exception, match="workspace does not belong"):
        admin.table("documents").update({"workspace_id": theirs}).eq("id", document).execute()


def test_database_refuses_conversation_documents_outside_its_workspace(app_client, admin, user_a):
    user_id, token = user_a
    default = _workspaces(app_client, token)[0]["id"]
    other = _create(app_client, token, "Other").json()["id"]
    document = _ingest(app_client, user_id, token, workspace_id=other).json()["document_id"]
    with pytest.raises(Exception, match="documents must belong"):
        admin.table("conversations").insert({"user_id": user_id, "workspace_id": default, "document_ids": [document]}).execute()


@pytest.mark.parametrize("endpoint", ["/query", "/query/stream"])
def test_query_cannot_change_an_existing_conversations_workspace(app_client, admin, user_a, endpoint):
    user_id, token = user_a
    other = _create(app_client, token, "Other").json()["id"]
    first = _ingest(app_client, user_id, token, filename="a.pdf").json()["document_id"]
    second = _ingest(app_client, user_id, token, workspace_id=other, filename="b.pdf").json()["document_id"]
    conversation = admin.table("conversations").insert({"user_id": user_id, "document_ids": [first]}).execute().data[0]
    response = app_client.post(endpoint, json={"question": "Compare", "document_ids": [second], "conversation_id": conversation["id"]}, headers=_auth(token))
    assert response.status_code == 422
    assert "conversation's workspace" in response.json()["error"]["message"]


def test_workspace_rls_enforces_owner_for_direct_browser_access(app_client, user_a, user_b):
    from supabase import create_client
    from tests._local_supabase import LOCAL_SUPABASE_URL, LOCAL_SUPABASE_ANON_KEY
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    mine = _workspaces(app_client, token_a)[0]["id"]
    theirs = _create(app_client, token_b, "Private").json()["id"]
    client = create_client(LOCAL_SUPABASE_URL, LOCAL_SUPABASE_ANON_KEY)
    client.postgrest.auth(token_a)
    assert {w["id"] for w in client.table("workspaces").select("id").execute().data} == {mine}
    assert client.table("workspaces").update({"name": "Hijacked"}).eq("id", theirs).execute().data == []
    with pytest.raises(Exception, match="row-level security"):
        client.table("workspaces").insert({"user_id": user_id_b, "name": "Injected"}).execute()
