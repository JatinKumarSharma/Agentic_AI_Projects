import api
import pytest
import scripts.worker as worker_entrypoint

from agent.jobs import service as worker_service
from agent.persistence import sqlite as sqlite_repository


@pytest.fixture(autouse=True)
def clean_registry():
    worker_service.clear_handlers()
    yield
    worker_service.clear_handlers()


@pytest.fixture
def sqlite_backend(tmp_path, monkeypatch):
    database = tmp_path / "worker.sqlite3"
    monkeypatch.setattr(sqlite_repository, "_database_path", database)
    sqlite_repository.configure(database)
    return sqlite_repository


def test_worker_dispatches_claim_and_completes_job(sqlite_backend):
    seen = {}

    def handler(payload):
        seen.update(payload)
        return {"status": "done"}

    worker_service.register_handler("test_job", handler)

    sqlite_backend.enqueue_job(
        job_id="job-1",
        job_type="test_job",
        session_id="session-1",
        payload={"value": 42},
        tenant_id="tenant-a",
    )

    worker = worker_service.DurableWorker(
        repository=sqlite_backend,
        worker_id="worker-1",
        job_types=["test_job"],
    )

    assert worker.run_once() == 1
    assert seen == {
        "value": 42,
        "job_id": "job-1",
        "job_type": "test_job",
        "session_id": "session-1",
        "tenant_id": "tenant-a",
    }

    job = sqlite_backend.get_job(job_id="job-1", tenant_id="tenant-a")
    assert job["status"] == "completed"
    assert job["result"] == {"status": "done"}


def test_worker_retries_then_fails_job(sqlite_backend):
    attempts = []

    def failing_handler(payload):
        attempts.append(len(attempts) + 1)
        raise RuntimeError("boom")

    worker_service.register_handler("failing_job", failing_handler)

    sqlite_backend.enqueue_job(
        job_id="job-1",
        job_type="failing_job",
        session_id="session-1",
        payload={"attempt": 1},
        tenant_id="tenant-a",
        max_attempts=2,
    )

    worker = worker_service.DurableWorker(
        repository=sqlite_backend,
        worker_id="worker-1",
        job_types=["failing_job"],
        retry_delay_seconds=0,
    )

    assert worker.run_once() == 1
    assert worker.run_once() == 1

    job = sqlite_backend.get_job(job_id="job-1", tenant_id="tenant-a")

    assert job["status"] == "failed"
    assert "RuntimeError" in job["error"]
    assert attempts == [1, 2]


def test_worker_only_claims_registered_job_types(sqlite_backend):
    handled = []
    worker_service.register_handler("wanted", lambda payload: handled.append(payload))

    for job_type in ("wanted", "ignored"):
        sqlite_backend.enqueue_job(
            job_id=f"job-{job_type}",
            job_type=job_type,
            session_id="session-1",
            payload={},
            tenant_id="tenant-a",
        )

    worker = worker_service.DurableWorker(
        repository=sqlite_backend,
        worker_id="worker-1",
        job_types=["wanted"],
    )

    assert worker.run_once() == 1
    assert sqlite_backend.get_job(
        job_id="job-ignored",
        tenant_id="tenant-a",
    )["status"] == "queued"


def test_missing_handler_fails_job(sqlite_backend):
    sqlite_backend.enqueue_job(
        job_id="job-1",
        job_type="unhandled",
        session_id="session-1",
        payload={},
        tenant_id="tenant-a",
        max_attempts=1,
    )

    worker = worker_service.DurableWorker(
        repository=sqlite_backend,
        worker_id="worker-1",
        job_types=["unhandled"],
    )

    worker.run_once()

    job = sqlite_backend.get_job(job_id="job-1", tenant_id="tenant-a")
    assert job["status"] == "failed"
    assert "UnregisteredJobError" in job["error"]


def test_registered_handlers_process_response_job(sqlite_backend, monkeypatch):
    session_id = "response-session"

    class FakeGraph:
        def stream(self, *args, **kwargs):
            yield "values", {"interview_complete": True}

    monkeypatch.setattr(api, "graph", FakeGraph())
    monkeypatch.setattr(api, "repository", sqlite_backend)
    monkeypatch.setattr(api, "WORKER_ENABLED", True)
    monkeypatch.setattr(api, "sessions", {session_id: {"session_id": session_id}})
    monkeypatch.setattr(api, "response_jobs", {})
    monkeypatch.setattr(api, "session_locks", {})

    sqlite_backend.save_pending_action(
        session_id=session_id,
        payload={"type": "await_consumer_response"},
    )
    sqlite_backend.enqueue_job(
        job_id="job-response",
        job_type=worker_service.PROCESS_RESPONSE,
        session_id=session_id,
        payload={"session_id": session_id, "transcript": "It works."},
    )

    worker_entrypoint.register_handlers()

    worker = worker_service.DurableWorker(
        repository=sqlite_backend,
        worker_id="worker-1",
        job_types=[worker_service.PROCESS_RESPONSE],
    )

    assert worker.run_once() == 1

    job = sqlite_backend.get_job(job_id="job-response")
    assert job["status"] == "completed"
    assert job["result"]["status"] == "completed"
    assert sqlite_backend.get_pending_action(session_id=session_id) is None
