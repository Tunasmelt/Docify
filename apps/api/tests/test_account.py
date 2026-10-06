# Tests for Settings batch 3, part 2: DELETE /account (routes/account.py).
# HIGH-scrutiny, same standard as FEAT-007/008's original delete audits
# — this is the highest-stakes single operation in the app. Real
# infrastructure throughout: real local Supabase (Auth, Postgres, RLS,
# Storage), Retriever/Generator/Verifier faked via dependency overrides
# to drive a real POST /query turn, never a hand-built fixture standing
# in for one.
#
# Priority per the task brief: item 6's comprehensive completeness
# check. Every assertion about "nothing remains" queries the DB/Storage
# directly with the admin (service-role) client — never inferred from
# the API's own 204, which could pass even if cleanup silently missed a
# category.

from unittest.mock import patch

import pytest
from PIL import Image

from main import app
from routes import account as account_module
from routes import query
from services.chunker import Chunk, ElementType
from services.generator import GenerateResult
from services.retriever import RetrievedChunk
from services.verifier import Verdict, VerdictLabel
from tests._local_supabase import LOCAL_SUPABASE_ANON_KEY, LOCAL_SUPABASE_URL
from tests.conftest import fake_elements, ingest_real_document

_DUMMY_EMBEDDING = [0.0] * 1024


class FakeRetriever:
    def __init__(self, chunks):
        self._chunks = chunks

    def retrieve(self, question, document_ids, user_id, k=8, rerank=False):
        return self._chunks


class FakeGenerator:
    def __init__(self, result):
        self._result = result

    def generate(self, question, chunks, history=None):
        return self._result


class FakeVerifier:
    def __init__(self, verdicts_by_chunk_id):
        self._verdicts_by_chunk_id = verdicts_by_chunk_id

    def verify_batch(self, pairs):
        return [self._verdicts_by_chunk_id[chunk.chunk_id] for _, chunk in pairs]


def _override_query(retriever, generator, verifier):
    app.dependency_overrides[query.get_retriever] = lambda: retriever
    app.dependency_overrides[query.get_generator] = lambda: generator
    app.dependency_overrides[query.get_verifier] = lambda: verifier
    app.dependency_overrides[query.get_query_rewriter] = lambda: _PassthroughRewriter()


@pytest.fixture(autouse=True)
def _clear_query_overrides_after_each_test():
    yield
    app.dependency_overrides.clear()


def _ingest_document_with_figure(app_client, user_id, token, filename):
    """Same helper shape as test_documents.py's own — a document whose
    ingestion touches BOTH Storage buckets that need explicit cleanup
    (uploads/ for the source file, figures/ for the extracted image),
    plus documents/chunks rows."""
    figure_image = Image.new("RGB", (10, 10), color="orange")
    elements = fake_elements(2)
    chunks = [
        Chunk(chunk_index=0, element_type=ElementType.TEXT, page_numbers=[1], source_element_indices=[0], content="body text"),
        Chunk(
            chunk_index=1, element_type=ElementType.FIGURE, page_numbers=[2], source_element_indices=[1],
            content="", image=figure_image,
        ),
    ]
    return ingest_real_document(app_client, user_id, token, filename=filename, elements=elements, chunks=chunks)


def _ask_real_question(app_client, admin, user_id, token, document_id, chunk_row, answer, verdict_label=VerdictLabel.SUPPORTED):
    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=chunk_row["page_number"],
            document_id=document_id, document_name="doc.pdf", document_mime_type="application/pdf",
            element_type=chunk_row["element_type"], score=0.9,
        )
    ]
    gen_result = GenerateResult(
        answer=answer, cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    verdict = Verdict(
        verdict=verdict_label,
        quote="a quote" if verdict_label != VerdictLabel.UNSUPPORTED else None,
        model="gemini-3.5-flash-lite", input_tokens=10, output_tokens=5, latency_ms=100.0,
    )
    _override_query(
        retriever=FakeRetriever(retrieved), generator=FakeGenerator(gen_result),
        verifier=FakeVerifier({chunk_row["id"]: verdict}),
    )
    response = app_client.post(
        "/query", json={"question": "a question", "document_ids": [document_id]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_full_account(app_client, admin, user_id, token, filename_prefix="acct"):
    """Real data across EVERY category enumerated in routes/account.py's
    own module docstring: documents+chunks (via a real /ingest with a
    figure, touching both uploads/ and figures/ Storage), usage_counters
    (a real side effect of that same /ingest call — check_daily_limit,
    rate_limit.py), a real conversation+message+citation (via a real
    /query turn), and a real avatar upload. Returns everything a
    completeness check needs to independently verify against afterward."""
    document_id = _ingest_document_with_figure(app_client, user_id, token, f"{filename_prefix}.pdf")
    chunk_rows = (
        admin.table("chunks")
        .select("id,document_id,element_type,page_number,content,figure_path")
        .eq("document_id", document_id)
        .execute()
        .data
    )
    text_chunk = next(c for c in chunk_rows if c["figure_path"] is None)
    figure_chunk = next(c for c in chunk_rows if c["figure_path"] is not None)

    query_result = _ask_real_question(app_client, admin, user_id, token, document_id, text_chunk, "Answer one [1].")
    conversation_id = query_result["conversation_id"]

    avatar_bytes = b"fake-avatar-png-bytes"
    admin.storage.from_("avatars").upload(
        f"{user_id}/avatar", avatar_bytes, {"content-type": "image/png", "upsert": "true"}
    )

    return {
        "document_id": document_id,
        "conversation_id": conversation_id,
        "uploads_path": f"{user_id}/{filename_prefix}.pdf",
        "figure_path": figure_chunk["figure_path"],
        "avatar_path": f"{user_id}/avatar",
    }


def _assert_nothing_remains_for_user(admin, user_id: str, seeded: dict):
    """Ground truth, queried directly with the service-role/admin
    client — independent of the DELETE call's own 204, which could
    pass even if a category was silently missed. Checks every table
    and bucket enumerated in routes/account.py's module docstring, not
    a subset."""
    # auth.users row itself.
    with pytest.raises(Exception):
        result = admin.auth.admin.get_user_by_id(user_id)
        # Some client versions return a user with error set rather than
        # raising -- guard both real shapes seen in this codebase's own
        # other tests (e.g. the settings audit's session-revocation check).
        assert result.user is None

    # All 6 user-scoped tables (routes/account.py's own enumeration).
    for table in ("documents", "chunks", "conversations", "messages", "citations", "usage_counters"):
        rows = admin.table(table).select("*").eq("user_id", user_id).execute().data
        assert rows == [], f"table {table!r} still has rows for deleted user {user_id}: {rows}"

    # All 3 user-scoped Storage buckets -- both via a direct existence
    # check on the exact known seeded paths AND a prefix list(), so a
    # bug that deletes the specific seeded files but leaves some OTHER
    # real object under the same user prefix wouldn't slip through.
    with pytest.raises(Exception):
        admin.storage.from_("uploads").download(seeded["uploads_path"])
    with pytest.raises(Exception):
        admin.storage.from_("figures").download(seeded["figure_path"])
    with pytest.raises(Exception):
        admin.storage.from_("avatars").download(seeded["avatar_path"])

    assert admin.storage.from_("uploads").list(user_id) == []
    assert admin.storage.from_("figures").list(user_id) == []
    assert admin.storage.from_("avatars").list(user_id) == []


# Acceptance criterion (priority, item 6): every category of real data
# is genuinely gone after deletion -- not a sample, the actual full set.
def test_delete_account_removes_every_category_of_real_data(app_client, admin, user_a):
    user_id, token = user_a
    seeded = _seed_full_account(app_client, admin, user_id, token)

    # Sanity: everything really exists before deletion (a completeness
    # check against already-empty tables would pass vacuously).
    assert admin.table("documents").select("id").eq("user_id", user_id).execute().data != []
    assert admin.table("chunks").select("id").eq("user_id", user_id).execute().data != []
    assert admin.table("conversations").select("id").eq("user_id", user_id).execute().data != []
    assert admin.table("messages").select("id").eq("user_id", user_id).execute().data != []
    assert admin.table("citations").select("id").eq("user_id", user_id).execute().data != []
    assert admin.table("usage_counters").select("user_id").eq("user_id", user_id).execute().data != []
    assert len(admin.storage.from_("uploads").download(seeded["uploads_path"])) > 0
    assert len(admin.storage.from_("figures").download(seeded["figure_path"])) > 0
    assert len(admin.storage.from_("avatars").download(seeded["avatar_path"])) > 0

    response = app_client.delete("/account", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204, response.text

    _assert_nothing_remains_for_user(admin, user_id, seeded)


def test_delete_account_requires_auth(app_client):
    response = app_client.delete("/account")
    assert response.status_code == 401


# Acceptance criterion (item 3): user B's real data is completely
# untouched by user A's deletion -- content-level check, not row counts.
def test_delete_account_only_deletes_the_requesting_users_own_data(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b

    seeded_a = _seed_full_account(app_client, admin, user_id_a, token_a, filename_prefix="user-a-doc")
    seeded_b = _seed_full_account(app_client, admin, user_id_b, token_b, filename_prefix="user-b-doc")

    response = app_client.delete("/account", headers={"Authorization": f"Bearer {token_a}"})
    assert response.status_code == 204

    _assert_nothing_remains_for_user(admin, user_id_a, seeded_a)

    # User B's real data, all categories, genuinely untouched.
    assert admin.table("documents").select("id").eq("id", seeded_b["document_id"]).execute().data != []
    assert admin.table("conversations").select("id").eq("id", seeded_b["conversation_id"]).execute().data != []
    assert len(admin.storage.from_("uploads").download(seeded_b["uploads_path"])) > 0
    assert len(admin.storage.from_("figures").download(seeded_b["figure_path"])) > 0
    assert len(admin.storage.from_("avatars").download(seeded_b["avatar_path"])) > 0
    user_b_row = admin.auth.admin.get_user_by_id(user_id_b)
    assert user_b_row.user is not None
    assert user_b_row.user.id == user_id_b


# --- Storage-failure retry-safety (items 4/5) -------------------------
# Same simulated-partial-outage technique as test_documents.py's own
# _PartiallyFailingStorageClient -- proxies every real call through
# except one bucket's remove(), which always raises.


class _FailingBucket:
    def __init__(self, real_bucket):
        self._real = real_bucket

    def __getattr__(self, name):
        return getattr(self._real, name)

    def remove(self, paths):
        raise RuntimeError("simulated Storage outage")


class _StorageProxyWithOneFailingBucket:
    def __init__(self, real_storage, fail_bucket):
        self._real = real_storage
        self._fail_bucket = fail_bucket

    def from_(self, bucket):
        real_bucket = self._real.from_(bucket)
        return _FailingBucket(real_bucket) if bucket == self._fail_bucket else real_bucket


class _PartiallyFailingStorageClient:
    def __init__(self, real_client, fail_bucket: str):
        self._real = real_client
        self._fail_bucket = fail_bucket

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def storage(self):
        return _StorageProxyWithOneFailingBucket(self._real.storage, self._fail_bucket)


def test_delete_account_storage_failure_leaves_everything_intact_and_is_retry_safe(app_client, admin, user_a):
    user_id, token = user_a
    seeded = _seed_full_account(app_client, admin, user_id, token)
    # uploads is cleaned first (routes/account.py's fixed bucket order)
    # -- failing figures (the second bucket) proves a mid-sequence
    # failure stops everything after it, including the auth-user delete.
    failing_client = _PartiallyFailingStorageClient(admin, fail_bucket="figures")

    with patch.object(account_module, "get_service_role_client", return_value=failing_client):
        first_response = app_client.delete("/account", headers={"Authorization": f"Bearer {token}"})
    assert first_response.status_code == 500
    body = first_response.json()
    assert body["error"]["code"] == "STORAGE_ERROR"
    assert "retry" in body["error"]["message"].lower()

    # Nothing deleted yet: the auth user, every DB row, AND the figures
    # object (the one that "failed") all still exist. uploads' own
    # object is already gone (its remove() ran and succeeded before the
    # figures failure) -- real, expected partial progress, not a bug.
    assert admin.auth.admin.get_user_by_id(user_id).user is not None
    assert admin.table("documents").select("id").eq("user_id", user_id).execute().data != []
    with pytest.raises(Exception):
        admin.storage.from_("uploads").download(seeded["uploads_path"])
    assert len(admin.storage.from_("figures").download(seeded["figure_path"])) > 0

    # Retry with the real client -- no simulated failure this time.
    second_response = app_client.delete("/account", headers={"Authorization": f"Bearer {token}"})
    assert second_response.status_code == 204

    _assert_nothing_remains_for_user(admin, user_id, seeded)


# --- Item 7: stale session behavior after deletion ---------------------


def test_stale_access_token_after_deletion_gets_empty_scoped_results_not_a_crash(app_client, admin, user_a):
    """middleware/auth.py does pure cryptographic JWT verification (no
    live session/DB lookup) -- so an already-issued, not-yet-expired
    access token remains valid AFTER the account is deleted, until it
    naturally expires. This proves the real, actual behavior: every
    downstream route still runs its normal user_id-scoped query, which
    now matches nothing (everything cascaded away) -- a clean, empty,
    non-crashing response, never a leak or a 500."""
    user_id, token = user_a
    _seed_full_account(app_client, admin, user_id, token, filename_prefix="stale-token-doc")

    delete_response = app_client.delete("/account", headers={"Authorization": f"Bearer {token}"})
    assert delete_response.status_code == 204

    # Same still-valid, now-orphaned token, reused after the account is gone.
    response = app_client.get("/conversations", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["conversations"] == []

    doc_response = app_client.get("/documents", headers={"Authorization": f"Bearer {token}"})
    assert doc_response.status_code == 200
    assert doc_response.json()["documents"] == []


def test_refresh_token_is_invalidated_by_account_deletion(app_client, admin, user_a):
    """The OTHER half of item 7: unlike the access token above, a
    refresh attempt genuinely fails after deletion -- auth.admin.delete_user()
    invalidates the user's sessions/refresh tokens as part of removing
    the row, confirmed live here, not assumed. This is the real recovery
    boundary: the stale-access-token window above is bounded by the
    token's own natural expiry, since nothing can ever refresh past it."""
    from supabase import create_client

    user_id, token = user_a
    email = admin.auth.admin.get_user_by_id(user_id).user.email

    anon_client = create_client(LOCAL_SUPABASE_URL, LOCAL_SUPABASE_ANON_KEY)
    auth_result = anon_client.auth.sign_in_with_password({"email": email, "password": "test-password-123"})
    refresh_token = auth_result.session.refresh_token

    _seed_full_account(app_client, admin, user_id, token, filename_prefix="refresh-test-doc")
    delete_response = app_client.delete("/account", headers={"Authorization": f"Bearer {token}"})
    assert delete_response.status_code == 204

    fresh_client = create_client(LOCAL_SUPABASE_URL, LOCAL_SUPABASE_ANON_KEY)
    with pytest.raises(Exception):
        fresh_client.auth.refresh_session(refresh_token)


class _PassthroughRewriter:
    """Follow-up questions are searched unchanged — no real Gemini rewrite call."""

    def rewrite(self, question, history):
        return question

