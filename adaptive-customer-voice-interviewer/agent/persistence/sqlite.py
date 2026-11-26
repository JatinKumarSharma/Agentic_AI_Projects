import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.jobs.errors import DuplicateJobError
from agent.response_outcomes import is_completed
from agent.session import normalize_date
from agent.persistence import invariants
from agent.tenancy import (
    audit_request_id,
    current_tenant_id,
    normalize_tenant_id,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = Path(
    os.getenv(
        "CVI_READ_MODEL_DB",
        PROJECT_ROOT / "data" / "research_read_model.sqlite3",
    )
)

BASELINE_SCHEMA_VERSION = "001_baseline"
TENANT_AUDIT_SCHEMA_VERSION = "002_tenant_audit_fields"
AUTHORITATIVE_MEMORY_SCHEMA_VERSION = "003_authoritative_memory_contract"
CHECKPOINT_SCHEMA_VERSION = "004_durable_langgraph_checkpoints"
WORKER_QUEUE_SCHEMA_VERSION = "005_durable_worker_queue"
SESSION_LEASE_SCHEMA_VERSION = "006_session_leases"
WORKER_FENCING_SCHEMA_VERSION = "007_worker_fencing"
SCHEMA_VERSIONS = (
    BASELINE_SCHEMA_VERSION,
    TENANT_AUDIT_SCHEMA_VERSION,
    AUTHORITATIVE_MEMORY_SCHEMA_VERSION,
    CHECKPOINT_SCHEMA_VERSION,
    WORKER_QUEUE_SCHEMA_VERSION,
    SESSION_LEASE_SCHEMA_VERSION,
    WORKER_FENCING_SCHEMA_VERSION,
)


_database_path = None


RESEARCHER_SINGLE_KNOWLEDGE_FIELDS = (
    "product_relationship",
    "product_awareness",
    "purchase_intent",
    "overall_sentiment",
    "product_perception",
)

RESEARCHER_DICTIONARY_KNOWLEDGE_FIELDS = (
    "feature_sentiment",
    "consumer_profile",
    "features",
    "needs",
    "pain_points",
    "use_cases",
    "aspirations",
    "expectations",
    "consumer_intent",
    "competitor_information",
    "budget_constraints",
    "buying_constraints",
    "adoption_barriers",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _lease_expiry(ttl_seconds: float) -> str:
    return (
        datetime.now(timezone.utc)
        + timedelta(seconds=max(0.0, ttl_seconds))
    ).isoformat(timespec="milliseconds")


def _ensure_columns(
    connection: sqlite3.Connection,
    table: str,
    definitions: dict[str, str],
) -> None:
    """Add missing columns to a table created by an older schema."""

    existing = {
        row[1]
        for row in connection.execute(
            f"PRAGMA table_info({table})"
        )
    }

    for name, definition in definitions.items():
        if name not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
            )


TENANT_SCOPED_TABLES = (
    "sessions",
    "session_snapshots",
    "session_events",
    "analysis_reports",
    "report_claims",
    "claim_evidence",
    "response_evidence",
    "response_outcomes",
    "conversation_timings",
)


def _migrate_legacy_schema_constraints(connection: sqlite3.Connection) -> None:
    """Bring old SQLite databases up to the current security constraints.

    ``CREATE TABLE IF NOT EXISTS`` cannot change a primary key and
    ``CREATE INDEX IF NOT EXISTS`` cannot replace an older index definition.
    Both changes are applied explicitly here before verification runs.
    """

    _ensure_columns(connection, "background_jobs", {"dedupe_key": "TEXT"})
    connection.execute("DROP INDEX IF EXISTS idx_background_jobs_active_unique")
    connection.execute(
        """
        CREATE UNIQUE INDEX idx_background_jobs_active_unique
            ON background_jobs(tenant_id, session_id, dedupe_key)
            WHERE dedupe_key IS NOT NULL
              AND status IN ('queued', 'processing')
        """
    )

    table = "langgraph_checkpoint_writes"
    expected_pk = list(
        invariants.REQUIRED_PRIMARY_KEYS["langgraph_checkpoint_writes"]
    )
    if not _table_exists(connection, table):
        return
    if _sqlite_primary_key(connection, table) == expected_pk:
        return

    columns = (
        "tenant_id",
        "thread_id",
        "checkpoint_ns",
        "checkpoint_id",
        "task_id",
        "task_path",
        "channel",
        "idx",
        "value_type",
        "value_blob",
    )
    legacy = f"{table}_legacy"
    connection.execute(f"DROP TABLE IF EXISTS {legacy}")
    connection.execute(f"ALTER TABLE {table} RENAME TO {legacy}")
    connection.execute(
        """
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
                idx
            )
        )
        """
    )
    available = set(_sqlite_columns(connection, legacy))
    shared = [column for column in columns if column in available]
    joined = ", ".join(shared)
    connection.execute(
        f"INSERT INTO {table} ({joined}) SELECT {joined} FROM {legacy}"
    )
    connection.execute(f"DROP TABLE {legacy}")
    connection.execute("DROP INDEX IF EXISTS idx_langgraph_checkpoint_writes_lookup")
    connection.execute(
        """
        CREATE INDEX idx_langgraph_checkpoint_writes_lookup
            ON langgraph_checkpoint_writes(
                tenant_id, thread_id, checkpoint_ns, checkpoint_id
            )
        """
    )


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def ensure_tenant_audit_schema(connection: sqlite3.Connection) -> None:
    """Add tenant and audit columns to current or pre-migration databases."""

    tenant = normalize_tenant_id()

    for table in TENANT_SCOPED_TABLES:
        _ensure_columns(
            connection,
            table,
            {"tenant_id": "TEXT NOT NULL DEFAULT ''"},
        )
        connection.execute(
            f"UPDATE {table} SET tenant_id = ? "
            "WHERE tenant_id IS NULL OR tenant_id = ''",
            (tenant,),
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_tenant "
            f"ON {table}(tenant_id)"
        )

    _ensure_columns(
        connection,
        "session_events",
        {
            "actor": "TEXT NOT NULL DEFAULT ''",
            "action": "TEXT NOT NULL DEFAULT ''",
            "resource": "TEXT NOT NULL DEFAULT ''",
            "request_id": "TEXT NOT NULL DEFAULT ''",
        },
    )

    for table in ("sessions", "analysis_reports"):
        _ensure_columns(
            connection,
            table,
            {
                "created_by": "TEXT NOT NULL DEFAULT ''",
                "updated_by": "TEXT NOT NULL DEFAULT ''",
            },
        )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_action
            ON audit_log(tenant_id, action, created_at)
        """
    )


def tenant_for_session(session_id: str) -> str:
    """Resolve the owning tenant for a session, with a safe fallback."""

    with _connect() as connection:
        row = connection.execute(
            "SELECT tenant_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    if row and row["tenant_id"]:
        return normalize_tenant_id(row["tenant_id"])

    return current_tenant_id()


def configure(database_path=None, *, ensure_schema: bool = True) -> Path:
    """Configure the SQLite file and, by default, ensure its schema exists."""

    global _database_path

    if database_path is None:
        database_path = os.getenv(
            "CVI_READ_MODEL_DB",
            DEFAULT_DATABASE_PATH,
        )

    _database_path = Path(database_path)

    if not ensure_schema:
        return _database_path

    _database_path.parent.mkdir(parents=True, exist_ok=True)

    with _connect() as connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        _ensure_schema_migrations_table(connection)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                source TEXT NOT NULL,
                projection_json TEXT NOT NULL,
                state_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_session_time
                ON session_snapshots(session_id, captured_at, snapshot_id);

            CREATE TABLE IF NOT EXISTS session_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_session_time
                ON session_events(session_id, captured_at, event_id);

            CREATE TABLE IF NOT EXISTS analysis_reports (
                report_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL UNIQUE,
                generated_at TEXT NOT NULL,
                period_start TEXT,
                period_end TEXT,
                customer_segment TEXT NOT NULL,
                product_category TEXT NOT NULL,
                research_goal TEXT,
                status_label TEXT NOT NULL DEFAULT 'unfinished',
                response_pattern_label TEXT NOT NULL DEFAULT 'unclassified',
                tags TEXT NOT NULL DEFAULT '|unfinished|unclassified|',
                report_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reports_dimensions
                ON analysis_reports(
                    period_start,
                    customer_segment,
                    product_category,
                    research_goal
                );

            CREATE TABLE IF NOT EXISTS report_claims (
                claim_id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                category TEXT NOT NULL,
                parameter TEXT NOT NULL,
                statement TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                FOREIGN KEY(report_id)
                    REFERENCES analysis_reports(report_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_report_claims_report
                ON report_claims(report_id, category, parameter);

            CREATE TABLE IF NOT EXISTS claim_evidence (
                claim_id TEXT NOT NULL,
                report_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                response_id TEXT NOT NULL,
                source_quote TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                PRIMARY KEY (claim_id, evidence_id),
                FOREIGN KEY(claim_id)
                    REFERENCES report_claims(claim_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(report_id)
                    REFERENCES analysis_reports(report_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_claim_evidence_response
                ON claim_evidence(response_id, evidence_id);

            CREATE TABLE IF NOT EXISTS response_evidence (
                response_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                question TEXT,
                text TEXT NOT NULL,
                captured_at TEXT,
                participant_id TEXT NOT NULL,
                customer_segment TEXT NOT NULL,
                product_category TEXT NOT NULL,
                period_start TEXT,
                period_end TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_response_evidence_dimensions
                ON response_evidence(
                    session_id,
                    period_start,
                    customer_segment,
                    product_category
                );

            CREATE TABLE IF NOT EXISTS response_outcomes (
                outcome_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                response_id TEXT NOT NULL,
                status TEXT NOT NULL,
                question TEXT,
                transcript TEXT,
                captured_at TEXT,
                participant_id TEXT NOT NULL,
                classification_source TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_response_outcomes_session
                ON response_outcomes(session_id, sequence, outcome_id);

            CREATE TABLE IF NOT EXISTS conversation_timings (
                timing_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn INTEGER NOT NULL,
                span_name TEXT NOT NULL,
                source TEXT NOT NULL,
                started_ms REAL NOT NULL,
                ended_ms REAL NOT NULL,
                duration_ms REAL NOT NULL,
                parent_span_id TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_conversation_timings_session
                ON conversation_timings(session_id, turn, timing_id);

            CREATE TABLE IF NOT EXISTS research_memory_records (
                record_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                source TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_research_memory_records_session
                ON research_memory_records(tenant_id, session_id, captured_at, record_id);

            CREATE INDEX IF NOT EXISTS idx_research_memory_records_type
                ON research_memory_records(tenant_id, record_type, captured_at);

            CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
                tenant_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                checkpoint_id TEXT NOT NULL,
                parent_checkpoint_id TEXT,
                checkpoint_type TEXT NOT NULL,
                checkpoint_blob BLOB NOT NULL,
                metadata_type TEXT NOT NULL,
                metadata_blob BLOB NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (
                    tenant_id,
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id
                )
            );

            CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoints_thread
                ON langgraph_checkpoints(
                    tenant_id,
                    thread_id,
                    checkpoint_ns,
                    created_at
                );

            CREATE TABLE IF NOT EXISTS langgraph_checkpoint_blobs (
                tenant_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                checkpoint_ns TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL,
                version TEXT NOT NULL,
                value_type TEXT NOT NULL,
                value_blob BLOB NOT NULL,
                PRIMARY KEY (
                    tenant_id,
                    thread_id,
                    checkpoint_ns,
                    channel,
                    version
                )
            );

            CREATE TABLE IF NOT EXISTS langgraph_checkpoint_writes (
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
                    idx
                )
            );

            CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoint_writes_lookup
                ON langgraph_checkpoint_writes(
                    tenant_id,
                    thread_id,
                    checkpoint_ns,
                    checkpoint_id
                );

            CREATE TABLE IF NOT EXISTS background_jobs (
                job_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                dedupe_key TEXT,
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

            CREATE INDEX IF NOT EXISTS idx_background_jobs_claim
                ON background_jobs(status, run_after, created_at, job_id);

            CREATE INDEX IF NOT EXISTS idx_background_jobs_session
                ON background_jobs(tenant_id, session_id, job_type, created_at);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_active_unique
                ON background_jobs(tenant_id, session_id, dedupe_key)
                WHERE dedupe_key IS NOT NULL
                  AND status IN ('queued', 'processing');

            CREATE TABLE IF NOT EXISTS voice_output_events (
                event_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_voice_output_events_drain
                ON voice_output_events(
                    tenant_id,
                    session_id,
                    delivered_at,
                    created_at,
                    event_id
                );

            CREATE TABLE IF NOT EXISTS session_pending_actions (
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, session_id)
            );

            CREATE TABLE IF NOT EXISTS session_leases (
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, session_id)
            );
            """
        )

        # Existing databases predate the report labels. SQLite cannot add a
        # NOT NULL column without a default, so use additive defaults here.
        _ensure_columns(
            connection,
            "analysis_reports",
            {
                "status_label": (
                    "TEXT NOT NULL DEFAULT 'unfinished'"
                ),
                "response_pattern_label": (
                    "TEXT NOT NULL DEFAULT 'unclassified'"
                ),
                "tags": (
                    "TEXT NOT NULL DEFAULT '|unfinished|unclassified|'"
                ),
            },
        )

        _ensure_columns(
            connection,
            "background_jobs",
            {
                "fencing_token": "TEXT",
                "heartbeat_at": "TEXT",
            },
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reports_status_pattern
                ON analysis_reports(status_label, response_pattern_label);
            """
        )

        _migrate_legacy_schema_constraints(connection)
        ensure_tenant_audit_schema(connection)
        _record_schema_versions(connection)

    return _database_path


def database_path() -> Path:
    if _database_path is None:
        return configure()

    return _database_path


def health_check() -> tuple[str, str | None]:
    """Return repository readiness without exposing raw connection details."""

    try:
        with _connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return "ok", None
    except sqlite3.Error as exc:
        return "unavailable", str(exc)


def list_research_memory_records(
    tenant_id: str | None = None,
    session_id: str | None = None,
    record_type: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Read append-only authoritative records, newest first.

    All reads are tenant-scoped. ``rowid`` is the append sequence for SQLite and
    breaks timestamp ties deterministically.
    """

    tenant = normalize_tenant_id(tenant_id)

    clauses = ["tenant_id = ?"]
    params: list[Any] = [tenant]

    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)

    if record_type:
        clauses.append("record_type = ?")
        params.append(record_type)

    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        params.append(int(limit))

    sql = f"""
        SELECT
            record_id,
            tenant_id,
            session_id,
            record_type,
            schema_version,
            source,
            captured_at,
            payload_json
        FROM research_memory_records
        WHERE {' AND '.join(clauses)}
        ORDER BY captured_at DESC, rowid DESC
    """

    if limit is not None:
        sql += " LIMIT ?"

    with _connect() as connection:
        rows = connection.execute(sql, params).fetchall()

    records = []
    for row in rows:
        record = dict(row)
        payload_json = record.pop("payload_json")
        record["payload"] = json.loads(payload_json)
        records.append(record)

    return records


def save_checkpoint(
    *,
    tenant_id: str | None,
    thread_id: str,
    checkpoint_ns: str,
    checkpoint_id: str,
    parent_checkpoint_id: str | None,
    checkpoint_type: str,
    checkpoint_blob: bytes,
    metadata_type: str,
    metadata_blob: bytes,
    created_at: str,
) -> None:
    """Upsert one serialized LangGraph checkpoint for a tenant thread."""

    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO langgraph_checkpoints (
                tenant_id,
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                parent_checkpoint_id,
                checkpoint_type,
                checkpoint_blob,
                metadata_type,
                metadata_blob,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                tenant_id,
                thread_id,
                checkpoint_ns,
                checkpoint_id
            ) DO UPDATE SET
                parent_checkpoint_id = excluded.parent_checkpoint_id,
                checkpoint_type = excluded.checkpoint_type,
                checkpoint_blob = excluded.checkpoint_blob,
                metadata_type = excluded.metadata_type,
                metadata_blob = excluded.metadata_blob,
                created_at = excluded.created_at
            """,
            (
                tenant,
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                parent_checkpoint_id,
                checkpoint_type,
                checkpoint_blob,
                metadata_type,
                metadata_blob,
                created_at,
            ),
        )


def get_checkpoint(
    *,
    tenant_id: str | None,
    thread_id: str,
    checkpoint_ns: str,
    checkpoint_id: str | None = None,
) -> dict | None:
    """Return one serialized checkpoint, or the latest one for a thread."""

    tenant = normalize_tenant_id(tenant_id)
    clauses = [
        "tenant_id = ?",
        "thread_id = ?",
        "checkpoint_ns = ?",
    ]
    params: list[Any] = [tenant, thread_id, checkpoint_ns]

    if checkpoint_id:
        clauses.append("checkpoint_id = ?")
        params.append(checkpoint_id)

    sql = f"""
        SELECT
            tenant_id,
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            parent_checkpoint_id,
            checkpoint_type,
            checkpoint_blob,
            metadata_type,
            metadata_blob,
            created_at
        FROM langgraph_checkpoints
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
    """

    with _connect() as connection:
        row = connection.execute(sql, params).fetchone()

    return dict(row) if row else None


def list_checkpoints(
    *,
    tenant_id: str | None,
    thread_id: str,
    checkpoint_ns: str,
    before_checkpoint_id: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """List serialized checkpoints newest first, optionally before one ID."""

    tenant = normalize_tenant_id(tenant_id)
    params: list[Any] = [tenant, thread_id, checkpoint_ns]
    before_clause = ""

    if before_checkpoint_id:
        before_clause = """
            AND (created_at, rowid) < (
                SELECT created_at, rowid
                FROM langgraph_checkpoints
                WHERE tenant_id = ? AND thread_id = ?
                  AND checkpoint_ns = ? AND checkpoint_id = ?
            )
        """
        params.extend([tenant, thread_id, checkpoint_ns, before_checkpoint_id])

    sql = f"""
        SELECT
            tenant_id,
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            parent_checkpoint_id,
            checkpoint_type,
            checkpoint_blob,
            metadata_type,
            metadata_blob,
            created_at
        FROM langgraph_checkpoints
        WHERE tenant_id = ? AND thread_id = ? AND checkpoint_ns = ?
        {before_clause}
        ORDER BY created_at DESC, rowid DESC
    """

    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        sql += " LIMIT ?"
        params.append(limit)

    with _connect() as connection:
        rows = connection.execute(sql, params).fetchall()

    return [dict(row) for row in rows]


def save_checkpoint_blobs(
    *,
    tenant_id: str | None,
    thread_id: str,
    checkpoint_ns: str,
    blobs: list[tuple[str, str, str, bytes]],
) -> None:
    """Store serialized channel values keyed by their LangGraph version."""

    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        connection.executemany(
            """
            INSERT INTO langgraph_checkpoint_blobs (
                tenant_id,
                thread_id,
                checkpoint_ns,
                channel,
                version,
                value_type,
                value_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                tenant_id,
                thread_id,
                checkpoint_ns,
                channel,
                version
            ) DO UPDATE SET
                value_type = excluded.value_type,
                value_blob = excluded.value_blob
            """,
            [
                (
                    tenant,
                    thread_id,
                    checkpoint_ns,
                    channel,
                    version,
                    value_type,
                    value_blob,
                )
                for channel, version, value_type, value_blob in blobs
            ],
        )


def get_checkpoint_blobs(
    *,
    tenant_id: str | None,
    thread_id: str,
    checkpoint_ns: str,
    keys: list[tuple[str, str]],
) -> list[dict]:
    """Return serialized channel values for the requested version keys."""

    if not keys:
        return []

    tenant = normalize_tenant_id(tenant_id)
    clauses = [
        "(channel = ? AND version = ?)"
        for _ in keys
    ]
    params: list[Any] = [tenant, thread_id, checkpoint_ns]

    for channel, version in keys:
        params.extend([channel, version])

    sql = f"""
        SELECT channel, version, value_type, value_blob
        FROM langgraph_checkpoint_blobs
        WHERE tenant_id = ? AND thread_id = ? AND checkpoint_ns = ?
          AND ({' OR '.join(clauses)})
    """

    with _connect() as connection:
        rows = connection.execute(sql, params).fetchall()

    return [dict(row) for row in rows]


def save_checkpoint_writes(
    *,
    tenant_id: str | None,
    thread_id: str,
    checkpoint_ns: str,
    checkpoint_id: str,
    writes: list[tuple[str, str, str, int, str, bytes]],
) -> None:
    """Persist pending/intermediate writes attached to one checkpoint.

    Regular channel writes are first-write-wins, matching LangGraph's semantics.
    Special indexes such as ``__interrupt__`` are replaced on later writes.
    """

    tenant = normalize_tenant_id(tenant_id)
    rows = [
        (
            tenant,
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            task_id,
            task_path,
            channel,
            idx,
            value_type,
            value_blob,
        )
        for task_id, task_path, channel, idx, value_type, value_blob in writes
    ]
    regular = [row for row in rows if row[7] >= 0]
    special = [row for row in rows if row[7] < 0]

    with _connect() as connection:
        connection.executemany(
            """
            INSERT INTO langgraph_checkpoint_writes (
                tenant_id,
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                task_id,
                task_path,
                channel,
                idx,
                value_type,
                value_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                tenant_id,
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                task_id,
                idx
            ) DO NOTHING
            """,
            regular,
        )
        connection.executemany(
            """
            INSERT INTO langgraph_checkpoint_writes (
                tenant_id,
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                task_id,
                task_path,
                channel,
                idx,
                value_type,
                value_blob
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                tenant_id,
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                task_id,
                idx
            ) DO UPDATE SET
                task_path = excluded.task_path,
                channel = excluded.channel,
                value_type = excluded.value_type,
                value_blob = excluded.value_blob
            """,
            special,
        )


def get_checkpoint_writes(
    *,
    tenant_id: str | None,
    thread_id: str,
    checkpoint_ns: str,
    checkpoint_id: str,
) -> list[dict]:
    """Return serialized writes in deterministic execution order."""

    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                task_id,
                task_path,
                channel,
                idx,
                value_type,
                value_blob
            FROM langgraph_checkpoint_writes
            WHERE tenant_id = ? AND thread_id = ?
              AND checkpoint_ns = ? AND checkpoint_id = ?
            ORDER BY rowid
            """,
            (tenant, thread_id, checkpoint_ns, checkpoint_id),
        ).fetchall()

    return [dict(row) for row in rows]


def delete_checkpoint_thread(
    *,
    tenant_id: str | None,
    thread_id: str,
    checkpoint_ns: str | None = None,
) -> None:
    """Delete all checkpoints, blobs, and writes for one tenant thread."""

    tenant = normalize_tenant_id(tenant_id)
    clauses = ["tenant_id = ?", "thread_id = ?"]
    params: list[Any] = [tenant, thread_id]

    if checkpoint_ns is not None:
        clauses.append("checkpoint_ns = ?")
        params.append(checkpoint_ns)

    where = " AND ".join(clauses)

    with _connect() as connection:
        for table in (
            "langgraph_checkpoint_writes",
            "langgraph_checkpoints",
            "langgraph_checkpoint_blobs",
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE {where}",
                params,
            )


def _job_from_row(row: Any) -> dict | None:
    if row is None:
        return None

    job = dict(row)
    job["payload"] = json.loads(job.pop("payload_json"))

    result_json = job.pop("result_json", None)
    job["result"] = json.loads(result_json) if result_json else None

    return job


def _retry_after(delay_seconds: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=max(0, delay_seconds))
    ).isoformat(timespec="milliseconds")


def with_job_identity(
    payload: dict | None,
    *,
    job_id: str,
    job_type: str,
    session_id: str,
    tenant_id: str,
) -> dict:
    """Stamp authoritative row identity into the stored job payload."""

    stored = dict(payload or {})
    stored.update(
        {
            "job_id": job_id,
            "job_type": job_type,
            "session_id": session_id,
            "tenant_id": tenant_id,
        }
    )
    return stored


def enqueue_job(
    *,
    job_id: str,
    job_type: str,
    session_id: str,
    payload: dict,
    tenant_id: str | None = None,
    max_attempts: int = 3,
    unique_active: bool = False,
    run_after: str | None = None,
) -> dict:
    """Append one durable background job in the queued state."""

    tenant = normalize_tenant_id(tenant_id)
    now = _utc_now()
    available_at = run_after or now

    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")

        if unique_active:
            existing = connection.execute(
                """
                SELECT job_id
                FROM background_jobs
                WHERE tenant_id = ? AND session_id = ? AND dedupe_key = ?
                  AND status IN ('queued', 'processing')
                LIMIT 1
                """,
                (tenant, session_id, job_type),
            ).fetchone()

            if existing:
                raise DuplicateJobError(
                    f"Active {job_type} job already exists for {session_id}."
                )

        connection.execute(
            """
            INSERT INTO background_jobs (
                job_id,
                tenant_id,
                session_id,
                job_type,
                dedupe_key,
                status,
                payload_json,
                result_json,
                error,
                attempts,
                max_attempts,
                run_after,
                worker_id,
                created_at,
                updated_at,
                started_at,
                completed_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, NULL, 0, ?, ?, NULL, ?, ?, NULL, NULL)
            """,
            (
                job_id,
                tenant,
                session_id,
                job_type,
                job_type if unique_active else None,
                _json(
                    with_job_identity(
                        payload,
                        job_id=job_id,
                        job_type=job_type,
                        session_id=session_id,
                        tenant_id=tenant,
                    )
                ),
                int(max_attempts),
                available_at,
                now,
                now,
            ),
        )

        row = connection.execute(
            "SELECT * FROM background_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()

    return _job_from_row(row)


def claim_jobs(
    *,
    worker_id: str,
    job_types: list[str] | None = None,
    tenant_id: str | None = None,
    limit: int = 1,
    fencing_token: str | None = None,
) -> list[dict]:
    """Atomically claim due jobs for one worker."""

    if limit <= 0:
        return []

    tenant = normalize_tenant_id(tenant_id) if tenant_id is not None else None
    clauses = ["status = ?", "run_after <= ?"]
    params: list[Any] = ["queued", _utc_now()]

    if tenant is not None:
        clauses.append("tenant_id = ?")
        params.append(tenant)

    if job_types:
        clauses.append(f"job_type IN ({','.join('?' for _ in job_types)})")
        params.extend(job_types)

    params.append(int(limit))
    sql = f"""
        SELECT *
        FROM background_jobs
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at, job_id
        LIMIT ?
    """

    now = _utc_now()

    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(sql, params).fetchall()

        claimed = []
        for row in rows:
            job_id = row["job_id"]
            token = fencing_token or f"lease_{uuid4().hex}"
            connection.execute(
                """
                UPDATE background_jobs
                SET status = 'processing',
                    attempts = attempts + 1,
                    worker_id = ?,
                    fencing_token = ?,
                    heartbeat_at = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (worker_id, token, now, now, now, job_id),
            )
            updated = connection.execute(
                "SELECT * FROM background_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            claimed.append(_job_from_row(updated))

    return claimed


def get_job(*, job_id: str, tenant_id: str | None = None) -> dict | None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM background_jobs WHERE job_id = ? AND tenant_id = ?",
            (job_id, tenant),
        ).fetchone()

    return _job_from_row(row)


def latest_job(
    *,
    session_id: str,
    job_type: str,
    tenant_id: str | None = None,
) -> dict | None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM background_jobs
            WHERE tenant_id = ? AND session_id = ? AND job_type = ?
            ORDER BY created_at DESC, job_id DESC
            LIMIT 1
            """,
            (tenant, session_id, job_type),
        ).fetchone()

    return _job_from_row(row)


def has_active_job(
    *,
    session_id: str,
    job_type: str,
    tenant_id: str | None = None,
) -> bool:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT job_id
            FROM background_jobs
            WHERE tenant_id = ? AND session_id = ? AND job_type = ?
              AND status IN ('queued', 'processing')
            LIMIT 1
            """,
            (tenant, session_id, job_type),
        ).fetchone()

    return row is not None


def count_pending_jobs(
    *,
    session_id: str,
    job_type: str,
    tenant_id: str | None = None,
) -> int:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM background_jobs
            WHERE tenant_id = ? AND session_id = ? AND job_type = ?
              AND status IN ('queued', 'processing')
            """,
            (tenant, session_id, job_type),
        ).fetchone()

    return int(row["count"]) if row else 0


def complete_job(
    *,
    job_id: str,
    result: dict | None,
    tenant_id: str | None = None,
    fencing_token: str | None = None,
) -> dict | None:
    """Complete only a processing job held by the current owner."""

    tenant = normalize_tenant_id(tenant_id)
    now = _utc_now()

    with _connect() as connection:
        connection.execute(
            """
            UPDATE background_jobs
            SET status = 'completed',
                result_json = ?,
                error = NULL,
                fencing_token = NULL,
                heartbeat_at = NULL,
                completed_at = ?,
                updated_at = ?
            WHERE job_id = ?
              AND tenant_id = ?
              AND status = 'processing'
              AND fencing_token = ?
            """,
            (
                _json(result or {}),
                now,
                now,
                job_id,
                tenant,
                fencing_token,
            ),
        )

    return get_job(job_id=job_id, tenant_id=tenant)


def fail_job(
    *,
    job_id: str,
    error: str,
    tenant_id: str | None = None,
    retry_delay_seconds: float = 0,
    fencing_token: str | None = None,
) -> dict | None:
    """Fail or requeue only a processing job held by the current owner."""

    tenant = normalize_tenant_id(tenant_id)
    now = _utc_now()

    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT attempts, max_attempts
            FROM background_jobs
            WHERE job_id = ?
              AND tenant_id = ?
              AND status = 'processing'
              AND fencing_token = ?
            """,
            (job_id, tenant, fencing_token),
        ).fetchone()

        if row is not None:
            can_retry = int(row["attempts"]) < int(row["max_attempts"])

            if can_retry:
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'queued',
                        error = ?,
                        run_after = ?,
                        worker_id = NULL,
                        fencing_token = NULL,
                        heartbeat_at = NULL,
                        updated_at = ?
                    WHERE job_id = ? AND tenant_id = ?
                    """,
                    (
                        str(error),
                        _retry_after(retry_delay_seconds),
                        now,
                        job_id,
                        tenant,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'failed',
                        error = ?,
                        worker_id = NULL,
                        fencing_token = NULL,
                        heartbeat_at = NULL,
                        completed_at = ?,
                        updated_at = ?
                    WHERE job_id = ? AND tenant_id = ?
                    """,
                    (str(error), now, now, job_id, tenant),
                )

    return get_job(job_id=job_id, tenant_id=tenant)


def heartbeat_job(
    *,
    job_id: str,
    fencing_token: str,
    tenant_id: str | None = None,
) -> dict | None:
    """Refresh a claimed job's liveness if the caller still owns it."""

    tenant = normalize_tenant_id(tenant_id)
    now = _utc_now()

    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE background_jobs
            SET heartbeat_at = ?,
                updated_at = ?
            WHERE job_id = ?
              AND tenant_id = ?
              AND status = 'processing'
              AND fencing_token = ?
            """,
            (now, now, job_id, tenant, fencing_token),
        )

        if not cursor.rowcount:
            return None

    return get_job(job_id=job_id, tenant_id=tenant)


def recover_stale_jobs(
    *,
    stale_before: str,
    tenant_id: str | None = None,
) -> int:
    """Requeue or fail jobs whose worker stopped updating them."""

    tenant = normalize_tenant_id(tenant_id)
    now = _utc_now()
    clauses = [
        "status = 'processing'",
        "COALESCE(heartbeat_at, updated_at) < ?",
    ]
    params: list[Any] = [stale_before]

    if tenant_id is not None:
        clauses.append("tenant_id = ?")
        params.append(tenant)

    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            f"""
            SELECT job_id, attempts, max_attempts
            FROM background_jobs
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchall()

        for row in rows:
            if int(row["attempts"]) < int(row["max_attempts"]):
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'queued',
                        run_after = ?,
                        worker_id = NULL,
                        fencing_token = NULL,
                        heartbeat_at = NULL,
                        error = 'worker_stale',
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (now, now, row["job_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE background_jobs
                    SET status = 'failed',
                        error = 'worker_stale',
                        worker_id = NULL,
                        fencing_token = NULL,
                        heartbeat_at = NULL,
                        completed_at = ?,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (now, now, row["job_id"]),
                )

    return len(rows)


def enqueue_voice_event(
    *,
    event_id: str,
    session_id: str,
    event: dict,
    tenant_id: str | None = None,
) -> None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO voice_output_events (
                event_id,
                tenant_id,
                session_id,
                payload_json,
                created_at,
                delivered_at
            ) VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (event_id, tenant, session_id, _json(event), _utc_now()),
        )


def list_voice_events(
    *,
    session_id: str,
    tenant_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return undelivered events without changing their delivery state."""

    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT event_id, payload_json
            FROM voice_output_events
            WHERE tenant_id = ? AND session_id = ? AND delivered_at IS NULL
            ORDER BY created_at, event_id
            LIMIT ?
            """,
            (tenant, session_id, int(limit)),
        ).fetchall()

    return [
        {
            "event_id": row["event_id"],
            "event": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


def acknowledge_voice_events(
    *,
    session_id: str,
    event_ids: list[str],
    tenant_id: str | None = None,
) -> int:
    """Mark listed events delivered for one session only."""

    tenant = normalize_tenant_id(tenant_id)
    ids = [str(event_id) for event_id in event_ids if event_id][:1000]

    if not ids:
        return 0

    now = _utc_now()
    placeholders = ", ".join("?" for _ in ids)

    with _connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE voice_output_events
            SET delivered_at = ?
            WHERE tenant_id = ?
              AND session_id = ?
              AND delivered_at IS NULL
              AND event_id IN ({placeholders})
            """,
            (now, tenant, session_id, *ids),
        )

    return int(cursor.rowcount or 0)


def drain_voice_events(
    *,
    session_id: str,
    tenant_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Backward-compatible drain: list now, acknowledge explicitly."""

    records = list_voice_events(
        session_id=session_id,
        tenant_id=tenant_id,
        limit=limit,
    )

    acknowledge_voice_events(
        session_id=session_id,
        tenant_id=tenant_id,
        event_ids=[record["event_id"] for record in records],
    )

    return [record["event"] for record in records]


def save_pending_action(
    *,
    session_id: str,
    payload: dict,
    tenant_id: str | None = None,
) -> None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO session_pending_actions (
                tenant_id,
                session_id,
                payload_json,
                updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT (tenant_id, session_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (tenant, session_id, _json(payload or {}), _utc_now()),
        )


def get_pending_action(
    *,
    session_id: str,
    tenant_id: str | None = None,
) -> dict | None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM session_pending_actions
            WHERE tenant_id = ? AND session_id = ?
            """,
            (tenant, session_id),
        ).fetchone()

    return json.loads(row["payload_json"]) if row else None


def clear_pending_action(
    *,
    session_id: str,
    tenant_id: str | None = None,
) -> None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        connection.execute(
            """
            DELETE FROM session_pending_actions
            WHERE tenant_id = ? AND session_id = ?
            """,
            (tenant, session_id),
        )


def acquire_session_lease(
    *,
    session_id: str,
    owner_id: str,
    ttl_seconds: float = 300,
    tenant_id: str | None = None,
) -> bool:
    """Atomically claim a short-lived, renewable per-session processing lease."""

    tenant = normalize_tenant_id(tenant_id)
    now = _utc_now()
    expires_at = _lease_expiry(ttl_seconds)

    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT owner_id, expires_at
            FROM session_leases
            WHERE tenant_id = ? AND session_id = ?
            """,
            (tenant, session_id),
        ).fetchone()

        if (
            row is not None
            and row["owner_id"] != owner_id
            and row["expires_at"] > now
        ):
            return False

        connection.execute(
            """
            INSERT INTO session_leases (
                tenant_id,
                session_id,
                owner_id,
                expires_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(tenant_id, session_id) DO UPDATE SET
                owner_id = excluded.owner_id,
                expires_at = excluded.expires_at
            """,
            (tenant, session_id, owner_id, expires_at),
        )

    return True


def renew_session_lease(
    *,
    session_id: str,
    owner_id: str,
    ttl_seconds: float = 300,
    tenant_id: str | None = None,
) -> bool:
    tenant = normalize_tenant_id(tenant_id)
    expires_at = _lease_expiry(ttl_seconds)

    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE session_leases
            SET expires_at = ?
            WHERE tenant_id = ?
              AND session_id = ?
              AND owner_id = ?
              AND expires_at > ?
            """,
            (
                expires_at,
                tenant,
                session_id,
                owner_id,
                _utc_now(),
            ),
        )

    return cursor.rowcount > 0


def release_session_lease(
    *,
    session_id: str,
    owner_id: str,
    tenant_id: str | None = None,
) -> None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        connection.execute(
            """
            DELETE FROM session_leases
            WHERE tenant_id = ?
              AND session_id = ?
              AND owner_id = ?
            """,
            (tenant, session_id, owner_id),
        )


def get_session_lease(
    *,
    session_id: str,
    tenant_id: str | None = None,
) -> dict | None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT owner_id, expires_at
            FROM session_leases
            WHERE tenant_id = ? AND session_id = ?
            """,
            (tenant, session_id),
        ).fetchone()

    return dict(row) if row else None


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _ensure_schema_migrations_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT ''
        )
        """
    )
    _ensure_columns(
        connection,
        "schema_migrations",
        {"description": "TEXT NOT NULL DEFAULT ''"},
    )


def _record_schema_versions(connection: sqlite3.Connection) -> None:
    descriptions = {
        BASELINE_SCHEMA_VERSION: "Baseline read-model schema",
        TENANT_AUDIT_SCHEMA_VERSION: "Tenant and audit fields",
        AUTHORITATIVE_MEMORY_SCHEMA_VERSION: "Authoritative research memory contract records",
        CHECKPOINT_SCHEMA_VERSION: "Durable LangGraph checkpoint records",
        WORKER_QUEUE_SCHEMA_VERSION: "Durable worker queue and voice outbox",
        SESSION_LEASE_SCHEMA_VERSION: "Durable per-session worker leases",
        WORKER_FENCING_SCHEMA_VERSION: "Worker fencing tokens and heartbeats",
    }
    for version in SCHEMA_VERSIONS:
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (version, _utc_now(), descriptions.get(version, version)),
        )


def apply_migrations(database_path: Any = None) -> list[str]:
    """Configure DB and return applied schema versions."""
    configure(database_path)
    with _connect() as connection:
        rows = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    return [row["version"] for row in rows]


def _sqlite_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def _sqlite_primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = [
        row
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        if row["pk"]
    ]
    rows.sort(key=lambda row: row["pk"])
    return [row["name"] for row in rows]


def verify_migration_invariants(database_path: Any = None) -> list[str]:
    """Return schema-integrity issues after migrations; empty means healthy.

    Verification never applies schema: a dropped constraint must remain
    missing so the caller can fail closed instead of silently repairing it.
    """

    global _database_path

    if database_path is not None:
        _database_path = Path(database_path)

    issues: list[str] = []

    with _connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        for table in invariants.REQUIRED_TABLES:
            if table not in tables:
                issues.append(f"table {table} is missing")

        for table in invariants.TENANT_SCOPED_TABLES:
            columns = _sqlite_columns(connection, table)
            if "tenant_id" not in columns:
                issues.append(f"table {table} is missing tenant_id")

        for table, required in invariants.REQUIRED_COLUMNS.items():
            columns = _sqlite_columns(connection, table)
            for column in required:
                if column not in columns:
                    issues.append(f"table {table} is missing {column}")

        for table, expected in invariants.REQUIRED_PRIMARY_KEYS.items():
            actual = _sqlite_primary_key(connection, table)
            if actual != list(expected):
                issues.append(
                    f"table {table} primary key is {actual!r}, expected {list(expected)!r}"
                )

        for name, spec in invariants.REQUIRED_INDEXES.items():
            rows = connection.execute(
                f"PRAGMA index_list({spec['table']})"
            ).fetchall()
            index = next((row for row in rows if row["name"] == name), None)
            if index is None:
                issues.append(f"index {name} is missing")
                continue

            if bool(index["unique"]) != spec["unique"]:
                issues.append(f"index {name} uniqueness changed")
            if spec["partial"] and not bool(index["partial"]):
                issues.append(f"index {name} is no longer partial")

            columns = [
                row["name"]
                for row in connection.execute(f"PRAGMA index_info({name})").fetchall()
            ]
            if columns != list(spec["columns"]):
                issues.append(
                    f"index {name} columns are {columns!r}, "
                    f"expected {list(spec['columns'])!r}"
                )

    return sorted(issues)


def _json(value) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _record_memory_record(
    connection: sqlite3.Connection,
    *,
    tenant_id: str,
    session_id: str,
    record_type: str,
    source: str,
    captured_at: str,
    payload: dict,
    schema_version: str = AUTHORITATIVE_MEMORY_SCHEMA_VERSION,
) -> None:
    """Write an append-only authoritative memory record.

    This is additive and does not change interview behavior; existing read-model
    tables remain the compatibility surface used by the API/UI.
    """
    record_id = f"{record_type}:{session_id}:{captured_at}:{os.urandom(4).hex()}"
    connection.execute(
        """
        INSERT OR IGNORE INTO research_memory_records (
            record_id,
            tenant_id,
            session_id,
            record_type,
            schema_version,
            source,
            captured_at,
            payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            tenant_id,
            session_id,
            record_type,
            schema_version,
            source,
            captured_at,
            _json(payload),
        ),
    )


def flatten_knowledge_items(state: dict) -> list[dict]:
    """Flatten structured knowledge into traceable researcher records."""

    items = []

    for field in RESEARCHER_SINGLE_KNOWLEDGE_FIELDS:
        item = state.get(field)

        if not isinstance(item, dict) or not item:
            continue

        items.append({
            "category": field,
            "parameter": field,
            **item,
        })

    for field in RESEARCHER_DICTIONARY_KNOWLEDGE_FIELDS:
        field_value = state.get(field)

        if not isinstance(field_value, dict):
            continue

        for parameter, item in field_value.items():
            if not isinstance(item, dict):
                continue

            items.append({
                "category": field,
                "parameter": parameter,
                **item,
            })

    return items


def build_conversation_timeline(state: dict) -> list[dict]:
    """Pair asked questions with stored consumer responses."""

    questions = state.get("questions_asked", [])
    responses = state.get("responses", [])

    timeline = []

    for index, question in enumerate(questions):
        timeline.append({
            "turn": index + 1,
            "question": question,
            "response": (
                responses[index]
                if index < len(responses)
                else None
            ),
        })

    for index in range(len(questions), len(responses)):
        timeline.append({
            "turn": index + 1,
            "question": None,
            "response": responses[index],
        })

    return timeline


def derive_activity(session: dict, state: dict) -> str:
    """Derive a researcher-facing phase from persisted state."""

    if is_completed(state):
        return "completed"

    if state.get("response_status") == "failed":
        return "failed"

    return session.get("status", "idle")


def build_projection(
    session: dict,
    state: dict,
    activity: str | None = None,
) -> dict:
    """Build the dashboard-facing view of one session state."""

    state = state or {}
    knowledge = flatten_knowledge_items(state)
    conversation = build_conversation_timeline(state)
    gaps = state.get("unresolved_gaps", [])

    return {
        "session": {
            **session,
            "activity": activity or derive_activity(session, state),
        },
        "checkpoint_found": bool(state),
        "iteration_count": state.get("iteration_count", 0),
        "research_context": (
            state.get("research_context")
            or {
                "company_name": session.get("company_name"),
                "product_name": session.get("product_name"),
                "product_category": session.get("product_category"),
                "research_goal": session.get("research_goal"),
            }
        ),
        "meta_plan": state.get("meta_plan"),
        "conversation": conversation,
        "response_records": state.get("response_records", []),
        "knowledge": knowledge,
        "quality": {
            "information_sufficiency": state.get(
                "information_sufficiency",
                {},
            ),
            "confidence": state.get("confidence", {}),
            "criticality": state.get("criticality", {}),
            "verification_status": state.get(
                "verification_status",
                {},
            ),
            "research_goal_alignment": state.get(
                "research_goal_alignment",
                {},
            ),
        },
        "gaps": gaps,
        "selected_proposal": state.get("selected_proposal"),
        "reflection": state.get("reflection"),
        "final_insights": state.get("final_insights"),
        "state_history_count": len(state.get("state_history", [])),
        "evidence_count": sum(
            len(item.get("values", []))
            for item in knowledge
        ),
        "questions_asked_count": len(
            state.get("questions_asked", [])
        ),
        "responses_count": len(state.get("responses", [])),
        "unresolved_gaps_count": len(gaps),
        "has_final_insights": bool(state.get("final_insights")),
    }


def upsert_session(session: dict) -> None:
    """Insert or update session metadata without losing created_at."""

    now = _utc_now()
    tenant = normalize_tenant_id(session.get("tenant_id"))
    created_by = str(session.get("created_by") or "")

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO sessions (
                session_id,
                tenant_id,
                payload_json,
                created_at,
                updated_at,
                created_by,
                updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            (
                session["session_id"],
                tenant,
                _json(session),
                now,
                now,
                created_by,
                created_by,
            ),
        )


def get_session(session_id: str, tenant_id: str | None = None) -> dict | None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT payload_json
            FROM sessions
            WHERE session_id = ? AND tenant_id = ?
            """,
            (session_id, tenant),
        ).fetchone()

    if row is None:
        return None

    session = json.loads(row["payload_json"])

    # JSON serializes date fields as strings. Restore them so hydrated
    # sessions keep the same typed contract as newly created sessions.
    for field in ("start_date", "end_date"):
        if field in session:
            session[field] = normalize_date(session[field])

    return session


def get_session_created_at(
    session_id: str,
    tenant_id: str | None = None,
) -> str | None:
    """Return the original creation timestamp for a session."""

    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT created_at
            FROM sessions
            WHERE session_id = ? AND tenant_id = ?
            """,
            (session_id, tenant),
        ).fetchone()

    return row["created_at"] if row else None


def list_sessions(tenant_id: str | None = None) -> list[dict]:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT payload_json
            FROM sessions
            WHERE tenant_id = ?
            ORDER BY created_at DESC, session_id DESC
            """,
            (tenant,),
        ).fetchall()

    return [json.loads(row["payload_json"]) for row in rows]


def record_snapshot(
    session_id: str,
    state: dict,
    source: str,
    activity: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Persist a state projection and its traceable source state."""

    tenant = normalize_tenant_id(tenant_id or tenant_for_session(session_id))
    session = get_session(session_id, tenant_id=tenant) or {
        "session_id": session_id,
        "status": "unknown",
    }

    projection = build_projection(session, state, activity)
    now = _utc_now()

    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO session_snapshots (
                session_id,
                tenant_id,
                captured_at,
                source,
                projection_json,
                state_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                tenant,
                now,
                source,
                _json(projection),
                _json(state or {}),
            ),
        )
        snapshot_id = cursor.lastrowid
        _record_memory_record(
            connection,
            tenant_id=tenant,
            session_id=session_id,
            record_type="state_snapshot",
            source=source,
            captured_at=now,
            payload={
                "snapshot_id": snapshot_id,
                "activity": projection.get("session", {}).get("activity"),
                "state": state or {},
            },
        )

    return {
        "snapshot_id": snapshot_id,
        "session_id": session_id,
        "captured_at": now,
        "source": source,
    }


def get_latest_snapshot(
    session_id: str,
    tenant_id: str | None = None,
) -> dict | None:
    tenant = normalize_tenant_id(tenant_id or tenant_for_session(session_id))

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT
                captured_at,
                source,
                projection_json,
                state_json
            FROM session_snapshots
            WHERE session_id = ? AND tenant_id = ?
            ORDER BY captured_at DESC, snapshot_id DESC
            LIMIT 1
            """,
            (session_id, tenant),
        ).fetchone()

    if row is None:
        return None

    projection = json.loads(row["projection_json"])
    state = json.loads(row["state_json"])

    # The snapshot may have been captured before a decision-policy stop was
    # recorded. Override only terminal phases so a completed interview is
    # never shown as still processing, while non-terminal activity captured
    # at snapshot time is preserved.
    session_view = projection.get("session") or {}
    derived_activity = derive_activity(session_view, state)
    if derived_activity in ("completed", "failed"):
        projection["session"] = {
            **session_view,
            "activity": derived_activity,
        }
    projection["snapshot"] = {
        "captured_at": row["captured_at"],
        "source": row["source"],
        "state": state,
    }

    return projection


def list_snapshot_metadata(
    session_id: str,
    tenant_id: str | None = None,
) -> list[dict]:
    tenant = normalize_tenant_id(tenant_id or tenant_for_session(session_id))

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                snapshot_id,
                captured_at,
                source
            FROM session_snapshots
            WHERE session_id = ? AND tenant_id = ?
            ORDER BY captured_at DESC, snapshot_id DESC
            """,
            (session_id, tenant),
        ).fetchall()

    return [dict(row) for row in rows]


def record_event(
    session_id: str,
    event_type: str,
    payload: dict | None = None,
    actor: str = "",
    action: str = "",
    resource: str = "",
    request_id: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    now = _utc_now()
    tenant = normalize_tenant_id(tenant_id or tenant_for_session(session_id))
    resolved_request_id = request_id if request_id is not None else audit_request_id()

    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO session_events (
                session_id,
                tenant_id,
                captured_at,
                event_type,
                payload_json,
                actor,
                action,
                resource,
                request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                tenant,
                now,
                event_type,
                _json(payload or {}),
                actor,
                action,
                resource,
                resolved_request_id,
            ),
        )
        event_id = cursor.lastrowid
        _record_memory_record(
            connection,
            tenant_id=tenant,
            session_id=session_id,
            record_type="event",
            source=event_type,
            captured_at=now,
            payload={
                "event_id": event_id,
                "event_type": event_type,
                "actor": actor,
                "action": action,
                "resource": resource,
                "request_id": resolved_request_id,
                "payload": payload or {},
            },
        )

    return {
        "event_id": event_id,
        "session_id": session_id,
        "captured_at": now,
        "event_type": event_type,
    }


def list_events(session_id: str, tenant_id: str | None = None) -> list[dict]:
    tenant = normalize_tenant_id(tenant_id or tenant_for_session(session_id))

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                event_id,
                captured_at,
                event_type,
                payload_json,
                actor,
                action,
                resource,
                request_id
            FROM session_events
            WHERE session_id = ? AND tenant_id = ?
            ORDER BY captured_at DESC, event_id DESC
            """,
            (session_id, tenant),
        ).fetchall()

    return [
        {
            "event_id": row["event_id"],
            "captured_at": row["captured_at"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "actor": row["actor"],
            "action": row["action"],
            "resource": row["resource"],
            "request_id": row["request_id"],
        }
        for row in rows
    ]


def record_timing(
    session_id: str,
    turn: int,
    span_name: str,
    started_ms: float,
    ended_ms: float,
    duration_ms: float,
    source: str = "runtime",
    parent_span_id: str | None = None,
    metadata: dict | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Persist one conversation latency span."""

    tenant = normalize_tenant_id(tenant_id or tenant_for_session(session_id))

    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO conversation_timings (
                session_id,
                tenant_id,
                turn,
                span_name,
                source,
                started_ms,
                ended_ms,
                duration_ms,
                parent_span_id,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                tenant,
                int(turn),
                span_name,
                source,
                float(started_ms),
                float(ended_ms),
                float(duration_ms),
                parent_span_id,
                _json(metadata or {}),
            ),
        )
        timing_id = cursor.lastrowid

    return {
        "timing_id": timing_id,
        "session_id": session_id,
        "turn": int(turn),
        "span_name": span_name,
    }


def list_timings(
    session_id: str,
    span_name: str | None = None,
    source: str | None = None,
    tenant_id: str | None = None,
) -> list[dict]:
    """Return timing spans in chronological order."""

    tenant = normalize_tenant_id(tenant_id or tenant_for_session(session_id))

    where = ["session_id = ?", "tenant_id = ?"]
    parameters: list[Any] = [session_id, tenant]

    if span_name:
        where.append("span_name = ?")
        parameters.append(span_name)

    if source:
        where.append("source = ?")
        parameters.append(source)

    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                timing_id,
                session_id,
                turn,
                span_name,
                source,
                started_ms,
                ended_ms,
                duration_ms,
                parent_span_id,
                metadata_json
            FROM conversation_timings
            WHERE {' AND '.join(where)}
            ORDER BY started_ms, timing_id
            """,
            parameters,
        ).fetchall()

    return [
        {
            "timing_id": row["timing_id"],
            "session_id": row["session_id"],
            "turn": row["turn"],
            "span_name": row["span_name"],
            "source": row["source"],
            "started_ms": row["started_ms"],
            "ended_ms": row["ended_ms"],
            "duration_ms": row["duration_ms"],
            "parent_span_id": row["parent_span_id"],
            "metadata": json.loads(row["metadata_json"]),
        }
        for row in rows
    ]


def _required_text(value) -> str:
    text = str(value).strip() if value is not None else ""

    return text or "unspecified"


def store_report(report: dict) -> None:
    """Persist a validated report and its claim/evidence ledger."""

    dimensions = report.get("dimensions") or {}
    summary = report.get("summary") or {}
    session_id = str(report["session_id"])
    report_id = str(report["report_id"])
    tenant = normalize_tenant_id(
        report.get("tenant_id") or tenant_for_session(session_id)
    )
    tags = dimensions.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    tag_index = "|" + "|".join(
        _required_text(tag) for tag in tags
    ) + "|"

    with _connect() as connection:
        # Replacing by session keeps one current report per interview.
        connection.execute(
            """
            DELETE FROM analysis_reports
            WHERE session_id = ? AND tenant_id = ?
            """,
            (session_id, tenant),
        )
        connection.execute(
            """
            DELETE FROM response_evidence
            WHERE session_id = ? AND tenant_id = ?
            """,
            (session_id, tenant),
        )
        connection.execute(
            """
            DELETE FROM response_outcomes
            WHERE session_id = ? AND tenant_id = ?
            """,
            (session_id, tenant),
        )

        connection.execute(
            """
            INSERT INTO analysis_reports (
                report_id,
                session_id,
                tenant_id,
                generated_at,
                period_start,
                period_end,
                customer_segment,
                product_category,
                research_goal,
                status_label,
                response_pattern_label,
                tags,
                report_json,
                created_by,
                updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                session_id,
                tenant,
                str(report.get("generated_at") or _utc_now()),
                dimensions.get("period_start"),
                dimensions.get("period_end"),
                _required_text(dimensions.get("customer_segment")),
                _required_text(dimensions.get("product_category")),
                dimensions.get("research_goal"),
                _required_text(dimensions.get("status_label")),
                _required_text(dimensions.get("response_pattern_label")),
                tag_index,
                _json(report),
                str(report.get("created_by") or ""),
                str(report.get("updated_by") or ""),
            ),
        )

        for record in report.get("response_evidence", []):
            connection.execute(
                """
                INSERT INTO response_evidence (
                    response_id,
                    session_id,
                    tenant_id,
                    turn,
                    question,
                    text,
                    captured_at,
                    participant_id,
                    customer_segment,
                    product_category,
                    period_start,
                    period_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.get("response_id")),
                    session_id,
                    tenant,
                    int(record.get("turn") or 0),
                    record.get("question"),
                    str(record.get("text") or ""),
                    record.get("captured_at"),
                    _required_text(record.get("participant_id")),
                    _required_text(dimensions.get("customer_segment")),
                    _required_text(dimensions.get("product_category")),
                    dimensions.get("period_start"),
                    dimensions.get("period_end"),
                ),
            )

        for outcome in report.get("response_outcomes", []):
            connection.execute(
                """
                INSERT INTO response_outcomes (
                    outcome_id,
                    session_id,
                    tenant_id,
                    sequence,
                    response_id,
                    status,
                    question,
                    transcript,
                    captured_at,
                    participant_id,
                    classification_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(outcome.get("outcome_id")),
                    session_id,
                    tenant,
                    int(outcome.get("sequence") or 0),
                    str(outcome.get("response_id")),
                    str(outcome.get("status")),
                    outcome.get("question"),
                    outcome.get("transcript"),
                    outcome.get("captured_at"),
                    _required_text(outcome.get("participant_id")),
                    str(outcome.get("classification_source")),
                ),
            )

        for claim in report.get("claims", []):
            claim_id = str(claim["claim_id"])

            connection.execute(
                """
                INSERT INTO report_claims (
                    claim_id,
                    report_id,
                    session_id,
                    tenant_id,
                    category,
                    parameter,
                    statement,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    report_id,
                    session_id,
                    tenant,
                    _required_text(claim.get("category")),
                    _required_text(claim.get("parameter")),
                    str(claim.get("statement") or ""),
                    _json({
                        key: value
                        for key, value in claim.items()
                        if key not in {"evidence"}
                    }),
                ),
            )

            for reference in claim.get("evidence", []):
                connection.execute(
                    """
                    INSERT INTO claim_evidence (
                        claim_id,
                        report_id,
                        tenant_id,
                        evidence_id,
                        response_id,
                        source_quote,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim_id,
                        report_id,
                        tenant,
                        str(reference.get("evidence_id")),
                        str(reference.get("response_id")),
                        str(reference.get("source_quote") or ""),
                        _json(reference),
                    ),
                )


def get_report(
    report_id: str,
    tenant_id: str | None = None,
) -> dict | None:
    tenant = normalize_tenant_id(tenant_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT report_json
            FROM analysis_reports
            WHERE report_id = ? AND tenant_id = ?
            """,
            (report_id, tenant),
        ).fetchone()

    if row is None:
        return None

    return json.loads(row["report_json"])


def list_reports(
    period_start: str | None = None,
    period_end: str | None = None,
    customer_segment: str | None = None,
    product_category: str | None = None,
    research_goal: str | None = None,
    session_id: str | None = None,
    status_label: str | None = None,
    response_pattern_label: str | None = None,
    tag: str | None = None,
    tenant_id: str | None = None,
) -> list[dict]:
    """List validated reports matching normalized reporting dimensions."""

    tenant = normalize_tenant_id(tenant_id)
    clauses = ["tenant_id = ?"]
    parameters: list[Any] = [tenant]

    if period_start:
        clauses.append(
            "(period_end IS NULL OR period_end >= ?)"
        )
        parameters.append(period_start)

    if period_end:
        clauses.append("period_start <= ?")
        parameters.append(period_end)

    for column, value in (
        ("customer_segment", customer_segment),
        ("product_category", product_category),
        ("research_goal", research_goal),
        ("session_id", session_id),
        ("status_label", status_label),
        ("response_pattern_label", response_pattern_label),
    ):
        if value:
            clauses.append(f"{column} = ?")
            parameters.append(value)

    if tag:
        clauses.append("tags LIKE ?")
        parameters.append(f"%|{tag}|%")

    where = (
        " WHERE " + " AND ".join(clauses)
        if clauses
        else ""
    )

    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT report_json
            FROM analysis_reports
            {where}
            ORDER BY period_start DESC, generated_at DESC, report_id DESC
            """,
            parameters,
        ).fetchall()

    return [json.loads(row["report_json"]) for row in rows]


def record_audit(
    action: str,
    actor: str = "",
    resource_type: str = "",
    resource_id: str = "",
    tenant_id: str | None = None,
    request_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Append an immutable audit entry for an application action."""

    tenant = normalize_tenant_id(tenant_id)
    now = _utc_now()

    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO audit_log (
                tenant_id,
                actor,
                action,
                resource_type,
                resource_id,
                request_id,
                created_at,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant,
                actor,
                action,
                resource_type,
                resource_id,
                request_id if request_id is not None else audit_request_id(),
                now,
                _json(metadata or {}),
            ),
        )
        audit_id = cursor.lastrowid

    return {"audit_id": audit_id, "action": action, "created_at": now}


def list_audit(
    tenant_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    tenant = normalize_tenant_id(tenant_id)
    clauses = ["tenant_id = ?"]
    parameters: list[Any] = [tenant]

    for column, value in (
        ("action", action),
        ("resource_type", resource_type),
        ("resource_id", resource_id),
    ):
        if value:
            clauses.append(f"{column} = ?")
            parameters.append(value)

    parameters.append(int(limit))

    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
                audit_id,
                tenant_id,
                actor,
                action,
                resource_type,
                resource_id,
                request_id,
                created_at,
                metadata_json
            FROM audit_log
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC, audit_id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

    return [
        {
            "audit_id": row["audit_id"],
            "tenant_id": row["tenant_id"],
            "actor": row["actor"],
            "action": row["action"],
            "resource_type": row["resource_type"],
            "resource_id": row["resource_id"],
            "request_id": row["request_id"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata_json"]),
        }
        for row in rows
    ]


class SQLiteResearchMemoryRepository:
    """Default local/dev repository implementation."""

    def configure(
        self,
        database_path: Any = None,
        *,
        ensure_schema: bool = True,
    ) -> Path:
        return configure(database_path, ensure_schema=ensure_schema)

    def database_path(self) -> Path:
        return database_path()

    def health_check(self) -> tuple[str, str | None]:
        return health_check()

    def list_research_memory_records(
        self,
        tenant_id: str | None = None,
        session_id: str | None = None,
        record_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        return list_research_memory_records(
            tenant_id,
            session_id,
            record_type,
            limit,
        )

    def save_checkpoint(self, **kwargs: Any) -> None:
        return save_checkpoint(**kwargs)

    def get_checkpoint(self, **kwargs: Any) -> dict | None:
        return get_checkpoint(**kwargs)

    def list_checkpoints(self, **kwargs: Any) -> list[dict]:
        return list_checkpoints(**kwargs)

    def save_checkpoint_blobs(self, **kwargs: Any) -> None:
        return save_checkpoint_blobs(**kwargs)

    def get_checkpoint_blobs(self, **kwargs: Any) -> list[dict]:
        return get_checkpoint_blobs(**kwargs)

    def save_checkpoint_writes(self, **kwargs: Any) -> None:
        return save_checkpoint_writes(**kwargs)

    def get_checkpoint_writes(self, **kwargs: Any) -> list[dict]:
        return get_checkpoint_writes(**kwargs)

    def delete_checkpoint_thread(self, **kwargs: Any) -> None:
        return delete_checkpoint_thread(**kwargs)

    def apply_migrations(self, database_path: Any = None) -> list[str]:
        return apply_migrations(database_path)

    def ensure_tenant_audit_schema(self, connection: Any) -> None:
        return ensure_tenant_audit_schema(connection)

    def __getattr__(self, name: str):
        return globals()[name]


_repository: SQLiteResearchMemoryRepository | None = None


def get_repository() -> SQLiteResearchMemoryRepository:
    global _repository
    if _repository is None:
        _repository = SQLiteResearchMemoryRepository()
    return _repository
