"""SQLite-backed authoritative research memory repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import sqlite as _sqlite


class SQLiteResearchMemoryRepository:
    """Default local/dev repository implementation."""

    def configure(self, database_path: Any = None) -> Path:
        return _sqlite.configure(database_path)

    def database_path(self) -> Path:
        return _sqlite.database_path()

    def apply_migrations(self) -> list[str]:
        return _sqlite.apply_migrations()

    def ensure_tenant_audit_schema(self, connection: Any) -> None:
        return _sqlite.ensure_tenant_audit_schema(connection)

    def __getattr__(self, name: str):
        return getattr(_sqlite, name)


_repository: SQLiteResearchMemoryRepository | None = None


def get_repository() -> SQLiteResearchMemoryRepository:
    global _repository
    if _repository is None:
        _repository = SQLiteResearchMemoryRepository()
    return _repository


def configure(database_path: Any = None) -> Path:
    return get_repository().configure(database_path)
