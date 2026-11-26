"""Compatibility facade for authoritative research memory.

Existing callers import `agent.read_model`. The implementation is selected from
the configured persistence backend: SQLite for offline/testing and PostgreSQL
for hosted production. Interview behavior and call sites stay unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.persistence import config as _persistence_config
from agent.persistence import postgres as _postgres
from agent.persistence import sqlite as _sqlite

# Re-exported constants for compatibility. Both adapters share the same
# schema-version contract.
PROJECT_ROOT = _sqlite.PROJECT_ROOT
DEFAULT_DATABASE_PATH = _sqlite.DEFAULT_DATABASE_PATH
BASELINE_SCHEMA_VERSION = _sqlite.BASELINE_SCHEMA_VERSION
TENANT_AUDIT_SCHEMA_VERSION = _sqlite.TENANT_AUDIT_SCHEMA_VERSION
AUTHORITATIVE_MEMORY_SCHEMA_VERSION = _sqlite.AUTHORITATIVE_MEMORY_SCHEMA_VERSION
CHECKPOINT_SCHEMA_VERSION = _sqlite.CHECKPOINT_SCHEMA_VERSION
WORKER_QUEUE_SCHEMA_VERSION = _sqlite.WORKER_QUEUE_SCHEMA_VERSION
SESSION_LEASE_SCHEMA_VERSION = _sqlite.SESSION_LEASE_SCHEMA_VERSION
SCHEMA_VERSIONS = _sqlite.SCHEMA_VERSIONS
RESEARCHER_SINGLE_KNOWLEDGE_FIELDS = _sqlite.RESEARCHER_SINGLE_KNOWLEDGE_FIELDS
RESEARCHER_DICTIONARY_KNOWLEDGE_FIELDS = _sqlite.RESEARCHER_DICTIONARY_KNOWLEDGE_FIELDS


def _backend_impl():
    """Resolve the implementation from environment configuration."""

    try:
        config = _persistence_config.load_persistence_config()
    except _persistence_config.PersistenceConfigError:
        return _sqlite

    if config.backend == _persistence_config.POSTGRES_BACKEND:
        return _postgres

    return _sqlite


_impl = _backend_impl()


def configure(database_path: Any = None):
    """Configure the selected backend.

    Tests pass a SQLite path explicitly. Production uses the environment
    configuration resolved at import time.
    """

    global _impl

    if database_path is not None and _impl is _postgres:
        return _impl.configure(database_path)

    if database_path is not None and _impl is not _sqlite:
        _impl = _sqlite

    return _impl.configure(database_path)


def database_path():
    return _impl.database_path()


def health_check() -> tuple[str, str | None]:
    return _impl.health_check()


def list_research_memory_records(
    tenant_id: str | None = None,
    session_id: str | None = None,
    record_type: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    return _impl.list_research_memory_records(
        tenant_id,
        session_id,
        record_type,
        limit,
    )


def apply_migrations(database_path: Any = None) -> list[str]:
    if database_path is not None and _impl is _postgres:
        return _impl.apply_migrations(database_path)

    return _impl.apply_migrations(database_path)


def verify_migration_invariants(database_path: Any = None) -> list[str]:
    return _impl.verify_migration_invariants(database_path)


def ensure_tenant_audit_schema(connection: Any) -> None:
    return _impl.ensure_tenant_audit_schema(connection)


def __getattr__(name: str):
    try:
        return getattr(_impl, name)
    except AttributeError:
        # Pure state-shaping helpers live in the SQLite adapter even when the
        # configured backend is PostgreSQL. They do not touch a database and
        # are part of the shared repository contract, so keep them available
        # through this compatibility facade.
        return getattr(_sqlite, name)
