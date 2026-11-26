import contextlib
import sqlite3

import pytest

from agent import read_model
from agent.persistence import invariants
from agent.persistence import postgres as postgres_repository
from scripts import backup, migrate


class FakeCursor:
    def __init__(self, versions):
        self.versions = versions

    def fetchone(self):
        return None

    def fetchall(self):
        return self.versions


class FakeConnection:
    def __init__(self, versions=None):
        self.statements = []
        self.cursor = FakeCursor(versions or [])

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self.cursor

    def executemany(self, sql, rows):
        self.statements.append((sql, rows))
        return self.cursor


class VerificationCursor:
    def __init__(self, missing_index=None):
        self.missing_index = missing_index
        self.sql = ""
        self.params = None

    def _columns_for(self, table):
        columns = set(invariants.REQUIRED_COLUMNS.get(table, ()))
        if table in invariants.TENANT_SCOPED_TABLES:
            columns.add("tenant_id")
        return [{"column_name": column} for column in sorted(columns)]

    def fetchall(self):
        sql = self.sql.lower()
        if "information_schema.tables" in sql:
            return [
                {"table_name": table}
                for table in invariants.REQUIRED_TABLES
            ]
        if "information_schema.columns" in sql:
            return self._columns_for(self.params[0])
        if "table_constraints" in sql:
            return [
                {"column_name": column}
                for column in invariants.REQUIRED_PRIMARY_KEYS[self.params[0]]
            ]
        if "pg_class i" in sql:
            name = self.params[0]
            if name == self.missing_index:
                return []
            spec = invariants.REQUIRED_INDEXES[name]
            return [
                {
                    "is_unique": spec["unique"],
                    "is_partial": spec["partial"],
                    "columns": list(spec["columns"]),
                }
            ]
        return []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class VerificationConnection:
    def __init__(self, missing_index=None):
        self.cursor = VerificationCursor(missing_index)

    def execute(self, sql, params=None):
        self.cursor.sql = sql
        self.cursor.params = params
        return self.cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def test_run_migrations_is_idempotent(tmp_path, monkeypatch):
    database = tmp_path / "nested" / "cvi.sqlite3"
    monkeypatch.setenv("CVI_READ_MODEL_DB", str(database))
    monkeypatch.delenv("CVI_PERSISTENCE_BACKEND", raising=False)

    migrate.run_migrations(database)
    migrate.run_migrations(database)

    connection = sqlite3.connect(database)
    versions = [
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ]
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    connection.close()

    assert versions == [
        "001_baseline",
        "002_tenant_audit_fields",
        "003_authoritative_memory_contract",
        "004_durable_langgraph_checkpoints",
        "005_durable_worker_queue",
        "006_session_leases",
        "007_worker_fencing",
    ]
    assert "sessions" in tables
    assert "conversation_timings" in tables
    assert "research_memory_records" in tables
    assert "langgraph_checkpoints" in tables
    assert "langgraph_checkpoint_blobs" in tables
    assert "langgraph_checkpoint_writes" in tables
    assert "schema_migrations" in tables
    assert journal_mode == "wal"


def test_sqlite_migration_verification_checks_security_invariants(tmp_path, monkeypatch):
    database = tmp_path / "verify.sqlite3"
    monkeypatch.setenv("CVI_READ_MODEL_DB", str(database))
    monkeypatch.delenv("CVI_PERSISTENCE_BACKEND", raising=False)

    migrate.run_migrations(database)

    assert read_model.verify_migration_invariants(database) == []

    connection = sqlite3.connect(database)
    connection.execute("DROP INDEX idx_background_jobs_active_unique")
    connection.commit()
    connection.close()

    issues = read_model.verify_migration_invariants(database)
    assert any("idx_background_jobs_active_unique" in issue for issue in issues)


def test_sqlite_configure_upgrades_legacy_worker_and_checkpoint_schema(
    tmp_path, monkeypatch
):
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE langgraph_checkpoint_writes (
            tenant_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            task_path TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL DEFAULT '',
            idx INTEGER NOT NULL,
            value_type TEXT NOT NULL,
            value_blob BLOB NOT NULL,
            PRIMARY KEY (
                tenant_id,
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                task_id,
                task_path,
                idx
            )
        );
        CREATE INDEX idx_langgraph_checkpoint_writes_lookup
            ON langgraph_checkpoint_writes(
                tenant_id, thread_id, checkpoint_ns, checkpoint_id
            );
        CREATE TABLE background_jobs (
            job_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            run_after TEXT NOT NULL,
            worker_id TEXT,
            fencing_token TEXT,
            heartbeat_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        );
        CREATE UNIQUE INDEX idx_background_jobs_active_unique
            ON background_jobs(tenant_id, session_id, job_type)
            WHERE status IN ('queued', 'processing');
        """
    )
    connection.execute(
        """
        INSERT INTO background_jobs (
            job_id, tenant_id, session_id, job_type, status,
            payload_json, run_after, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-job",
            "default",
            "legacy-session",
            "process_response",
            "completed",
            "{}",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO langgraph_checkpoint_writes (
            tenant_id, thread_id, checkpoint_id, task_id,
            idx, value_type, value_blob
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "default",
            "legacy-session",
            "checkpoint-1",
            "task-1",
            0,
            "json",
            b"{}",
        ),
    )
    connection.commit()
    connection.close()

    monkeypatch.setenv("CVI_READ_MODEL_DB", str(database))
    monkeypatch.delenv("CVI_PERSISTENCE_BACKEND", raising=False)

    read_model.configure(database)

    assert read_model.verify_migration_invariants(database) == []

    connection = sqlite3.connect(database)
    columns = connection.execute("PRAGMA table_info(background_jobs)").fetchall()
    assert "dedupe_key" in {row[1] for row in columns}
    checkpoint_columns = connection.execute(
        "PRAGMA table_info(langgraph_checkpoint_writes)"
    ).fetchall()
    assert [row[1] for row in checkpoint_columns if row[5]] == [
        "tenant_id",
        "thread_id",
        "checkpoint_ns",
        "checkpoint_id",
        "task_id",
        "idx",
    ]
    assert (
        connection.execute("SELECT session_id FROM background_jobs").fetchone()[0]
        == "legacy-session"
    )
    assert (
        connection.execute(
            "SELECT task_id FROM langgraph_checkpoint_writes"
        ).fetchone()[0]
        == "task-1"
    )
    connection.close()


def test_postgres_migration_verification_checks_security_invariants(monkeypatch):
    url = "postgresql://cvi:s3cret@db:5432/cvi_research"
    monkeypatch.setattr(postgres_repository, "_database_url", None)
    connection = VerificationConnection()
    monkeypatch.setattr(
        postgres_repository,
        "_connect",
        lambda: connection,
    )

    assert postgres_repository.verify_migration_invariants(url) == []

    connection = VerificationConnection("idx_background_jobs_active_unique")
    monkeypatch.setattr(postgres_repository, "_connect", lambda: connection)

    issues = postgres_repository.verify_migration_invariants(url)
    assert any("idx_background_jobs_active_unique" in issue for issue in issues)


def test_run_migrations_fails_closed_on_invariant_errors(tmp_path, monkeypatch):
    database = tmp_path / "verify.sqlite3"
    monkeypatch.setenv("CVI_READ_MODEL_DB", str(database))
    monkeypatch.delenv("CVI_PERSISTENCE_BACKEND", raising=False)
    monkeypatch.setattr(
        read_model,
        "verify_migration_invariants",
        lambda path=None: ["broken invariant"],
    )

    with pytest.raises(migrate.MigrationInvariantError, match="broken invariant"):
        migrate.run_migrations(database)


def test_run_migrations_uses_postgres_adapter_when_configured(tmp_path, monkeypatch):
    url = "postgresql://cvi:s3cret@db:5432/cvi_research"
    monkeypatch.setenv("CVI_PERSISTENCE_BACKEND", "postgresql")
    monkeypatch.setenv("CVI_DATABASE_URL", url)
    monkeypatch.setattr(postgres_repository, "_database_url", None)

    fake = FakeConnection(
        [{"version": version} for version in postgres_repository.SCHEMA_VERSIONS]
    )
    monkeypatch.setattr(
        postgres_repository,
        "_connect",
        lambda: contextlib.nullcontext(fake),
    )
    verified = []
    monkeypatch.setattr(
        postgres_repository,
        "verify_migration_invariants",
        lambda url=None: verified.append(url) or [],
    )

    target = migrate.run_migrations()

    assert target == url
    assert verified == [url]
    statements = "\n".join(sql for sql, _ in fake.statements)
    assert "CREATE TABLE IF NOT EXISTS sessions" in statements
    assert "SELECT version FROM schema_migrations" in statements


def test_postgres_migrations_fail_closed_on_invariant_errors(monkeypatch):
    url = "postgresql://cvi:s3cret@db:5432/cvi_research"
    monkeypatch.setenv("CVI_PERSISTENCE_BACKEND", "postgresql")
    monkeypatch.setenv("CVI_DATABASE_URL", url)
    monkeypatch.setattr(postgres_repository, "_database_url", None)
    monkeypatch.setattr(
        postgres_repository,
        "_connect",
        lambda: contextlib.nullcontext(FakeConnection()),
    )
    monkeypatch.setattr(
        postgres_repository,
        "verify_migration_invariants",
        lambda url=None: ["idx_background_jobs_active_unique is missing"],
    )

    with pytest.raises(
        migrate.MigrationInvariantError,
        match="idx_background_jobs_active_unique",
    ):
        migrate.run_migrations()


def test_create_backup_writes_copy_and_prunes_old_backups(tmp_path):
    source = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE marker (value TEXT)")
    connection.execute("INSERT INTO marker VALUES ('ok')")
    connection.commit()
    connection.close()

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_backup = backup_dir / "cvi-20200101T000000Z.sqlite3"
    old_backup.write_bytes(b"old")

    result = backup.create_backup(source, backup_dir, retention=1)

    assert result.exists()
    assert result.parent == backup_dir
    assert not old_backup.exists()

    check = sqlite3.connect(result)
    value = check.execute("SELECT value FROM marker").fetchone()[0]
    check.close()
    assert value == "ok"


def test_create_postgres_backup_uses_pg_dump_without_password_in_args(
    tmp_path,
    monkeypatch,
):
    captured = {}

    def fake_run(command, check, env):
        captured["command"] = command
        captured["env"] = env
        destination = command[command.index("--file") + 1]
        with open(destination, "wb") as handle:
            handle.write(b"dump")
        return None

    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_backup = backup_dir / "cvi-20200101T000000Z.dump"
    old_backup.write_bytes(b"old")

    result = backup.create_postgres_backup(
        "postgresql://cvi:s3cret@db:5432/cvi_research",
        backup_dir,
        retention=1,
    )

    assert result.exists()
    assert not old_backup.exists()
    assert result.suffix == ".dump"
    command = " ".join(captured["command"])
    assert "pg_dump" in command
    assert "s3cret" not in command
    assert captured["env"]["PGPASSWORD"] == "s3cret"
