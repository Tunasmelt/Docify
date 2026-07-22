import os

import psycopg
import pytest

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:54322/postgres"
)

TABLES = ["documents", "chunks", "conversations", "messages", "citations"]


@pytest.fixture(scope="module")
def conn():
    try:
        connection = psycopg.connect(DATABASE_URL, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip(f"no Postgres reachable at {DATABASE_URL} — run `supabase start` first")
    yield connection
    connection.close()


def test_tables_exist(conn):
    with conn.cursor() as cur:
        cur.execute(
            "select tablename from pg_tables where schemaname = 'public' and tablename = any(%s)",
            (TABLES,),
        )
        found = {row[0] for row in cur.fetchall()}
    assert found == set(TABLES)


@pytest.mark.parametrize("table", TABLES)
def test_rls_enabled(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class where relname = %s and relnamespace = 'public'::regnamespace",
            (table,),
        )
        row = cur.fetchone()
    assert row is not None, f"{table} not found"
    assert row[0] is True, f"RLS not enabled on {table}"


@pytest.mark.parametrize("table", TABLES)
def test_select_policy_present(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            "select policyname from pg_policies where schemaname = 'public' and tablename = %s",
            (table,),
        )
        policies = {row[0] for row in cur.fetchall()}
    assert f"{table}_select" in policies


def test_pgvector_extension_enabled(conn):
    with conn.cursor() as cur:
        cur.execute("select extname from pg_extension where extname = 'vector'")
        row = cur.fetchone()
    assert row is not None


def test_chunks_embedding_hnsw_index(conn):
    with conn.cursor() as cur:
        cur.execute(
            "select indexdef from pg_indexes where tablename = 'chunks' and indexname = 'chunks_embedding_idx'"
        )
        row = cur.fetchone()
    assert row is not None
    assert "hnsw" in row[0].lower()


def test_storage_buckets_created(conn):
    with conn.cursor() as cur:
        cur.execute("select id from storage.buckets where id = any(%s)", (["uploads", "figures"],))
        found = {row[0] for row in cur.fetchall()}
    assert found == {"uploads", "figures"}
