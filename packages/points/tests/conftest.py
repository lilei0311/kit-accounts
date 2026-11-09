"""Postgres fixtures. Never touch NAS / production; local disposable DB only."""

from __future__ import annotations

import os
import uuid

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres: needs ephemeral local PostgreSQL (skipped when unavailable)",
    )


def _admin_dsn() -> str:
    # Prefer peer auth as the local OS user (superuser in disposable CI/dev).
    return os.environ.get(
        "MAXKIT_POINTS_TEST_ADMIN_DSN",
        "postgresql:///postgres?host=/var/run/postgresql",
    )


def _postgres_ready() -> bool:
    if os.environ.get("MAXKIT_POINTS_SKIP_POSTGRES", "").strip() in {"1", "true", "yes"}:
        return False
    try:
        import psycopg
    except ImportError:
        return False
    try:
        with psycopg.connect(_admin_dsn(), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def postgres_enabled() -> bool:
    return _postgres_ready()


@pytest.fixture
def pg_store(postgres_enabled: bool):
    """Ephemeral database + migrated ``points`` schema; dropped after the test."""

    if not postgres_enabled:
        pytest.skip("local PostgreSQL unavailable or explicitly skipped")

    import psycopg

    from maxkit_points.postgres import PostgresPointsStore, _packaged_migrations_dir

    owner = os.environ.get("MAXKIT_POINTS_TEST_OWNER_ROLE", "kit_accounts_owner")
    runtime = os.environ.get("MAXKIT_POINTS_TEST_RUNTIME_ROLE", "kit_accounts_test")
    runtime_password = os.environ.get("MAXKIT_POINTS_TEST_RUNTIME_PASSWORD", "kit_accounts_test")
    db_name = f"kit_accounts_t_{uuid.uuid4().hex[:12]}"
    admin = _admin_dsn()

    with psycopg.connect(admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            admin_user = cur.fetchone()[0]
            cur.execute(f'CREATE DATABASE "{db_name}" OWNER "{admin_user}"')

    db_admin = f"postgresql:///{db_name}?host=/var/run/postgresql"
    sql = (_packaged_migrations_dir() / "001_init.sql").read_text(encoding="utf-8")
    sql = sql.replace("__RUNTIME_ROLE__", runtime)
    with psycopg.connect(db_admin, autocommit=False) as conn:
        with conn.cursor() as cur:
            # Mimic ops: schema owned by owner role; runtime has USAGE only.
            cur.execute(
                f"""
                DO $$ BEGIN
                  CREATE ROLE {owner} NOLOGIN;
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                """
            )
            cur.execute(
                f"""
                DO $$ BEGIN
                  CREATE ROLE {runtime} LOGIN PASSWORD '{runtime_password}';
                EXCEPTION WHEN duplicate_object THEN
                  ALTER ROLE {runtime} WITH LOGIN PASSWORD '{runtime_password}';
                END $$;
                """
            )
            cur.execute(f"CREATE SCHEMA points AUTHORIZATION {owner}")
            cur.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO {runtime}')
            cur.execute(f"GRANT USAGE ON SCHEMA points TO {runtime}")
            # DDL as schema owner (ops: points_owner).
            cur.execute(f"SET ROLE {owner}")
            cur.execute("SET search_path TO points")
            cur.execute(sql)
            cur.execute("RESET ROLE")
        conn.commit()

    dsn = (
        f"postgresql://{runtime}:{runtime_password}@127.0.0.1:5432/{db_name}"
        "?options=-csearch_path%3Dpoints"
    )
    store = PostgresPointsStore(dsn, schema="points")
    try:
        yield store
    finally:
        store.close()
        with psycopg.connect(admin, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = %s AND pid <> pg_backend_pid()
                    """,
                    (db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
