"""Run the durable CVI background worker.

The API enqueues response-processing and contextual-engagement jobs. This
process claims them from the configured repository (SQLite for tests,
PostgreSQL for hosted production), dispatches them to handlers, and records
completion or failure back in the same durable store.
"""

from __future__ import annotations

import signal
import threading

import api
from agent.jobs import service as job_service
from agent.persistence import (
    POSTGRES_BACKEND,
    configure_repository,
    load_persistence_config,
)


def register_handlers() -> None:
    """Bind durable job types to the existing interview functions."""

    def handle_process_response(payload: dict) -> dict:
        session_id = payload["session_id"]
        result = api.process_response_in_background(session_id, payload)
        return result or {"status": "failed", "error": "Unknown processing result."}

    def handle_contextual_engagement(payload: dict) -> dict:
        api.process_contextual_engagement(
            session_id=payload["session_id"],
            interview_id=payload.get("interview_id") or payload["session_id"],
            decomposition=payload["decomposition"],
            turn=int(payload.get("turn") or 0),
            queued_at_ms=payload.get("queued_at_ms"),
        )
        return {"status": "completed"}

    job_service.register_handler(
        job_service.PROCESS_RESPONSE,
        handle_process_response,
    )
    job_service.register_handler(
        job_service.CONTEXTUAL_ENGAGEMENT,
        handle_contextual_engagement,
    )


def main() -> None:
    """Run until SIGINT/SIGTERM or KeyboardInterrupt."""

    # Workers always use the durable boundaries; in-process executors are a
    # test-only compatibility mode inside the API.
    api.WORKER_ENABLED = True
    register_handlers()

    config = load_persistence_config()
    repository = configure_repository(
        config,
        ensure_schema=config.backend != POSTGRES_BACKEND,
    )
    worker = job_service.worker_from_environment(repository)

    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        signal_number = getattr(signal, signal_name, None)
        if signal_number is not None:
            signal.signal(signal_number, request_stop)

    try:
        worker.run_forever(stop_event)
    except KeyboardInterrupt:
        stop_event.set()


if __name__ == "__main__":
    main()
