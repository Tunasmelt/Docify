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
from routes.ingest import INGEST_MINUTE_LIMIT
from routes.query import QUERY_MINUTE_LIMIT

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


# Acceptance criterion: rate limiting is keyed per-user, not shared/per-IP -- one user hitting their limit never blocks another
def test_ingest_rate_limit_is_per_user_not_shared(app_client, admin, user_a, user_b):
    user_id_a, token_a = user_a
    user_id_b, token_b = user_b
    per_minute = int(INGEST_MINUTE_LIMIT.split("/")[0])

    for _ in range(per_minute):
        resp = _ingest_request(app_client, token_a)
        assert resp.status_code == 403
    limited = _ingest_request(app_client, token_a)
    assert limited.status_code == 429

    # A completely different real user, same TestClient, same in-memory
    # limiter instance -- must NOT be affected by user_a's exhausted limit.
    resp_b = _ingest_request(app_client, token_b)
    assert resp_b.status_code == 403, f"user_b was incorrectly rate-limited by user_a's usage: {resp_b.status_code} {resp_b.text}"


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


# Acceptance criterion: documents/conversations routes are explicitly NOT rate-limited (SCOPE.md scopes this to /ingest + /query only)
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
