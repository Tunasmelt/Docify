# Shared helpers for tests that exercise a real local Supabase stack
# (`supabase start`), per STANDARDS.md: "No mocking of Supabase in
# integration tests — use `supabase start` for a local instance."
#
# These are the Supabase CLI's fixed local-dev demo keys — provisioned by
# every `supabase start`, not secret (sourced here from the running local
# containers' own kong.yml routing config, not guessed). They must never
# be the keys used against the live project (those live in `.env` and are
# real secrets) — conftest.py overrides SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY
# to these local values before `main.app` is ever imported, so the whole
# test suite talks to the local stack, never the live one.

import uuid

import httpx
from supabase import Client, create_client

LOCAL_SUPABASE_URL = "http://127.0.0.1:54321"
LOCAL_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9."
    "CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0"
)
LOCAL_SUPABASE_SERVICE_ROLE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0."
    "EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU"
)
LOCAL_POSTGRES_DSN = "postgresql://postgres:postgres@localhost:54322/postgres"


def admin_client() -> Client:
    """Service-role client — bypasses RLS, same client the app itself
    uses (db.client.get_service_role_client points at the same local
    values once conftest.py's env override is in effect)."""
    return create_client(LOCAL_SUPABASE_URL, LOCAL_SUPABASE_SERVICE_ROLE_KEY)


def create_test_user(client: Client, *, password: str = "test-password-123") -> tuple[str, str]:
    """Creates a real, auto-confirmed local auth user. Returns (user_id, email)."""
    email = f"test-{uuid.uuid4().hex[:12]}@example.com"
    result = client.auth.admin.create_user({"email": email, "password": password, "email_confirm": True})
    return result.user.id, email


def delete_test_user(client: Client, user_id: str) -> None:
    """Cascades to that user's documents/chunks via the schema's `on
    delete cascade` FKs — does not touch storage objects, which have no
    such FK; callers that upload files must clean those up separately."""
    client.auth.admin.delete_user(user_id)


def login(email: str, password: str = "test-password-123") -> str:
    """Real password-grant login against local GoTrue — returns a
    genuinely issued ES256 access token, not a self-forged one (see
    .agent/MEMORY.md's anti-pattern entry on circular JWT verification)."""
    response = httpx.post(
        f"{LOCAL_SUPABASE_URL}/auth/v1/token?grant_type=password",
        json={"email": email, "password": password},
        headers={"apikey": LOCAL_SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def upload_via_rest(access_token: str, bucket: str, path: str, content: bytes, content_type: str) -> httpx.Response:
    """Uploads as the authenticated user over the real Storage REST API —
    exercises the same storage.objects RLS policies (and HTTP path) a
    real browser client would hit, not a service-role bypass."""
    return httpx.put(
        f"{LOCAL_SUPABASE_URL}/storage/v1/object/{bucket}/{path}",
        content=content,
        headers={
            "apikey": LOCAL_SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": content_type,
        },
        timeout=30,
    )


def rest_select(access_token: str, table: str, query: str = "") -> httpx.Response:
    """RLS-scoped PostgREST select as the authenticated user — used to
    prove multi-tenant isolation at the same layer the app itself reads
    through, independent of any /documents endpoint (FEAT-008's job, not
    built yet)."""
    url = f"{LOCAL_SUPABASE_URL}/rest/v1/{table}"
    if query:
        url = f"{url}?{query}"
    return httpx.get(
        url,
        headers={"apikey": LOCAL_SUPABASE_ANON_KEY, "Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
