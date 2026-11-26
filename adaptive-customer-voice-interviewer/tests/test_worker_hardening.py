import json
from datetime import datetime, timedelta, timezone

import pytest

from agent.jobs import service as worker_service
from agent.persistence import sqlite as sqlite_repository


TENANT_A = "tenant-a"
SESSION_A = "session-a"


@pytest.fixture(autouse=True)
def clean_handlers():
    worker_service.clear_handlers()
    yield
    worker_service.clear_handlers()


@pytest.fixture
def sqlite_backend(tmp_path, monkeypatch):
    database = tmp_path / "hardened-worker.sqlite3"
    monkeypatch.setattr(sqlite_repository, "_database_path", database)
    sqlite_repository.configure(database)
    return sqlite_repository


def enqueue(job_id="job-1", **overrides):
    payload = {
        "job_id": job_id,
        "job_type": "test_job",
        "session_id": SESSION_A,
        "tenant_id": TENANT_A,
        "payload": {"session_id": SESSION_A, "value": 1},
    }
    payload.update(overrides)
    return sqlite_repository.enqueue_job(**payload)


def test_claim_returns_fencing_token_and_completion_requires_it(sqlite_backend):
    enqueue()

    claimed = sqlite_backend.claim_jobs(worker_id="worker-1")[0]
    token = claimed["fencing_token"]

    assert token

    wrong = sqlite_backend.complete_job(
        job_id="job-1",
        result={"status": "wrong"},
        tenant_id=TENANT_A,
        fencing_token="not-the-token",
    )
    assert wrong["status"] == "processing"

    completed = sqlite_backend.complete_job(
        job_id="job-1",
        result={"status": "done"},
        tenant_id=TENANT_A,
        fencing_token=token,
    )
    assert completed["status"] == "completed"
    assert completed["result"] == {"status": "done"}


def test_failure_requires_current_owner(sqlite_backend):
    enqueue(max_attempts=1)

    claimed = sqlite_backend.claim_jobs(worker_id="worker-1")[0]
    token = claimed["fencing_token"]

    wrong = sqlite_backend.fail_job(
        job_id="job-1",
        error="rogue",
        tenant_id=TENANT_A,
        fencing_token="not-the-token",
    )
    assert wrong["status"] == "processing"

    failed = sqlite_backend.fail_job(
        job_id="job-1",
        error="owner",
        tenant_id=TENANT_A,
        fencing_token=token,
    )
    assert failed["status"] == "failed"
    assert failed["error"] == "owner"


def test_queued_job_cannot_be_completed(sqlite_backend):
    enqueue()

    result = sqlite_backend.complete_job(
        job_id="job-1",
        result={},
        tenant_id=TENANT_A,
        fencing_token="unused",
    )

    assert result["status"] == "queued"


def test_heartbeat_requires_current_owner(sqlite_backend):
    enqueue()

    claimed = sqlite_backend.claim_jobs(worker_id="worker-1")[0]
    token = claimed["fencing_token"]

    assert sqlite_backend.heartbeat_job(
        job_id="job-1",
        fencing_token="not-the-token",
        tenant_id=TENANT_A,
    ) is None

    refreshed = sqlite_backend.heartbeat_job(
        job_id="job-1",
        fencing_token=token,
        tenant_id=TENANT_A,
    )
    assert refreshed is not None
    assert refreshed["heartbeat_at"]


def test_worker_identity_is_unique_with_a_shared_label():
    first = worker_service.worker_identity("shared-worker")
    second = worker_service.worker_identity("shared-worker")

    assert first.startswith("shared-worker-")
    assert first != second


def test_worker_rejects_payload_missing_identity(sqlite_backend):
    handled = []
    worker_service.register_handler(
        "test_job",
        lambda payload: handled.append(payload) or {"status": "done"},
    )

    enqueue(payload={"value": 1}, max_attempts=1)

    # Simulate a corrupted payload row rather than trusting the enqueue call.
    with sqlite_repository._connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM background_jobs WHERE job_id = ?",
            ("job-1",),
        ).fetchone()
        stored = json.loads(row["payload_json"])
        for field in ("job_id", "job_type", "session_id", "tenant_id"):
            stored.pop(field, None)
        connection.execute(
            "UPDATE background_jobs SET payload_json = ? WHERE job_id = ?",
            (json.dumps(stored), "job-1"),
        )

    worker = worker_service.DurableWorker(
        repository=sqlite_backend,
        worker_id="worker-1",
        job_types=["test_job"],
    )

    assert worker.run_once() == 1

    job = sqlite_backend.get_job(job_id="job-1", tenant_id=TENANT_A)
    assert handled == []
    assert job["status"] == "failed"
    assert "JobPayloadError" in job["error"]
    assert "missing" in job["error"].lower()


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("job_id", "wrong-job"),
        ("job_type", "wrong-type"),
        ("session_id", "other-session"),
        ("tenant_id", "other-tenant"),
    ],
)
def test_worker_rejects_mismatched_payload_identity(
    sqlite_backend,
    field,
    bad_value,
):
    handled = []
    worker_service.register_handler(
        "test_job",
        lambda payload: handled.append(payload) or {"status": "done"},
    )

    enqueue(payload={"value": 1}, max_attempts=1)

    # Simulate a corrupted payload row rather than trusting the enqueue call.
    with sqlite_repository._connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM background_jobs WHERE job_id = ?",
            ("job-1",),
        ).fetchone()
        stored = json.loads(row["payload_json"])
        stored[field] = bad_value
        connection.execute(
            "UPDATE background_jobs SET payload_json = ? WHERE job_id = ?",
            (json.dumps(stored), "job-1"),
        )

    worker = worker_service.DurableWorker(
        repository=sqlite_backend,
        worker_id="worker-1",
        job_types=["test_job"],
    )

    assert worker.run_once() == 1

    job = sqlite_backend.get_job(job_id="job-1", tenant_id=TENANT_A)
    assert handled == []
    assert job["status"] == "failed"
    assert "JobPayloadError" in job["error"]
    assert field in job["error"]


def test_heartbeat_keeps_a_claimed_job_out_of_stale_recovery(sqlite_backend):
    enqueue("job-1")
    enqueue("job-2")
    sqlite_backend.claim_jobs(worker_id="worker-1", limit=2)

    now = datetime.now(timezone.utc)
    old_update = (now - timedelta(seconds=120)).isoformat(
        timespec="milliseconds"
    )
    stale_before = (now - timedelta(seconds=60)).isoformat(
        timespec="milliseconds"
    )

    with sqlite_repository._connect() as connection:
        connection.execute(
            """
            UPDATE background_jobs
            SET updated_at = ?, heartbeat_at = NULL
            WHERE job_id IN ('job-1', 'job-2')
            """,
            (old_update,),
        )

    token = sqlite_backend.get_job(
        job_id="job-2",
        tenant_id=TENANT_A,
    )["fencing_token"]

    assert sqlite_backend.heartbeat_job(
        job_id="job-2",
        fencing_token=token,
        tenant_id=TENANT_A,
    )

    assert sqlite_backend.recover_stale_jobs(
        stale_before=stale_before,
        tenant_id=TENANT_A,
    ) == 1

    assert sqlite_backend.get_job(
        job_id="job-1",
        tenant_id=TENANT_A,
    )["status"] == "queued"
    assert sqlite_backend.get_job(
        job_id="job-2",
        tenant_id=TENANT_A,
    )["status"] == "processing"


def test_two_workers_with_the_same_label_have_distinct_claim_tokens():
    first = worker_service.DurableWorker(repository=None, worker_id="shared")
    second = worker_service.DurableWorker(repository=None, worker_id="shared")

    assert first.worker_id == second.worker_id == "shared"
    assert first.worker_instance_id != second.worker_instance_id


def test_worker_environment_reads_heartbeat_interval(monkeypatch):
    monkeypatch.setenv("CVI_WORKER_HEARTBEAT_SECONDS", "7")

    worker = worker_service.worker_from_environment(repository=None)

    assert worker.heartbeat_interval_seconds == 7.0
