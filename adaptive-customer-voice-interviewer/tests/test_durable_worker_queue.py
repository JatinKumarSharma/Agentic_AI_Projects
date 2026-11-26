import pytest

from agent.jobs.errors import DuplicateJobError
from agent.persistence import postgres as postgres_repository
from agent.persistence import sqlite as sqlite_repository


TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
SESSION_A = "session-a"


@pytest.fixture
def sqlite_backend(tmp_path, monkeypatch):
    database = tmp_path / "jobs.sqlite3"
    monkeypatch.setattr(sqlite_repository, "_database_path", database)
    sqlite_repository.configure(database)
    return sqlite_repository


def enqueue(job_id, job_type="process_response", **overrides):
    payload = {
        "job_id": job_id,
        "job_type": job_type,
        "session_id": SESSION_A,
        "payload": {"turn": 1},
        "tenant_id": TENANT_A,
    }
    payload.update(overrides)
    return sqlite_repository.enqueue_job(**payload)


def test_enqueue_claim_and_complete_job(sqlite_backend):
    enqueue("job-1", max_attempts=2)

    claimed = sqlite_backend.claim_jobs(worker_id="worker-1")

    assert len(claimed) == 1
    assert claimed[0]["job_id"] == "job-1"
    assert claimed[0]["status"] == "processing"
    assert claimed[0]["attempts"] == 1
    assert claimed[0]["worker_id"] == "worker-1"
    assert claimed[0]["fencing_token"]
    assert claimed[0]["heartbeat_at"]
    assert claimed[0]["payload"] == {
        "turn": 1,
        "job_id": "job-1",
        "job_type": "process_response",
        "session_id": SESSION_A,
        "tenant_id": TENANT_A,
    }

    assert sqlite_backend.claim_jobs(worker_id="worker-2") == []

    completed = sqlite_backend.complete_job(
        job_id="job-1",
        result={"status": "waiting"},
        tenant_id=TENANT_A,
        fencing_token=claimed[0]["fencing_token"],
    )

    assert completed["status"] == "completed"
    assert completed["result"] == {"status": "waiting"}
    assert completed["completed_at"]

    latest = sqlite_backend.latest_job(
        session_id=SESSION_A,
        job_type="process_response",
        tenant_id=TENANT_A,
    )
    assert latest["job_id"] == "job-1"


def test_unique_active_job_rejects_duplicate_response_submission(sqlite_backend):
    enqueue("job-1", unique_active=True)

    with pytest.raises(DuplicateJobError):
        enqueue("job-2", unique_active=True)

    claimed = sqlite_backend.claim_jobs(worker_id="worker-1")[0]
    sqlite_backend.complete_job(
        job_id="job-1",
        result={"status": "waiting"},
        tenant_id=TENANT_A,
        fencing_token=claimed["fencing_token"],
    )

    enqueue("job-2", unique_active=True)

    assert sqlite_backend.has_active_job(
        session_id=SESSION_A,
        job_type="process_response",
        tenant_id=TENANT_A,
    )


def test_failed_job_is_retried_until_attempt_budget_exhausted(sqlite_backend):
    enqueue("job-1", max_attempts=2)

    first = sqlite_backend.claim_jobs(worker_id="worker-1")[0]
    assert first["attempts"] == 1

    retried = sqlite_backend.fail_job(
        job_id="job-1",
        error="temporary",
        tenant_id=TENANT_A,
        retry_delay_seconds=0,
        fencing_token=first["fencing_token"],
    )
    assert retried["status"] == "queued"
    assert retried["attempts"] == 1

    second = sqlite_backend.claim_jobs(worker_id="worker-1")[0]
    assert second["attempts"] == 2

    failed = sqlite_backend.fail_job(
        job_id="job-1",
        error="permanent",
        tenant_id=TENANT_A,
        fencing_token=second["fencing_token"],
    )
    assert failed["status"] == "failed"
    assert failed["error"] == "permanent"
    assert failed["completed_at"]


def test_stale_processing_job_is_recovered(sqlite_backend):
    enqueue("job-1", max_attempts=2)
    sqlite_backend.claim_jobs(worker_id="worker-1")

    recovered = sqlite_backend.recover_stale_jobs(
        stale_before="2999-01-01T00:00:00.000+00:00",
        tenant_id=TENANT_A,
    )

    assert recovered == 1
    job = sqlite_backend.get_job(job_id="job-1", tenant_id=TENANT_A)
    assert job["status"] == "queued"
    assert job["attempts"] == 1
    assert job["worker_id"] is None


def test_voice_outbox_is_tenant_and_session_scoped(sqlite_backend):
    event_a = {"event_type": "contextual_engagement", "text": "A"}
    event_b = {"event_type": "contextual_engagement", "text": "B"}

    sqlite_backend.enqueue_voice_event(
        event_id="event-a",
        session_id=SESSION_A,
        event=event_a,
        tenant_id=TENANT_A,
    )
    sqlite_backend.enqueue_voice_event(
        event_id="event-b",
        session_id=SESSION_A,
        event=event_b,
        tenant_id=TENANT_B,
    )

    first = sqlite_backend.drain_voice_events(
        session_id=SESSION_A,
        tenant_id=TENANT_A,
    )
    assert [event["event_type"] for event in first] == ["contextual_engagement"]
    assert first[0]["text"] == "A"

    assert sqlite_backend.drain_voice_events(
        session_id=SESSION_A,
        tenant_id=TENANT_A,
    ) == []

    assert len(
        sqlite_backend.drain_voice_events(
            session_id=SESSION_A,
            tenant_id=TENANT_B,
        )
    ) == 1


def test_pending_action_is_tenant_scoped_and_replaceable(sqlite_backend):
    sqlite_backend.save_pending_action(
        session_id=SESSION_A,
        payload={"type": "await_consumer_response", "turn": 1},
        tenant_id=TENANT_A,
    )
    sqlite_backend.save_pending_action(
        session_id=SESSION_A,
        payload={"type": "deliver_question", "turn": 2},
        tenant_id=TENANT_B,
    )

    assert sqlite_backend.get_pending_action(
        session_id=SESSION_A,
        tenant_id=TENANT_A,
    ) == {"type": "await_consumer_response", "turn": 1}

    sqlite_backend.clear_pending_action(
        session_id=SESSION_A,
        tenant_id=TENANT_A,
    )

    assert sqlite_backend.get_pending_action(
        session_id=SESSION_A,
        tenant_id=TENANT_A,
    ) is None
    assert sqlite_backend.get_pending_action(
        session_id=SESSION_A,
        tenant_id=TENANT_B,
    ) == {"type": "deliver_question", "turn": 2}


def test_postgres_queue_schema_uses_psycopg_placeholders(monkeypatch):
    class FakeCursor:
        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class FakeConnection:
        def __init__(self):
            self.statements = []

        def execute(self, sql, params=None):
            self.statements.append((sql, params))
            return FakeCursor()

        def executemany(self, sql, rows):
            self.statements.append((sql, rows))
            return FakeCursor()

    fake = FakeConnection()
    monkeypatch.setattr(postgres_repository, "_database_url", "postgresql://cvi@db/cvi")
    monkeypatch.setattr(
        postgres_repository,
        "_connect",
        lambda: __import__("contextlib").nullcontext(fake),
    )

    postgres_repository.configure("postgresql://cvi@db/cvi")

    statements = "\n".join(sql for sql, _ in fake.statements)
    assert "CREATE TABLE IF NOT EXISTS background_jobs" in statements
    assert "CREATE TABLE IF NOT EXISTS voice_output_events" in statements
    assert "CREATE TABLE IF NOT EXISTS session_pending_actions" in statements
    assert "CREATE TABLE IF NOT EXISTS session_leases" in statements
    assert "%s" in statements
    assert "?" not in statements
