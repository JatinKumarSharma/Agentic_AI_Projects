"""Repository interface for authoritative research memory.

This boundary keeps storage concerns out of the API and research graph. SQLite is
the default development implementation; Postgres can implement the same contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class ResearchMemoryRepository(Protocol):
    """Authoritative storage contract for CVI research memory."""

    def configure(self, database_url: Any = None, *, ensure_schema: bool = True) -> Any: ...

    def database_path(self) -> str | Path: ...

    def apply_migrations(self) -> list[str]: ...

    def verify_migration_invariants(self) -> list[str]:
        """Return security-schema drift issues; empty means verified."""
        ...

    def health_check(self) -> tuple[str, str | None]:
        """Return ``(status, detail)`` for readiness probes."""
        ...

    def ensure_tenant_audit_schema(self, connection: Any) -> None: ...

    # Sessions
    def upsert_session(self, session: dict) -> None: ...
    def get_session(self, session_id: str, tenant_id: str | None = None) -> dict | None: ...
    def get_session_created_at(self, session_id: str, tenant_id: str | None = None) -> str | None: ...
    def list_sessions(self, tenant_id: str | None = None) -> list[dict]: ...
    def tenant_for_session(self, session_id: str) -> str: ...

    # Authoritative memory retrieval
    def list_research_memory_records(
        self,
        tenant_id: str | None = None,
        session_id: str | None = None,
        record_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict]: ...

    # Durable LangGraph checkpoints
    def save_checkpoint(
        self,
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
    ) -> None: ...
    def get_checkpoint(
        self,
        *,
        tenant_id: str | None,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str | None = None,
    ) -> dict | None: ...
    def list_checkpoints(
        self,
        *,
        tenant_id: str | None,
        thread_id: str,
        checkpoint_ns: str,
        before_checkpoint_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict]: ...
    def save_checkpoint_blobs(
        self,
        *,
        tenant_id: str | None,
        thread_id: str,
        checkpoint_ns: str,
        blobs: list[tuple[str, str, str, bytes]],
    ) -> None: ...
    def get_checkpoint_blobs(
        self,
        *,
        tenant_id: str | None,
        thread_id: str,
        checkpoint_ns: str,
        keys: list[tuple[str, str]],
    ) -> list[dict]: ...
    def save_checkpoint_writes(
        self,
        *,
        tenant_id: str | None,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        writes: list[tuple[str, str, str, int, str, bytes]],
    ) -> None: ...
    def get_checkpoint_writes(
        self,
        *,
        tenant_id: str | None,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> list[dict]: ...
    def delete_checkpoint_thread(
        self,
        *,
        tenant_id: str | None,
        thread_id: str,
        checkpoint_ns: str | None = None,
    ) -> None: ...

    # Snapshots/events
    def record_snapshot(
        self,
        session_id: str,
        state: dict,
        source: str,
        activity: str | None = None,
        tenant_id: str | None = None,
    ) -> dict: ...
    def get_latest_snapshot(self, session_id: str, tenant_id: str | None = None) -> dict | None: ...
    def list_snapshot_metadata(self, session_id: str, tenant_id: str | None = None) -> list[dict]: ...
    def record_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict | None = None,
        actor: str = "",
        action: str = "",
        resource: str = "",
        request_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict: ...
    def list_events(self, session_id: str, tenant_id: str | None = None) -> list[dict]: ...

    # Timings
    def record_timing(
        self,
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
    ) -> dict: ...
    def list_timings(
        self,
        session_id: str,
        span_name: str | None = None,
        source: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict]: ...

    # Reports/audit
    def store_report(self, report: dict) -> None: ...
    def get_report(self, report_id: str, tenant_id: str | None = None) -> dict | None: ...
    def list_reports(
        self,
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
    ) -> list[dict]: ...
    def record_audit(
        self,
        action: str,
        actor: str = "",
        resource_type: str = "",
        resource_id: str = "",
        tenant_id: str | None = None,
        request_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict: ...
    def list_audit(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]: ...

    # Durable background jobs
    def enqueue_job(
        self,
        *,
        job_id: str,
        job_type: str,
        session_id: str,
        payload: dict,
        tenant_id: str | None = None,
        max_attempts: int = 3,
        unique_active: bool = False,
        run_after: str | None = None,
    ) -> dict: ...
    def claim_jobs(
        self,
        *,
        worker_id: str,
        job_types: list[str] | None = None,
        tenant_id: str | None = None,
        limit: int = 1,
        fencing_token: str | None = None,
    ) -> list[dict]: ...
    def get_job(self, *, job_id: str, tenant_id: str | None = None) -> dict | None: ...
    def latest_job(
        self,
        *,
        session_id: str,
        job_type: str,
        tenant_id: str | None = None,
    ) -> dict | None: ...
    def has_active_job(
        self,
        *,
        session_id: str,
        job_type: str,
        tenant_id: str | None = None,
    ) -> bool: ...
    def count_pending_jobs(
        self,
        *,
        session_id: str,
        job_type: str,
        tenant_id: str | None = None,
    ) -> int: ...
    def complete_job(
        self,
        *,
        job_id: str,
        result: dict | None,
        tenant_id: str | None = None,
        fencing_token: str | None = None,
    ) -> dict | None: ...
    def fail_job(
        self,
        *,
        job_id: str,
        error: str,
        tenant_id: str | None = None,
        retry_delay_seconds: float = 0,
        fencing_token: str | None = None,
    ) -> dict | None: ...
    def heartbeat_job(
        self,
        *,
        job_id: str,
        fencing_token: str,
        tenant_id: str | None = None,
    ) -> dict | None: ...
    def recover_stale_jobs(
        self,
        *,
        stale_before: str,
        tenant_id: str | None = None,
    ) -> int: ...
    def enqueue_voice_event(
        self,
        *,
        event_id: str,
        session_id: str,
        event: dict,
        tenant_id: str | None = None,
    ) -> None: ...
    def list_voice_events(
        self,
        *,
        session_id: str,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]: ...
    def acknowledge_voice_events(
        self,
        *,
        session_id: str,
        event_ids: list[str],
        tenant_id: str | None = None,
    ) -> int: ...
    def drain_voice_events(
        self,
        *,
        session_id: str,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]: ...
    def save_pending_action(
        self,
        *,
        session_id: str,
        payload: dict,
        tenant_id: str | None = None,
    ) -> None: ...
    def get_pending_action(
        self,
        *,
        session_id: str,
        tenant_id: str | None = None,
    ) -> dict | None: ...
    def clear_pending_action(
        self,
        *,
        session_id: str,
        tenant_id: str | None = None,
    ) -> None: ...
    def acquire_session_lease(
        self,
        *,
        session_id: str,
        owner_id: str,
        ttl_seconds: float = 300,
        tenant_id: str | None = None,
    ) -> bool: ...
    def renew_session_lease(
        self,
        *,
        session_id: str,
        owner_id: str,
        ttl_seconds: float = 300,
        tenant_id: str | None = None,
    ) -> bool: ...
    def release_session_lease(
        self,
        *,
        session_id: str,
        owner_id: str,
        tenant_id: str | None = None,
    ) -> None: ...
    def get_session_lease(
        self,
        *,
        session_id: str,
        tenant_id: str | None = None,
    ) -> dict | None: ...
