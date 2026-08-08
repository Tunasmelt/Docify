# Tests for [FEAT-024] rate limiting on POST /ingest, POST /query, and
# POST /query/stream.
#
# The limiter is disabled globally by conftest.py so the rest of the
# suite (which legitimately makes several real requests per session)
# isn't accidentally throttled — this file is the one place it's turned
# back on, with its in-memory counters reset before and after every
# test so tests here can't interfere with each other or leak state into
# whatever test runs next in the same session.
#
# Real HTTP requests throughout (TestClient, real Auth, real routes) —
# the thing being tested is the ACTUAL 429 behavior, not slowapi's own
# claimed behavior. To trigger a rate-limit "hit" cheaply and fast
# (without a real Docling/Voyage/Gemini call each time), every test
# below uses a request that's valid enough to reach the route body —
# and therefore the limiter, which runs before anything else in that
# body — but is deliberately rejected downstream (a wrong-owner
# document_id/storage_path, 403) so it never needs the real pipeline.

import time

import pytest

from main import app
from routes.ingest import INGEST_GLOBAL_MINUTE_LIMIT, INGEST_MINUTE_LIMIT
from routes.query import QUERY_MINUTE_LIMIT
from services.generator import GenerateResult, GenerateStreamResult
from services.retriever import RetrievedChunk
from services.verifier import VerdictLabel
from tests.conftest import override_pipeline, clear_pipeline_override, ingest_real_document, upload_placeholder
from tests.test_query import FakeGenerator, FakeRetriever, _clear_overrides, _override, _real_chunk_row, _verdict

NIL_UUID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _enable_rate_limiter():
    app.state.limiter.enabled = True
    app.state.limiter.reset()
    yield
    app.state.limiter.reset()
    app.state.limiter.enabled = False


def _ingest_request(app_client, token, filename="probe.pdf"):
    # A storage_path that fails validate_storage_path()'s ownership
    # check (wrong prefix) -- reaches the route body (and therefore the
    # limiter) but 403s immediately after, never touching real Docling/
    # Voyage. Cheap and fast, real per this file's own module docstring.
    return app_client.post(
        "/ingest",
        json={
            "storage_path": f"uploads/{NIL_UUID}/{filename}",
            "filename": filename,
            "mime_type": "application/pdf",
            "size_bytes": 17,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def _reindex_request(app_client, token):
    # A nonexistent document_id -- reaches the route (and therefore the
    # rate limiter, which runs before the route body via the decorator)
    # but 404s immediately after, never touching a real pipeline.
    return app_client.post(f"/reindex/{NIL_UUID}", headers={"Authorization": f"Bearer {token}"})


def _query_request(app_client, token):
    # A document_id that doesn't belong to the user -- reaches the
    # route body (and the limiter) but 403s immediately after
    # documents_owned_by_user(), never touching real retrieval/generation.
    return app_client.post(
        "/query",
        json={"question": "probe", "document_ids": [NIL_UUID]},
        headers={"Authorization": f"Bearer {token}"},
    )


def _query_stream_request(app_client, token):
    return app_client.post(
        "/query/stream",
        json={"question": "probe", "document_ids": [NIL_UUID]},
        headers={"Authorization": f"Bearer {token}"},
    )


# Acceptance criterion: exceeding /ingest's per-minute limit returns a real 429 matching API_CONTRACT.md's error envelope
def test_ingest_rate_limit_exceeded_returns_429_with_error_envelope(app_client, admin, user_a):
    user_id, token = user_a
    per_minute = int(INGEST_MINUTE_LIMIT.split("/")[0])

    for _ in range(per_minute):
        resp = _ingest_request(app_client, token)
        assert resp.status_code == 403, f"expected the pre-limit requests to 403 (wrong owner), got {resp.status_code}: {resp.text}"

    resp = _ingest_request(app_client, token)
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert "message" in body["error"]
    # Standard slowapi headers, confirmed present -- real clients use
    # these to back off correctly instead of guessing.
    assert "Retry-After" in resp.headers or "retry-after" in resp.headers


# Acceptance criterion: rate limiting is keyed per-user, not shared/per-IP -- one user's own per-user counter never blocks another user's own per-user counter
#
# 2026-08-02 (FEAT-024 follow-up): this test used to also drive user_a one
# request PAST their own per-user limit (a 3rd, 429'd request) before
# testing user_b -- that additional request is deliberately NOT made here
# anymore. INGEST_GLOBAL_MINUTE_LIMIT ("3/minute", checked before the
# per-user limit -- see test_global_rate_limit_trips_on_combined_usage_
# across_two_users below for why decorator order matters here) means a
# 3rd real attempt from user_a, even a per-user-rejected one, still
# increments the shared global counter to 3 -- exactly the shared account-
# level ceiling this same feature exists to protect. Combined with a 4th
# request from user_b, that combination WOULD legitimately trip the
# global limit -- correct new behavior, not a bug, but a different claim
# than "per-user counters are independent," which is this test's own,
# narrower scope. Staying at exactly `per_minute` requests for user_a
# keeps the global counter at 2 (well under its own 3/minute cap), so
# user_b's first request here is only ever gated by user_b's OWN
# per-user counter, isolating the claim this test actually makes.
def test_ingest_rate_limit_is_per_user_not_shared(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    per_minute = int(INGEST_MINUTE_LIMIT.split("/")[0])

    for _ in range(per_minute):
        resp = _ingest_request(app_client, token_a)
        assert resp.status_code == 403

    # A completely different real user, same TestClient, same in-memory
    # limiter instance -- must NOT be affected by user_a's own per-user
    # usage (user_a is AT their own cap here, not yet rejected by it).
    resp_b = _ingest_request(app_client, token_b)
    assert resp_b.status_code == 403, f"user_b was incorrectly rate-limited by user_a's per-user usage: {resp_b.status_code} {resp_b.text}"


# Acceptance criterion (Part 1, item 2): the GLOBAL limit trips on COMBINED
# usage across different real users, independent of either user's own
# per-user count -- the specific scenario a per-user-only design
# structurally cannot catch (N users each within their own limit still
# collectively exceeding the real shared vendor ceiling).
def test_global_rate_limit_trips_on_combined_usage_across_two_users(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    global_per_minute = int(INGEST_GLOBAL_MINUTE_LIMIT.split("/")[0])

    # 3 requests alternating between two different real users -- user_a
    # makes 2 (their own full per-user allowance), user_b makes 1 (well
    # under theirs). Neither user's OWN counter is exceeded by this, but
    # combined they exactly exhaust the shared 3/minute global budget.
    resp = _ingest_request(app_client, token_a)
    assert resp.status_code == 403
    resp = _ingest_request(app_client, token_b)
    assert resp.status_code == 403
    resp = _ingest_request(app_client, token_a)
    assert resp.status_code == 403

    # A 4th combined request, from user_b again -- user_b's OWN per-user
    # count would only reach 2 (still within their own 2/minute cap), but
    # the SHARED global counter reaches 4, over its 3/minute cap. Must be
    # rejected, and specifically by the GLOBAL limit (verified via its
    # own distinct value in the message, not either user's per-user one).
    resp = _ingest_request(app_client, token_b)
    assert resp.status_code == 429, f"expected the 4th combined request (within both users' own per-user caps) to trip the global limit: {resp.status_code} {resp.text}"
    message = resp.json()["error"]["message"]
    assert f"{global_per_minute} per 1 minute" in message, f"expected the GLOBAL limit's own value ({global_per_minute}/minute) to be what bound here, got: {message!r}"


# Acceptance criterion (Part 1, item 3): the global limit must never bind
# for a single active user well under their own per-user limit -- it
# should only ever be the tighter constraint when combined usage genuinely
# warrants it, never as an accidental side effect of existing alongside
# the per-user limit.
def test_global_rate_limit_does_not_bind_for_a_single_user_under_their_own_limit(app_client, admin, user_a):
    user_id, token = user_a
    per_minute = int(INGEST_MINUTE_LIMIT.split("/")[0])
    global_per_minute = int(INGEST_GLOBAL_MINUTE_LIMIT.split("/")[0])
    assert per_minute < global_per_minute, (
        "this test's premise (global should never bind for a lone user) requires the per-user "
        "cap to stay strictly tighter than the global one -- re-check these constants if this fires"
    )

    for _ in range(per_minute):
        resp = _ingest_request(app_client, token)
        assert resp.status_code == 403

    # One more, past user_a's OWN cap -- rejected, but by the PER-USER
    # limit specifically. user_a is the only active user in this test, so
    # the shared global counter is nowhere near its own, looser cap --
    # confirmed by checking which limit's value shows up in the message,
    # not just that SOME 429 happened.
    resp = _ingest_request(app_client, token)
    assert resp.status_code == 429
    message = resp.json()["error"]["message"]
    assert f"{per_minute} per 1 minute" in message, (
        f"expected the PER-USER limit to be what bound here (global must never fire for a lone "
        f"user under it), got: {message!r}"
    )


# Acceptance criterion: an invalid/missing JWT is always rejected with 401 before the limiter ever runs -- never counted, never 429
def test_invalid_or_missing_jwt_never_reaches_the_rate_limiter(app_client):
    per_minute = int(INGEST_MINUTE_LIMIT.split("/")[0])

    # Fire well past what the real per-user limit would be, with no
    # valid auth at all -- every single one must 401, never 429. If the
    # limiter ran before JWTAuthMiddleware (or ran despite a rejected
    # auth), this would flip to 429 partway through instead.
    for _ in range(per_minute + 5):
        resp = app_client.post(
            "/ingest",
            json={"storage_path": "uploads/x/y.pdf", "filename": "y.pdf", "mime_type": "application/pdf", "size_bytes": 1},
        )
        assert resp.status_code == 401, f"expected 401 (no auth), got {resp.status_code}"

    for _ in range(per_minute + 5):
        resp = app_client.post(
            "/ingest",
            json={"storage_path": "uploads/x/y.pdf", "filename": "y.pdf", "mime_type": "application/pdf", "size_bytes": 1},
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )
        assert resp.status_code == 401, f"expected 401 (invalid jwt), got {resp.status_code}"


# Acceptance criterion: POST /query's per-minute limit is enforced with the same real 429 shape
def test_query_rate_limit_exceeded_returns_429(app_client, admin, user_a):
    user_id, token = user_a
    per_minute = int(QUERY_MINUTE_LIMIT.split("/")[0])

    for _ in range(per_minute):
        resp = _query_request(app_client, token)
        assert resp.status_code == 403, f"expected pre-limit requests to 403 (non-owned doc), got {resp.status_code}: {resp.text}"

    resp = _query_request(app_client, token)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


# Acceptance criterion: /query and /query/stream share ONE combined per-user limit, not two independent ones
def test_query_and_query_stream_share_one_rate_limit(app_client, admin, user_a):
    user_id, token = user_a
    per_minute = int(QUERY_MINUTE_LIMIT.split("/")[0])

    # Split the limit's worth of hits across BOTH routes, alternating.
    hits = 0
    route_index = 0
    while hits < per_minute:
        if route_index % 2 == 0:
            resp = _query_request(app_client, token)
        else:
            resp = _query_stream_request(app_client, token)
        assert resp.status_code == 403, f"expected pre-limit request #{hits} to 403, got {resp.status_code}: {resp.text}"
        hits += 1
        route_index += 1

    # The NEXT request, on EITHER route, must now be rate-limited --
    # proving the counter is shared, not independent per route.
    over_limit_on_query = _query_request(app_client, token)
    assert over_limit_on_query.status_code == 429, "expected /query to be rate-limited by /query/stream's own prior usage (shared counter)"


# Acceptance criterion (task item 7): a rate-limited /query/stream request gets a clean 429 -- never an SSE stream that opens and then errors
def test_query_stream_rate_limited_returns_clean_429_not_a_stream(app_client, admin, user_a):
    user_id, token = user_a
    per_minute = int(QUERY_MINUTE_LIMIT.split("/")[0])

    for _ in range(per_minute):
        resp = _query_stream_request(app_client, token)
        assert resp.status_code == 403

    with app_client.stream(
        "POST",
        "/query/stream",
        json={"question": "probe", "document_ids": [NIL_UUID]},
        headers={"Authorization": f"Bearer {token}"},
    ) as resp:
        assert resp.status_code == 429
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" not in content_type, f"rate-limited request must not open an SSE stream, got content-type={content_type!r}"
        assert "application/json" in content_type
        body = resp.read()
        assert b"event:" not in body, "rate-limited response must be a plain JSON error, not any SSE-framed content"
        import json as _json

        parsed = _json.loads(body)
        assert parsed["error"]["code"] == "RATE_LIMITED"


# Acceptance criterion: documents/conversations/export routes are explicitly NOT rate-limited (SCOPE.md scopes this to /ingest + /query only)
def test_documents_and_conversations_routes_are_not_rate_limited(app_client, admin, user_a):
    user_id, token = user_a
    # Comfortably more calls than either /ingest's or /query's real
    # per-minute limit -- if a decorator were accidentally applied to
    # these routes too, this would 429 well before the loop finishes.
    calls = max(int(INGEST_MINUTE_LIMIT.split("/")[0]), int(QUERY_MINUTE_LIMIT.split("/")[0])) + 5
    for _ in range(calls):
        resp = app_client.get("/documents", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, f"documents route was unexpectedly rate-limited: {resp.status_code} {resp.text}"
    for _ in range(calls):
        resp = app_client.get("/conversations", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, f"conversations route was unexpectedly rate-limited: {resp.status_code} {resp.text}"
    # Settings batch 3, part 1 (export): read-only, no Voyage/Gemini calls
    # -- same real-cost profile as documents/conversations above, not a
    # different one (routes/export.py's own module comment). Proven the
    # same way, not just asserted by the absence of a decorator.
    for _ in range(calls):
        resp = app_client.get("/export/conversations", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200, f"export route was unexpectedly rate-limited: {resp.status_code} {resp.text}"


# --- Part 2 (2026-08-02 FEAT-024 follow-up): Postgres-backed daily counters ---
#
# Acceptance criterion (Part 2, item 3): the DAILY limit must survive the
# real failure mode that motivates moving it off slowapi's in-memory
# storage — Render's free tier wiping all in-memory state on every
# ~15-minute idle spin-down. app.state.limiter.reset() (this file's own
# established stand-in for "the process restarted", already used by the
# _enable_rate_limiter fixture above for setup/teardown isolation) clears
# every in-memory per-minute/global counter; the daily Postgres counter
# must NOT reset alongside it.
def test_daily_limit_count_survives_in_memory_state_being_cleared(app_client, admin, user_a):
    user_id, token = user_a

    def real_ingest_request(filename):
        storage_path = upload_placeholder(user_id, token, filename=filename)
        override_pipeline()  # Fake parser/chunker/embedder — no real Voyage/Gemini calls, fast
        try:
            return app_client.post(
                "/ingest",
                json={"storage_path": storage_path, "filename": filename, "mime_type": "application/pdf", "size_bytes": 17},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            clear_pipeline_override()

    # 2 real, successful ingests (a valid storage_path this time, not the
    # wrong-owner one _ingest_request uses elsewhere in this file — the
    # daily-limit check runs AFTER storage_path validation in post_ingest,
    # so a request that 403s there never reaches it) — both within the
    # per-minute limits, daily Postgres counter should now read 2.
    for i in range(2):
        resp = real_ingest_request(f"daily-pre-restart-{i}.pdf")
        assert resp.status_code == 202, f"expected a real successful ingest, got {resp.status_code}: {resp.text}"

    app.state.limiter.reset()

    # 2 more, post-"restart" — per-minute counters are fresh again
    # (proving the reset genuinely cleared them, since 2 more requests
    # succeed instead of immediately 429ing), but the DAILY count must
    # NOT be — it should now read 4, not have reset back to 2.
    for i in range(2):
        resp = real_ingest_request(f"daily-post-restart-{i}.pdf")
        assert resp.status_code == 202, f"post-restart request unexpectedly failed: {resp.status_code}: {resp.text}"

    rows = admin.table("usage_counters").select("count").eq("user_id", user_id).eq("route", "ingest").execute().data
    assert rows, "expected a usage_counters row for this user/route to exist"
    assert rows[0]["count"] == 4, (
        f"expected the daily count to have survived the simulated restart at 4 (2 before + 2 after), "
        f"got {rows[0]['count']} — a value of 2 would mean the count was lost/reset, defeating the "
        f"whole point of moving this off in-memory storage"
    )


# Acceptance criterion (Part 2, item 2 — explicit statement, not silence):
# per-minute limits deliberately stay on slowapi's in-memory storage; only
# the daily ones moved to Postgres. Confirms the per-minute limit is
# genuinely unaffected by usage_counters existing at all — same real 429
# behavior as before this feature, on the same request shape the original
# FEAT-024 tests already use.
def test_per_minute_limit_still_uses_in_memory_storage_not_postgres(app_client, admin, user_a):
    user_id, token = user_a
    per_minute = int(INGEST_MINUTE_LIMIT.split("/")[0])

    for _ in range(per_minute):
        resp = _ingest_request(app_client, token)
        assert resp.status_code == 403

    resp = _ingest_request(app_client, token)
    assert resp.status_code == 429
    # The per-minute rejection happens at storage_path validation time
    # (wrong-owner path), strictly BEFORE post_ingest ever constructs a
    # client or calls check_daily_limit — so no usage_counters row should
    # exist for this user at all yet.
    rows = admin.table("usage_counters").select("count").eq("user_id", user_id).eq("route", "ingest").execute().data
    assert rows == [], f"expected no usage_counters row (per-minute rejection never reaches the daily check), got {rows}"


# --- Part 3, item 4 (2026-08-02 FEAT-024 follow-up): POST /reindex shares --
# --- /ingest's rate-limit infrastructure, not a separate, looser budget ---


# Acceptance criterion: /reindex and /ingest share ONE per-user per-minute
# counter (INGEST_RATE_LIMIT_SCOPE) -- exhausting one via one route trips
# the other immediately, the same discipline query.py already proves for
# /query + /query/stream sharing QUERY_RATE_LIMIT_SCOPE.
def test_reindex_and_ingest_share_one_per_user_per_minute_limit(app_client, admin, user_a):
    user_id, token = user_a
    per_minute = int(INGEST_MINUTE_LIMIT.split("/")[0])

    hits = 0
    while hits < per_minute:
        resp = _ingest_request(app_client, token) if hits % 2 == 0 else _reindex_request(app_client, token)
        assert resp.status_code in (403, 404), f"expected pre-limit request #{hits} to reach its route body, got {resp.status_code}: {resp.text}"
        hits += 1

    # Whichever route comes next, on a counter already exhausted by the
    # OTHER route, must be rejected -- proving one shared counter, not two.
    over_limit = _reindex_request(app_client, token)
    assert over_limit.status_code == 429, "expected /reindex to be rate-limited by /ingest's own prior usage (shared per-user counter)"


# Acceptance criterion: /reindex also draws on the SAME global per-minute
# counter as /ingest -- combined usage across both routes trips it, not
# just combined usage within one route.
def test_reindex_and_ingest_share_the_global_per_minute_limit(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    global_per_minute = int(INGEST_GLOBAL_MINUTE_LIMIT.split("/")[0])

    # 3 requests across both routes AND both users -- reaching the global
    # cap exactly, none of it from a single route or a single user alone.
    resp = _ingest_request(app_client, token_a)
    assert resp.status_code in (403, 404)
    resp = _reindex_request(app_client, token_b)
    assert resp.status_code in (403, 404)
    resp = _ingest_request(app_client, token_a)
    assert resp.status_code in (403, 404)

    # A 4th, on /reindex, from user_b (whose own per-user count would
    # only reach 2, still within their own cap) -- must trip the shared
    # global limit.
    resp = _reindex_request(app_client, token_b)
    assert resp.status_code == 429, f"expected /reindex to be bound by the shared global limit: {resp.status_code} {resp.text}"
    message = resp.json()["error"]["message"]
    assert f"{global_per_minute} per 1 minute" in message, f"expected the GLOBAL limit's own value to be what bound here, got: {message!r}"


# Real, slow: confirms the per-minute window genuinely resets after real
# time passes, not just that slowapi claims a fixed/sliding window
# internally. Gated the same way this project gates other slow-but-not-
# quota-costly real tests (this doesn't touch any real vendor API, it's
# just a real ~65s wall-clock wait).
@pytest.mark.skipif(
    __import__("os").environ.get("RUN_RATE_LIMIT_WINDOW_TEST") != "1",
    reason="set RUN_RATE_LIMIT_WINDOW_TEST=1 to run the real ~65s window-reset test",
)
def test_ingest_rate_limit_window_resets_after_real_time_passes(app_client, admin, user_a):
    user_id, token = user_a
    per_minute = int(INGEST_MINUTE_LIMIT.split("/")[0])

    for _ in range(per_minute):
        resp = _ingest_request(app_client, token)
        assert resp.status_code == 403
    limited = _ingest_request(app_client, token)
    assert limited.status_code == 429

    time.sleep(65)

    recovered = _ingest_request(app_client, token)
    assert recovered.status_code == 403, f"expected the window to have reset (403, not 429) after 65s, got {recovered.status_code}: {recovered.text}"


# --- Regression: the real SUCCESS path with the limiter enabled --------
#
# Every test above deliberately triggers the limiter via a request that
# gets rejected downstream (403) so it never needs the real pipeline —
# which meant NOTHING in this file ever exercised a genuine 200/202
# response with the limiter enabled. That gap let a real, production-
# breaking bug through: slowapi's headers_enabled=True requires any
# route returning a plain Pydantic model (via response_model=, not a
# raw Response) to also declare a `response: Response` parameter for
# FastAPI to inject — without it, a real successful call 500s with
# "parameter `response` must be an instance of starlette.responses.
# Response" the moment slowapi tries to attach rate-limit headers to a
# non-Response return value. Found live (2026-07-30) running a real
# /ingest call against a live server with the limiter enabled — not by
# reading slowapi's docs. These tests exercise exactly that path so it
# can never regress silently again.


def test_ingest_success_path_with_limiter_enabled_returns_202_not_500(app_client, admin, user_a):
    user_id, token = user_a
    storage_path = upload_placeholder(user_id, token, filename="real.pdf")
    override_pipeline()
    try:
        resp = app_client.post(
            "/ingest",
            json={"storage_path": storage_path, "filename": "real.pdf", "mime_type": "application/pdf", "size_bytes": 17},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        clear_pipeline_override()

    assert resp.status_code == 202, f"real success path must return 202, not 500: {resp.status_code} {resp.text}"
    body = resp.json()
    assert "document_id" in body
    # headers_enabled=True must produce real headers on a SUCCESSFUL
    # response too, not just on a 429 (rate_limit.py's own comment) —
    # confirmed here since this is the response path that broke.
    assert any(h.lower() == "x-ratelimit-limit" for h in resp.headers), f"missing X-RateLimit-* headers on a real success response: {dict(resp.headers)}"


def test_query_success_path_with_limiter_enabled_returns_200_not_500(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf", chunks=None, elements=None)
    chunk_row = _real_chunk_row(admin, document_id)

    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id,
            document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9,
        )
    ]
    gen_result = GenerateResult(
        answer="Revenue grew 12% [1].", cited_indices=[1], hallucinated_markers=[],
        model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
    )
    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeGenerator(gen_result),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.SUPPORTED) for _ in pairs])})(),
    )
    try:
        resp = app_client.post(
            "/query",
            json={"question": "What was revenue growth?", "document_ids": [document_id]},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        _clear_overrides()

    assert resp.status_code == 200, f"real success path must return 200, not 500: {resp.status_code} {resp.text}"
    assert resp.json()["answer"] == "Revenue grew 12% [1]."
    assert any(h.lower() == "x-ratelimit-limit" for h in resp.headers), f"missing X-RateLimit-* headers on a real success response: {dict(resp.headers)}"


def test_query_stream_success_path_with_limiter_enabled_streams_correctly(app_client, admin, user_a):
    user_id, token = user_a
    document_id = ingest_real_document(app_client, user_id, token, filename="doc.pdf", chunks=None, elements=None)
    chunk_row = _real_chunk_row(admin, document_id)

    retrieved = [
        RetrievedChunk(
            chunk_id=chunk_row["id"], content=chunk_row["content"], page=1, document_id=document_id,
            document_name="doc.pdf", document_mime_type="application/pdf", element_type="text", score=0.9,
        )
    ]

    class FakeStreamingGenerator:
        async def generate_stream(self, question, chunks, history=None):
            yield "Revenue grew 12% [1]."
            yield GenerateStreamResult(
                answer="Revenue grew 12% [1].", cited_indices=[1], hallucinated_markers=[],
                model="gemini-3.6-flash", input_tokens=100, output_tokens=20, latency_ms=500.0,
            )

    _override(
        retriever=FakeRetriever(retrieved),
        generator=FakeStreamingGenerator(),
        verifier=type("V", (), {"verify_batch": staticmethod(lambda pairs: [_verdict(VerdictLabel.SUPPORTED) for _ in pairs])})(),
    )
    try:
        with app_client.stream(
            "POST",
            "/query/stream",
            json={"question": "What was revenue growth?", "document_ids": [document_id]},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            assert resp.status_code == 200, f"real success path must return 200, not 500: {resp.status_code}"
            assert "text/event-stream" in resp.headers.get("content-type", "")
            body = resp.read()
    finally:
        _clear_overrides()

    assert b"done" in body, f"expected a real done event in the stream, got: {body!r}"
