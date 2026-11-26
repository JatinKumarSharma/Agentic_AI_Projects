"""Durable worker queue semantics.

The API only enqueues jobs. A separate worker process claims them through the
configured repository and dispatches them to registered handlers. The worker
never imports the FastAPI app; the process entry point registers handlers.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from agent.observability import configure_logging, get_logger

from .errors import JobPayloadError, UnregisteredJobError

configure_logging()
logger = get_logger(__name__)

PROCESS_RESPONSE = "process_response"
CONTEXTUAL_ENGAGEMENT = "contextual_engagement"

JOB_TYPES = (PROCESS_RESPONSE, CONTEXTUAL_ENGAGEMENT)

Handler = Callable[[dict], dict | None]

_handlers: dict[str, Handler] = {}


def register_handler(job_type: str, handler: Handler) -> None:
    """Register the in-process function that executes one durable job type."""

    _handlers[job_type] = handler


def clear_handlers() -> None:
    """Reset the registry. Used by tests and process reconfiguration."""

    _handlers.clear()


def worker_identity(label: str | None = None) -> str:
    """Return a per-process owner identity for a configured worker label."""

    prefix = (label or "worker").strip() or "worker"
    return f"{prefix}-{uuid4().hex[:12]}"


def validate_job_payload(job: dict) -> dict:
    """Reject payloads that disagree with their durable row identity."""

    payload = job.get("payload")

    if not isinstance(payload, dict):
        raise JobPayloadError("Job payload must be a JSON object.")

    for field in ("job_id", "job_type", "session_id", "tenant_id"):
        row_value = job.get(field)
        payload_value = payload.get(field)

        if payload_value is None:
            raise JobPayloadError(
                f"Payload {field} is missing from the claimed job."
            )

        if row_value is None or payload_value != row_value:
            raise JobPayloadError(
                f"Payload {field} does not match the claimed job."
            )

    return payload


def dispatch_job(job: dict) -> dict | None:
    """Execute one claimed job with its registered handler."""

    job_type = job["job_type"]
    handler = _handlers.get(job_type)

    if handler is None:
        raise UnregisteredJobError(f"No handler registered for {job_type}.")

    return handler(validate_job_payload(job))


def new_job_id(job_type: str) -> str:
    return f"job_{job_type}_{uuid4().hex}"


class DurableWorker:
    """Claim, dispatch, and acknowledge durable jobs from a worker process."""

    def __init__(
        self,
        *,
        repository: Any,
        worker_id: str | None = None,
        job_types: list[str] | None = None,
        batch_size: int = 4,
        poll_interval_seconds: float = 0.25,
        retry_delay_seconds: float = 5,
        stale_timeout_seconds: float = 300,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self.repository = repository
        # The configured ID is a readable label, not the ownership identity.
        self.worker_id = worker_id or f"worker-{uuid4().hex[:12]}"
        self.worker_instance_id = worker_identity(self.worker_id)
        self.job_types = list(job_types or JOB_TYPES)
        self.batch_size = max(1, batch_size)
        self.poll_interval_seconds = max(0.05, poll_interval_seconds)
        self.retry_delay_seconds = max(0, retry_delay_seconds)
        self.stale_timeout_seconds = max(30, stale_timeout_seconds)
        interval = heartbeat_interval_seconds
        if interval is None:
            interval = min(30.0, self.stale_timeout_seconds / 2)
        self.heartbeat_interval_seconds = max(1.0, interval)

    @contextmanager
    def _keep_alive(self, job: dict, fencing_token: str):
        """Refresh a long-running job's heartbeat until dispatch returns."""

        stop = Event()

        def pulse() -> None:
            while not stop.wait(self.heartbeat_interval_seconds):
                try:
                    self.repository.heartbeat_job(
                        job_id=job["job_id"],
                        fencing_token=fencing_token,
                        tenant_id=job.get("tenant_id"),
                    )
                except Exception:
                    logger.exception(
                        "worker_heartbeat_failed",
                        extra={"job_id": job["job_id"]},
                    )

        thread = Thread(
            target=pulse,
            name=f"cvi-heartbeat-{fencing_token[:8]}",
            daemon=True,
        )
        thread.start()

        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=0.1)

    def _stale_before(self) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self.stale_timeout_seconds
        )
        return cutoff.isoformat(timespec="milliseconds")

    def run_once(self) -> int:
        """Process at most one batch of due jobs.

        Returns the number of claimed jobs, including failed ones, so the loop
        can tell whether it should poll again immediately.
        """

        try:
            self.repository.recover_stale_jobs(
                stale_before=self._stale_before(),
            )
        except Exception:
            logger.exception("worker_stale_recovery_failed")

        jobs = self.repository.claim_jobs(
            worker_id=self.worker_id,
            job_types=self.job_types,
            limit=self.batch_size,
            fencing_token=self.worker_instance_id,
        )

        for job in jobs:
            job_id = job["job_id"]
            tenant_id = job.get("tenant_id")
            fencing_token = job.get("fencing_token")

            if not fencing_token:
                logger.error(
                    "worker_job_missing_fencing_token",
                    extra={"job_id": job_id},
                )
                continue

            if not self.repository.heartbeat_job(
                job_id=job_id,
                fencing_token=fencing_token,
                tenant_id=tenant_id,
            ):
                logger.warning(
                    "worker_job_lost_before_dispatch",
                    extra={"job_id": job_id},
                )
                continue

            try:
                with self._keep_alive(job, fencing_token):
                    result = dispatch_job(job) or {}

                completed = self.repository.complete_job(
                    job_id=job_id,
                    result=result,
                    tenant_id=tenant_id,
                    fencing_token=fencing_token,
                )

                if not completed or completed.get("status") != "completed":
                    logger.warning(
                        "worker_job_completion_rejected",
                        extra={"job_id": job_id},
                    )
                    continue

                logger.info(
                    "worker_job_completed",
                    extra={"job_id": job_id, "job_type": job["job_type"]},
                )
            except Exception as exc:
                logger.exception(
                    "worker_job_failed",
                    extra={"job_id": job_id, "job_type": job["job_type"]},
                )
                failed = self.repository.fail_job(
                    job_id=job_id,
                    error=f"{type(exc).__name__}: {exc}",
                    tenant_id=tenant_id,
                    retry_delay_seconds=self.retry_delay_seconds,
                    fencing_token=fencing_token,
                )

                if not failed or failed.get("status") not in {
                    "queued",
                    "failed",
                }:
                    logger.warning(
                        "worker_job_failure_rejected",
                        extra={"job_id": job_id},
                    )

        return len(jobs)

    def run_forever(self, stop_event: Any | None = None) -> None:
        """Poll the durable queue until a stop event is set."""

        logger.info(
            "worker_started",
            extra={
                "worker_id": self.worker_id,
                "job_types": self.job_types,
                "poll_interval_seconds": self.poll_interval_seconds,
            },
        )

        while stop_event is None or not stop_event.is_set():
            processed = self.run_once()

            if not processed:
                if stop_event is None:
                    time.sleep(self.poll_interval_seconds)
                else:
                    stop_event.wait(self.poll_interval_seconds)


def worker_from_environment(repository: Any) -> DurableWorker:
    """Build a worker using hosted deployment environment variables."""

    def _float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except ValueError:
            return default

    def _int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            return default

    def _optional_float(name: str) -> float | None:
        raw = os.getenv(name)

        if raw is None:
            return None

        try:
            return float(raw)
        except ValueError:
            return None

    return DurableWorker(
        repository=repository,
        worker_id=os.getenv("CVI_WORKER_ID"),
        batch_size=_int("CVI_WORKER_BATCH_SIZE", 4),
        poll_interval_seconds=_float("CVI_WORKER_POLL_INTERVAL_SECONDS", 0.25),
        retry_delay_seconds=_float("CVI_WORKER_RETRY_DELAY_SECONDS", 5),
        stale_timeout_seconds=_float("CVI_WORKER_STALE_TIMEOUT_SECONDS", 300),
        heartbeat_interval_seconds=_optional_float(
            "CVI_WORKER_HEARTBEAT_SECONDS"
        ),
    )
