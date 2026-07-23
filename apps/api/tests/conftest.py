# Must run (and set env vars) before any test module can `from main
# import app` — JWTAuthMiddleware resolves SUPABASE_URL from os.environ
# lazily, on the first request through TestClient, so this needs to win
# before that happens, not before app startup specifically.
# main.py's load_dotenv() defaults to override=False, so these values
# win regardless of import order (see FEAT-007 CHANGELOG entry for why
# this override exists at all: apps/api/.env points at the LIVE Supabase
# project, but STANDARDS.md requires integration tests to run against a
# local `supabase start` instance instead).
import os

from tests._local_supabase import LOCAL_SUPABASE_SERVICE_ROLE_KEY, LOCAL_SUPABASE_URL

os.environ["SUPABASE_URL"] = LOCAL_SUPABASE_URL
os.environ["SUPABASE_SERVICE_ROLE_KEY"] = LOCAL_SUPABASE_SERVICE_ROLE_KEY

import psycopg
import pytest

from tests._local_supabase import LOCAL_POSTGRES_DSN, admin_client


def _local_supabase_reachable() -> bool:
    try:
        conn = psycopg.connect(LOCAL_POSTGRES_DSN, connect_timeout=3)
        conn.close()
        return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def require_local_supabase():
    if not _local_supabase_reachable():
        pytest.skip(f"no local Supabase reachable at {LOCAL_POSTGRES_DSN} — run `supabase start` first")


@pytest.fixture
def admin(require_local_supabase):
    return admin_client()
