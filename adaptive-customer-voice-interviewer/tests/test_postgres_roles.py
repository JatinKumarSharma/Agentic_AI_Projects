"""Least-privilege PostgreSQL role bootstrap for hosted deployments."""

from __future__ import annotations

import pytest

from scripts import bootstrap_postgres_roles as bootstrap


class FakeCursor:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.statements: list[str] = []
        self.cursor = FakeCursor()

    def execute(self, sql, params=None):
        self.statements.append(str(sql))
        return self.cursor

    def commit(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def install_fake_driver(monkeypatch, connection: FakeConnection):
    class FakePsycopg:
        @staticmethod
        def connect(dsn):
            assert dsn == "postgresql://cvi:admin-secret@db:5432/cvi_research"
            return connection

    monkeypatch.setattr(bootstrap, "_driver", lambda: (FakePsycopg, None))


def test_bootstrap_creates_distinct_runtime_and_backup_roles(monkeypatch):
    connection = FakeConnection()
    install_fake_driver(monkeypatch, connection)
    monkeypatch.setenv("CVI_RUNTIME_DB_USER", "cvi_runtime")
    monkeypatch.setenv("CVI_RUNTIME_DB_PASSWORD", "runtime-secret")
    monkeypatch.setenv("CVI_BACKUP_DB_USER", "cvi_backup")
    monkeypatch.setenv("CVI_BACKUP_DB_PASSWORD", "backup-secret")

    bootstrap.bootstrap_from_environment(
        "postgresql://cvi:admin-secret@db:5432/cvi_research"
    )

    statements = "\n".join(connection.statements)
    assert "CREATE ROLE cvi_runtime LOGIN PASSWORD 'runtime-secret'" in statements
    assert "CREATE ROLE cvi_backup LOGIN PASSWORD 'backup-secret'" in statements
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        "TO cvi_runtime"
    ) in statements
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO cvi_backup" in statements
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE cvi IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cvi_runtime"
    ) in statements
    assert (
        "ALTER DEFAULT PRIVILEGES FOR ROLE cvi IN SCHEMA public "
        "GRANT SELECT ON TABLES TO cvi_backup"
    ) in statements
    assert "REVOKE CREATE ON SCHEMA public FROM cvi_runtime, cvi_backup" in statements


def test_bootstrap_requires_distinct_role_passwords(monkeypatch):
    monkeypatch.delenv("CVI_RUNTIME_DB_PASSWORD", raising=False)
    monkeypatch.delenv("CVI_BACKUP_DB_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="CVI_RUNTIME_DB_PASSWORD"):
        bootstrap.bootstrap_from_environment(
            "postgresql://cvi:admin-secret@db:5432/cvi_research"
        )
