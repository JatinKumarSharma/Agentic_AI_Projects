"""Create least-privilege PostgreSQL roles for hosted CVI deployments.

The migration/owner credential stays DDL-capable. Runtime API/worker processes
receive DML-only privileges, and backups receive read-only privileges. This
script is idempotent and is intended to run as a one-shot Compose service before
migrations create the application tables.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse, urlunparse

from agent.persistence import postgres as _postgres

_ROLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _driver():
    """Resolve the PostgreSQL driver through the existing lazy boundary."""

    return _postgres._driver()


def _role_name(value: str, label: str) -> str:
    candidate = str(value or "").strip()
    if not _ROLE_PATTERN.fullmatch(candidate):
        raise RuntimeError(f"{label} must be a simple PostgreSQL role name.")
    return candidate


def _literal(value: str) -> str:
    candidate = str(value or "")
    if "\x00" in candidate:
        raise RuntimeError("PostgreSQL passwords cannot contain NUL bytes.")
    return "'" + candidate.replace("'", "''") + "'"


def _normalize_dsn(dsn: str | None = None) -> str:
    resolved = str(
        dsn
        or os.getenv("CVI_MIGRATION_DATABASE_URL")
        or os.getenv("CVI_DATABASE_URL")
        or ""
    ).strip()
    parsed = urlparse(resolved)
    scheme = parsed.scheme.split("+", 1)[0].lower()

    if not resolved or scheme not in _postgres.POSTGRES_SCHEMES:
        raise RuntimeError(
            "A PostgreSQL admin URL is required to bootstrap CVI database roles."
        )

    if scheme == "postgres":
        resolved = urlunparse(parsed._replace(scheme="postgresql"))

    return resolved


def _ensure_role(connection, username: str, password: str) -> None:
    password_literal = _literal(password)
    exists = connection.execute(
        f"SELECT 1 FROM pg_roles WHERE rolname = {_literal(username)}"
    ).fetchone()

    if exists:
        connection.execute(
            f"ALTER ROLE {username} WITH LOGIN PASSWORD {password_literal}"
        )
    else:
        connection.execute(
            f"CREATE ROLE {username} LOGIN PASSWORD {password_literal}"
        )


def bootstrap_roles(
    admin_dsn: str | None = None,
    *,
    runtime_user: str = "cvi_runtime",
    runtime_password: str = "",
    backup_user: str = "cvi_backup",
    backup_password: str = "",
    migration_user: str | None = None,
) -> None:
    """Create/update runtime and backup roles and grant least privilege."""

    resolved_dsn = _normalize_dsn(admin_dsn)
    runtime = _role_name(runtime_user, "CVI_RUNTIME_DB_USER")
    backup = _role_name(backup_user, "CVI_BACKUP_DB_USER")

    if not runtime_password:
        raise RuntimeError("CVI_RUNTIME_DB_PASSWORD is required.")
    if not backup_password:
        raise RuntimeError("CVI_BACKUP_DB_PASSWORD is required.")

    owner = _role_name(
        migration_user or urlparse(resolved_dsn).username or "",
        "migration database user",
    )

    psycopg, _ = _driver()
    with psycopg.connect(resolved_dsn) as connection:
        _ensure_role(connection, runtime, runtime_password)
        _ensure_role(connection, backup, backup_password)

        connection.execute(f"GRANT USAGE ON SCHEMA public TO {runtime}, {backup}")
        connection.execute(f"REVOKE CREATE ON SCHEMA public FROM {runtime}, {backup}")

        connection.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
            f"TO {runtime}"
        )
        connection.execute(
            "GRANT SELECT ON ALL TABLES IN SCHEMA public "
            f"TO {backup}"
        )
        connection.execute(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
            f"TO {runtime}, {backup}"
        )

        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE "
            f"{owner} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES "
            f"TO {runtime}"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE "
            f"{owner} IN SCHEMA public "
            "GRANT SELECT ON TABLES "
            f"TO {backup}"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE "
            f"{owner} IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES "
            f"TO {runtime}, {backup}"
        )


def bootstrap_from_environment(admin_dsn: str | None = None) -> None:
    """Read role names/passwords from the hosted deployment environment."""

    bootstrap_roles(
        admin_dsn,
        runtime_user=os.getenv("CVI_RUNTIME_DB_USER", "cvi_runtime"),
        runtime_password=os.getenv("CVI_RUNTIME_DB_PASSWORD", ""),
        backup_user=os.getenv("CVI_BACKUP_DB_USER", "cvi_backup"),
        backup_password=os.getenv("CVI_BACKUP_DB_PASSWORD", ""),
        migration_user=os.getenv("CVI_MIGRATION_DB_USER"),
    )


def main() -> None:
    bootstrap_from_environment()
    print("PostgreSQL runtime and backup roles are ready.")


if __name__ == "__main__":
    main()
