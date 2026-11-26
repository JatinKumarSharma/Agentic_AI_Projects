"""Apply CVI database migrations.

SQLite is used for offline testing. PostgreSQL is the hosted production
backend. Both are selected through the repository configuration boundary so
the migration service works without code changes in either environment.
"""

import os
import sqlite3
from pathlib import Path

from agent import read_model
from agent.persistence.config import (
    POSTGRES_BACKEND,
    configure_repository,
    load_persistence_config,
)
from agent.persistence.invariants import (
    MigrationInvariantError,
    format_issues,
)


def _resolve_database_path(database_path=None) -> Path:
    if database_path is not None:
        return Path(database_path)
    configured = os.getenv("CVI_READ_MODEL_DB")
    return Path(configured) if configured else Path(read_model.DEFAULT_DATABASE_PATH)


def _raise_for_issues(issues: list[str]) -> None:
    if issues:
        raise MigrationInvariantError(format_issues(issues))


def _run_postgres_migrations() -> str:
    config = load_persistence_config()
    repository = configure_repository(config)
    repository.apply_migrations()
    _raise_for_issues(repository.verify_migration_invariants(config.database_url))
    return config.database_url or ""


def run_migrations(database_path=None) -> str | Path:
    """Ensure schema exists and record applied schema versions."""

    config = load_persistence_config()
    if config.backend == POSTGRES_BACKEND:
        return _run_postgres_migrations()

    path = _resolve_database_path(database_path)
    versions = read_model.apply_migrations(path)

    # Ensure baseline is present for old DBs that only had tables.
    connection = sqlite3.connect(path)
    try:
        rows = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
        missing = [v for v in read_model.SCHEMA_VERSIONS if v not in rows]
        if missing:
            read_model.configure(path)
            versions = read_model.apply_migrations(path)
    finally:
        connection.close()

    _raise_for_issues(read_model.verify_migration_invariants(path))
    _ = versions
    return path


def main() -> None:
    target = run_migrations()
    print(f"Migrations up to date: {target}")


if __name__ == "__main__":
    main()
