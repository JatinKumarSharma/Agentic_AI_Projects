"""Configuration boundary for authoritative research memory storage.

SQLite is the default offline/test backend. PostgreSQL is the production
backend. Both implement the same repository contract, so callers and interview
behavior do not change when the backend is selected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

from . import postgres as _postgres
from . import sqlite as _sqlite
from .repository import ResearchMemoryRepository

SQLITE_BACKEND = "sqlite"
POSTGRES_BACKEND = "postgresql"

_BACKEND_ALIASES = {
    "sqlite": SQLITE_BACKEND,
    "sqlite3": SQLITE_BACKEND,
    "postgres": POSTGRES_BACKEND,
    "postgresql": POSTGRES_BACKEND,
    "postgresql+psycopg": POSTGRES_BACKEND,
    "postgresql+psycopg2": POSTGRES_BACKEND,
}

_POSTGRES_SCHEMES = {
    "postgres",
    "postgresql",
    "postgresql+psycopg",
    "postgresql+psycopg2",
}


class PersistenceConfigError(ValueError):
    """Raised when persistence environment configuration is invalid."""


@dataclass(frozen=True)
class PersistenceConfig:
    backend: str
    database_path: Path | None = None
    database_url: str | None = None

    def describe(self) -> dict[str, str]:
        """Return a log/readiness-safe description without secrets."""

        if self.backend == SQLITE_BACKEND:
            return {
                "backend": self.backend,
                "database": str(self.database_path),
            }

        return {
            "backend": self.backend,
            "database": _mask_database_url(self.database_url),
        }


def load_persistence_config(env: Mapping[str, str] | None = None) -> PersistenceConfig:
    """Resolve the configured persistence backend from environment variables."""

    values: Mapping[str, str] = env if env is not None else os.environ

    raw_backend = str(values.get("CVI_PERSISTENCE_BACKEND", SQLITE_BACKEND)).strip().lower()
    backend = _BACKEND_ALIASES.get(raw_backend)
    if backend is None:
        supported = ", ".join(sorted(set(_BACKEND_ALIASES.values())))
        raise PersistenceConfigError(
            f"Unsupported persistence backend {raw_backend!r}. "
            f"Supported backends: {supported}."
        )

    if backend == SQLITE_BACKEND:
        configured_path = values.get("CVI_READ_MODEL_DB")
        database_path = Path(configured_path) if configured_path else Path(_sqlite.DEFAULT_DATABASE_PATH)
        return PersistenceConfig(backend=backend, database_path=database_path)

    database_url = (
        values.get("CVI_DATABASE_URL")
        or values.get("DATABASE_URL")
        or ""
    ).strip()
    if not database_url:
        raise PersistenceConfigError(
            "CVI_DATABASE_URL is required when CVI_PERSISTENCE_BACKEND=postgresql."
        )

    parsed = urlparse(database_url)
    if parsed.scheme not in _POSTGRES_SCHEMES or not parsed.hostname:
        raise PersistenceConfigError(
            "CVI_DATABASE_URL must be a PostgreSQL URL such as "
            "postgresql://user:password@host:5432/database."
        )

    return PersistenceConfig(backend=backend, database_url=database_url)


def get_repository(config: PersistenceConfig | None = None) -> ResearchMemoryRepository:
    """Return the repository for the configured backend."""

    resolved = config or load_persistence_config()

    if resolved.backend == SQLITE_BACKEND:
        return _sqlite.get_repository()

    return _postgres.get_repository()


def configure_repository(
    config: PersistenceConfig | None = None,
    *,
    ensure_schema: bool = True,
) -> ResearchMemoryRepository:
    """Configure and return the repository for the configured backend.

    Runtime services pass ``ensure_schema=False`` so the DML-only credential
    never attempts DDL. Migration and offline-test paths keep the default.
    """

    resolved = config or load_persistence_config()
    repository = get_repository(resolved)

    if resolved.backend == SQLITE_BACKEND:
        repository.configure(resolved.database_path, ensure_schema=ensure_schema)
    else:
        repository.configure(resolved.database_url, ensure_schema=ensure_schema)

    return repository


def _mask_database_url(database_url: str | None) -> str:
    if not database_url:
        return ""

    parsed = urlparse(database_url)
    if not parsed.password:
        return database_url

    user = parsed.username or ""
    host = parsed.hostname or ""
    netloc = f"{user}:***@{host}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


__all__ = [
    "SQLITE_BACKEND",
    "POSTGRES_BACKEND",
    "PersistenceConfig",
    "PersistenceConfigError",
    "load_persistence_config",
    "get_repository",
    "configure_repository",
]
