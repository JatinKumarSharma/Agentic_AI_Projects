"""Declarative security invariants checked after migrations.

Schema creation is idempotent, so a missing or altered constraint can otherwise
be recorded as a successful migration. These declarations describe the
constraints that protect tenant isolation, worker deduplication, and durable
checkpoints. Both repository adapters verify them after applying migrations.
"""

from __future__ import annotations


class MigrationInvariantError(RuntimeError):
    """Raised when a migrated schema no longer satisfies a security invariant."""


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
    "research_memory_records",
    "langgraph_checkpoints",
    "langgraph_checkpoint_blobs",
    "langgraph_checkpoint_writes",
    "audit_log",
    "background_jobs",
    "voice_output_events",
    "session_pending_actions",
    "session_leases",
)

REQUIRED_TABLES = (
    "schema_migrations",
    *TENANT_SCOPED_TABLES,
)

REQUIRED_COLUMNS = {
    "schema_migrations": ("version", "applied_at"),
    "sessions": ("session_id", "payload_json", "created_by", "updated_by"),
    "session_events": ("event_id", "actor", "action", "resource", "request_id"),
    "audit_log": ("audit_id", "actor", "action", "resource_id", "request_id"),
    "background_jobs": (
        "job_id",
        "job_type",
        "dedupe_key",
        "status",
        "payload_json",
        "worker_id",
        "fencing_token",
        "heartbeat_at",
    ),
    "voice_output_events": ("event_id", "payload_json", "delivered_at"),
    "session_pending_actions": ("session_id", "payload_json"),
    "session_leases": ("session_id", "owner_id", "expires_at"),
}

REQUIRED_PRIMARY_KEYS = {
    "schema_migrations": ("version",),
    "sessions": ("session_id",),
    "claim_evidence": ("claim_id", "evidence_id"),
    "langgraph_checkpoints": (
        "tenant_id",
        "thread_id",
        "checkpoint_ns",
        "checkpoint_id",
    ),
    "langgraph_checkpoint_blobs": (
        "tenant_id",
        "thread_id",
        "checkpoint_ns",
        "channel",
        "version",
    ),
    "langgraph_checkpoint_writes": (
        "tenant_id",
        "thread_id",
        "checkpoint_ns",
        "checkpoint_id",
        "task_id",
        "idx",
    ),
    "background_jobs": ("job_id",),
    "voice_output_events": ("event_id",),
    "session_pending_actions": ("tenant_id", "session_id"),
    "session_leases": ("tenant_id", "session_id"),
}

REQUIRED_INDEXES = {
    "idx_background_jobs_active_unique": {
        "table": "background_jobs",
        "columns": ("tenant_id", "session_id", "dedupe_key"),
        "unique": True,
        "partial": True,
    },
    "idx_audit_log_tenant_action": {
        "table": "audit_log",
        "columns": ("tenant_id", "action", "created_at"),
        "unique": False,
        "partial": False,
    },
    "idx_langgraph_checkpoints_thread": {
        "table": "langgraph_checkpoints",
        "columns": ("tenant_id", "thread_id", "checkpoint_ns", "created_at"),
        "unique": False,
        "partial": False,
    },
    "idx_voice_output_events_drain": {
        "table": "voice_output_events",
        "columns": (
            "tenant_id",
            "session_id",
            "delivered_at",
            "created_at",
            "event_id",
        ),
        "unique": False,
        "partial": False,
    },
}


def format_issues(issues: list[str]) -> str:
    """Return a stable, operator-readable verification failure."""

    return "Migration invariant verification failed: " + "; ".join(sorted(issues))
