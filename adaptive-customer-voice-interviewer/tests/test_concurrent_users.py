"""Concurrency isolation for simultaneous participants and workers."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

import api
from agent.persistence import sqlite as sqlite_repository


TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
SESSION_A = "session-a"
SESSION_B = "session-b"


@pytest.fixture
def sqlite_backend(tmp_path, monkeypatch):
    database = tmp_path / "concurrency.sqlite3"
    monkeypatch.setattr(sqlite_repository, "_database_path", database)
    sqlite_repository.configure(database)
    return sqlite_repository


def _expiry(seconds: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat(timespec="milliseconds")


def test_session_lease_blocks_a_second_worker(sqlite_backend):
    assert sqlite_backend.acquire_session_lease(
        session_id=SESSION_A,
        owner_id="worker-1",
        ttl_seconds=30,
        tenant_id=TENANT_A,
    )

    assert not sqlite_backend.acquire_session_lease(
        session_id=SESSION_A,
        owner_id="worker-2",
        ttl_seconds=30,
        tenant_id=TENANT_A,
    )

    lease = sqlite_backend.get_session_lease(
        session_id=SESSION_A,
        tenant_id=TENANT_A,
    )
    assert lease["owner_id"] == "worker-1"

    sqlite_backend.release_session_lease(
        session_id=SESSION_A,
        owner_id="worker-1",
        tenant_id=TENANT_A,
    )

    assert sqlite_backend.acquire_session_lease(
        session_id=SESSION_A,
        owner_id="worker-2",
        ttl_seconds=30,
        tenant_id=TENANT_A,
    )


def test_session_lease_expires_and_is_tenant_scoped(sqlite_backend):
    sqlite_backend.save_pending_action(
        session_id=SESSION_A,
        payload={"tenant": TENANT_A},
        tenant_id=TENANT_A,
    )

    assert sqlite_backend.acquire_session_lease(
        session_id=SESSION_A,
        owner_id="worker-1",
        ttl_seconds=30,
        tenant_id=TENANT_A,
    )

    assert sqlite_backend.acquire_session_lease(
        session_id=SESSION_A,
        owner_id="worker-2",
        ttl_seconds=30,
        tenant_id=TENANT_B,
    )

    with sqlite_repository._connect() as connection:
        connection.execute(
            """
            UPDATE session_leases
            SET expires_at = ?
            WHERE tenant_id = ? AND session_id = ?
            """,
            (_expiry(-1), TENANT_A, SESSION_A),
        )

    assert sqlite_backend.renew_session_lease(
        session_id=SESSION_A,
        owner_id="worker-1",
        ttl_seconds=30,
        tenant_id=TENANT_A,
    ) is False

    assert sqlite_backend.acquire_session_lease(
        session_id=SESSION_A,
        owner_id="worker-3",
        ttl_seconds=30,
        tenant_id=TENANT_A,
    )


def test_only_one_racing_worker_acquires_a_session_lease(sqlite_backend):
    barrier = threading.Barrier(2, timeout=3)
    results = []

    def attempt(owner_id):
        barrier.wait()
        acquired = sqlite_backend.acquire_session_lease(
            session_id=SESSION_A,
            owner_id=owner_id,
            ttl_seconds=30,
            tenant_id=TENANT_A,
        )
        results.append((owner_id, acquired))

    threads = [
        threading.Thread(target=attempt, args=("worker-1",)),
        threading.Thread(target=attempt, args=("worker-2",)),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert sum(acquired for _, acquired in results) == 1


def test_response_submissions_do_not_share_a_global_lock(monkeypatch):
    """Two participants can enter the scheduling critical section together."""

    monkeypatch.setattr(api, "WORKER_ENABLED", True)
    monkeypatch.setattr(api, "response_jobs", {})
    monkeypatch.setattr(api, "voice_output_queues", {})
    monkeypatch.setattr(api, "voice_output_events", {})

    sessions = {
        SESSION_A: {"session_id": SESSION_A},
        SESSION_B: {"session_id": SESSION_B},
    }
    pending = {
        "type": "await_consumer_response",
        "turn": 1,
        "question": "What did you notice?",
        "interaction_mode": "voice",
    }

    monkeypatch.setattr(api, "resolve_session", lambda sid: sessions.get(sid))
    monkeypatch.setattr(api, "get_pending_interrupt", lambda sid: pending)
    monkeypatch.setattr(api, "has_processing_response_job", lambda sid: False)
    monkeypatch.setattr(api, "_tenant_for_runtime", lambda sid: TENANT_A)
    monkeypatch.setattr(api, "_record_timing", lambda *args, **kwargs: None)
    monkeypatch.setattr(api.job_service, "new_job_id", lambda kind: f"job-{kind}")
    monkeypatch.setattr(api.read_model, "record_event", lambda *args, **kwargs: None)

    both_entered = threading.Barrier(2, timeout=3)

    def fake_enqueue(**kwargs):
        both_entered.wait()
        return {"job_id": kwargs["job_id"]}

    monkeypatch.setattr(api.repository, "enqueue_job", fake_enqueue)

    errors = []

    def submit(session_id):
        try:
            api.schedule_response_submission(
                session_id,
                api.VoiceResponseRequest(transcript="A useful answer."),
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=submit, args=(SESSION_A,)),
        threading.Thread(target=submit, args=(SESSION_B,)),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []


def test_worker_lease_contention_skips_duplicate_processing(monkeypatch):
    """A second worker never resumes the same session concurrently."""

    monkeypatch.setattr(api, "WORKER_ENABLED", True)
    monkeypatch.setattr(api, "response_jobs", {})
    monkeypatch.setattr(api, "_tenant_for_runtime", lambda sid: TENANT_A)

    class ContendedRepository:
        def acquire_session_lease(self, **kwargs):
            return False

    monkeypatch.setattr(api, "repository", ContendedRepository())

    class UnexpectedGraphCall(AssertionError):
        pass

    class FakeGraph:
        def stream(self, *args, **kwargs):
            raise UnexpectedGraphCall("graph stream must not run")

    monkeypatch.setattr(api, "graph", FakeGraph())

    result = api.process_response_in_background(
        SESSION_A,
        {"session_id": SESSION_A, "turn": 1},
    )

    assert result["status"] == "skipped"
