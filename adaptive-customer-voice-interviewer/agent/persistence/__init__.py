"""Persistence boundary for CVI authoritative research memory.

SQLite is the offline/test implementation. PostgreSQL is the hosted production
implementation. Both satisfy the same repository contract.
"""

from .repository import ResearchMemoryRepository
from .sqlite import SQLiteResearchMemoryRepository, configure
from .postgres import PostgresResearchMemoryRepository
from .config import (
    POSTGRES_BACKEND,
    SQLITE_BACKEND,
    PersistenceConfig,
    PersistenceConfigError,
    configure_repository,
    get_repository,
    load_persistence_config,
)

__all__ = [
    "ResearchMemoryRepository",
    "SQLiteResearchMemoryRepository",
    "PostgresResearchMemoryRepository",
    "POSTGRES_BACKEND",
    "SQLITE_BACKEND",
    "PersistenceConfig",
    "PersistenceConfigError",
    "configure_repository",
    "get_repository",
    "load_persistence_config",
    "configure",
]
