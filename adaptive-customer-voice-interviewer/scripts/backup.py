"""Online backups for the configured CVI persistence backend.

SQLite uses ``sqlite3.Connection.backup()`` for offline/testing deployments.
PostgreSQL uses ``pg_dump`` in custom format for hosted production.
"""

import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

from agent import read_model
from agent.persistence.config import (
    POSTGRES_BACKEND,
    load_persistence_config,
)


BACKUP_PREFIX = "cvi-"


def resolve_source_path() -> Path:
    configured = os.getenv("CVI_READ_MODEL_DB")
    return Path(configured) if configured else Path(read_model.DEFAULT_DATABASE_PATH)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _prune(backup_dir: Path, pattern: str, retention: int) -> None:
    if retention <= 0:
        return

    backups = sorted(backup_dir.glob(pattern), reverse=True)
    for stale in backups[retention:]:
        stale.unlink()


def create_backup(
    source_path: Path,
    backup_dir: Path,
    retention: int = 7,
) -> Path:
    """Create a consistent SQLite backup with retention."""

    source_path = Path(source_path)
    backup_dir = Path(backup_dir)

    if not source_path.exists():
        raise FileNotFoundError(f"Database not found: {source_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{BACKUP_PREFIX}{_stamp()}.sqlite3"

    source = sqlite3.connect(source_path)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    _prune(backup_dir, f"{BACKUP_PREFIX}*.sqlite3", retention)
    return destination


def create_postgres_backup(
    database_url: str,
    backup_dir: Path,
    retention: int = 7,
) -> Path:
    """Create a consistent PostgreSQL custom-format dump with retention."""

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{BACKUP_PREFIX}{_stamp()}.dump"

    parsed = urlparse(database_url)
    if (
        parsed.scheme.split("+", 1)[0] not in {"postgres", "postgresql"}
        or not parsed.hostname
    ):
        raise ValueError("CVI_DATABASE_URL must be a PostgreSQL connection URL.")

    parsed = parsed._replace(scheme="postgresql")

    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)

    host_port = parsed.hostname
    if parsed.port:
        host_port = f"{host_port}:{parsed.port}"
    safe_netloc = f"{parsed.username or 'postgres'}@{host_port}"
    safe_url = urlunparse(parsed._replace(netloc=safe_netloc))

    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--file",
            str(destination),
            safe_url,
        ],
        check=True,
        env=env,
    )

    _prune(backup_dir, f"{BACKUP_PREFIX}*.dump", retention)
    return destination


def main() -> None:
    config = load_persistence_config()
    backup_dir = Path(os.getenv("CVI_BACKUP_DIR", "/backups"))
    retention = int(os.getenv("CVI_BACKUP_RETENTION", "7"))

    if config.backend == POSTGRES_BACKEND:
        result = create_postgres_backup(config.database_url or "", backup_dir, retention)
    else:
        source = resolve_source_path()
        if not source.exists():
            raise SystemExit(f"Database not found: {source}")
        result = create_backup(source, backup_dir, retention)

    print(f"Backup written: {result}")


if __name__ == "__main__":
    main()
