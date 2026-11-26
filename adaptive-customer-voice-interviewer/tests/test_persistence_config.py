import contextlib
from pathlib import Path

import pytest

from agent.persistence import config as persistence_config
from agent.persistence import get_repository
from agent.persistence import postgres as postgres_repository


class FakeCursor:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.cursor = FakeCursor()

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self.cursor

    def executemany(self, sql, rows):
        self.statements.append((sql, rows))
        return self.cursor


def test_default_sqlite_config_uses_read_model_db(tmp_path, monkeypatch):
    database = tmp_path / "cvi.sqlite3"
    monkeypatch.setenv("CVI_READ_MODEL_DB", str(database))
    monkeypatch.delenv("CVI_PERSISTENCE_BACKEND", raising=False)

    config = persistence_config.load_persistence_config()

    assert config.backend == "sqlite"
    assert config.database_path == database
    assert config.database_url is None
    assert config.describe() == {
        "backend": "sqlite",
        "database": str(database),
    }


def test_postgres_backend_requires_database_url(monkeypatch):
    monkeypatch.setenv("CVI_PERSISTENCE_BACKEND", "postgres")
    monkeypatch.delenv("CVI_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(persistence_config.PersistenceConfigError, match="CVI_DATABASE_URL"):
        persistence_config.load_persistence_config()


def test_postgres_config_masks_password_in_description(monkeypatch):
    monkeypatch.setenv("CVI_PERSISTENCE_BACKEND", "postgresql")
    monkeypatch.setenv(
        "CVI_DATABASE_URL",
        "postgresql://cvi:s3cret@db:5432/cvi_research",
    )

    config = persistence_config.load_persistence_config()

    assert config.backend == "postgresql"
    assert config.database_path is None
    assert config.database_url == "postgresql://cvi:s3cret@db:5432/cvi_research"
    described = config.describe()
    assert described["database"] == "postgresql://cvi:***@db:5432/cvi_research"
    assert "s3cret" not in str(described)


def test_unsupported_backend_fails_early(monkeypatch):
    monkeypatch.setenv("CVI_PERSISTENCE_BACKEND", "mysql")

    with pytest.raises(persistence_config.PersistenceConfigError, match="Unsupported persistence backend"):
        persistence_config.load_persistence_config()


def test_postgres_repository_factory_returns_postgres_adapter(monkeypatch):
    monkeypatch.setenv("CVI_PERSISTENCE_BACKEND", "postgresql")
    monkeypatch.setenv("CVI_DATABASE_URL", "postgresql://cvi:s3cret@db:5432/cvi_research")
    config = persistence_config.load_persistence_config()

    repository = get_repository(config)

    assert isinstance(repository, postgres_repository.PostgresResearchMemoryRepository)


def test_configure_repository_can_skip_schema_creation_for_runtime_roles(monkeypatch):
    monkeypatch.setenv("CVI_PERSISTENCE_BACKEND", "postgresql")
    monkeypatch.setenv(
        "CVI_DATABASE_URL",
        "postgresql://cvi_runtime:s3cret@db:5432/cvi_research",
    )
    monkeypatch.setattr(postgres_repository, "_database_url", None)
    fake = FakeConnection()
    monkeypatch.setattr(
        postgres_repository,
        "_connect",
        lambda: contextlib.nullcontext(fake),
    )

    config = persistence_config.load_persistence_config()
    repository = persistence_config.configure_repository(config, ensure_schema=False)

    assert isinstance(repository, postgres_repository.PostgresResearchMemoryRepository)
    assert fake.statements == []
    assert (
        postgres_repository.database_path()
        == "postgresql://cvi_runtime:s3cret@db:5432/cvi_research"
    )


def test_postgres_configure_creates_schema_without_database_path(monkeypatch):
    monkeypatch.setattr(postgres_repository, "_database_url", None)
    fake = FakeConnection()
    monkeypatch.setattr(
        postgres_repository,
        "_connect",
        lambda: contextlib.nullcontext(fake),
    )

    configured = postgres_repository.configure(
        "postgresql://cvi:s3cret@db:5432/cvi_research"
    )

    assert configured == "postgresql://cvi:s3cret@db:5432/cvi_research"
    assert postgres_repository.database_path() == configured
    statements = "\n".join(sql for sql, _ in fake.statements)
    assert "CREATE TABLE IF NOT EXISTS sessions" in statements
    assert "CREATE TABLE IF NOT EXISTS langgraph_checkpoints" in statements
    assert "CREATE TABLE IF NOT EXISTS research_memory_records" in statements
    assert "%s" in statements


def test_postgres_session_write_is_tenant_scoped_with_psycopg_placeholders(monkeypatch):
    monkeypatch.setattr(postgres_repository, "_database_url", "postgresql://cvi@db/cvi")
    fake = FakeConnection()
    monkeypatch.setattr(
        postgres_repository,
        "_connect",
        lambda: contextlib.nullcontext(fake),
    )

    postgres_repository.upsert_session(
        {"session_id": "session-1", "tenant_id": "tenant-a"}
    )

    sql, params = next(
        (sql, params)
        for sql, params in fake.statements
        if "INSERT INTO sessions" in sql
    )
    assert "%s" in sql
    assert "?" not in sql
    assert params[0] == "session-1"
    assert params[1] == "tenant-a"


def test_postgres_driver_dsn_normalizes_alias_scheme(monkeypatch):
    monkeypatch.setattr(
        postgres_repository,
        "_database_url",
        "postgresql+psycopg://cvi:s3cret@db:5432/cvi_research",
    )

    assert postgres_repository._driver_dsn() == (
        "postgresql://cvi:s3cret@db:5432/cvi_research"
    )


def test_sqlite_repository_health_check(tmp_path):
    repository = get_repository()
    repository.configure(tmp_path / "health.sqlite3")

    status, detail = repository.health_check()

    assert status == "ok"
    assert detail is None
