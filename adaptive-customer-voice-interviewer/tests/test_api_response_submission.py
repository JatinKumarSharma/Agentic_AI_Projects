import pytest
from fastapi import HTTPException

import api
from agent.jobs.errors import DuplicateJobError


SESSION_ID = "submission-test-session"
TENANT = "default"
ORIGINAL_REPOSITORY = api.repository
ORIGINAL_WORKER_ENABLED = api.WORKER_ENABLED


class FakeRepository:
    """Minimal durable-job repository for API scheduling tests."""

    def __init__(self):
        self.jobs = {}
        self.pending = {}
        self.voice_events = {}
        self.pending_counts = {}
        self.fail_enqueue = False
        self.duplicate_enqueue = False

    def enqueue_job(self, **kwargs):
        if self.fail_enqueue:
            raise RuntimeError("Executor unavailable")

        if self.duplicate_enqueue:
            raise DuplicateJobError("Duplicate active job.")

        job = {
            "job_id": kwargs["job_id"],
            "job_type": kwargs["job_type"],
            "session_id": kwargs["session_id"],
            "tenant_id": kwargs.get("tenant_id") or TENANT,
            "status": "queued",
            "payload": kwargs["payload"],
            "result": None,
            "error": None,
        }
        self.jobs[job["job_id"]] = job
        return job

    def has_active_job(self, *, session_id, job_type, tenant_id=None):
        return any(
            job["session_id"] == session_id
            and job["job_type"] == job_type
            and job["status"] in {"queued", "processing"}
            for job in self.jobs.values()
        )

    def latest_job(self, *, session_id, job_type, tenant_id=None):
        matches = [
            job
            for job in self.jobs.values()
            if job["session_id"] == session_id and job["job_type"] == job_type
        ]
        return matches[-1] if matches else None

    def save_pending_action(self, *, session_id, payload, tenant_id=None):
        self.pending[session_id] = payload

    def get_pending_action(self, *, session_id, tenant_id=None):
        return self.pending.get(session_id)

    def clear_pending_action(self, *, session_id, tenant_id=None):
        self.pending.pop(session_id, None)

    def enqueue_voice_event(self, *, event_id, session_id, event, tenant_id=None):
        self.voice_events.setdefault(session_id, []).append(
            {"event_id": event_id, "event": event}
        )

    def list_voice_events(self, *, session_id, tenant_id=None, limit=100):
        return self.voice_events.get(session_id, [])[:limit]

    def acknowledge_voice_events(
        self,
        *,
        session_id,
        event_ids,
        tenant_id=None,
    ):
        records = self.voice_events.get(session_id, [])
        ids = set(event_ids)
        remaining = [
            record
            for record in records
            if record["event_id"] not in ids
        ]
        acknowledged = len(records) - len(remaining)
        self.voice_events[session_id] = remaining
        return acknowledged

    def drain_voice_events(self, *, session_id, tenant_id=None, limit=100):
        records = self.list_voice_events(
            session_id=session_id,
            tenant_id=tenant_id,
            limit=limit,
        )
        self.acknowledge_voice_events(
            session_id=session_id,
            tenant_id=tenant_id,
            event_ids=[record["event_id"] for record in records],
        )
        return [record["event"] for record in records]

    def count_pending_jobs(self, *, session_id, job_type, tenant_id=None):
        return self.pending_counts.get(session_id, 0)


def setup_function(_function):
    api.sessions.clear()
    api.pending_interrupts.clear()
    api.response_jobs.clear()
    api.session_locks.clear()
    api.voice_output_queues.clear()
    api.voice_output_events.clear()
    api.pending_engagement_tasks.clear()

    api.WORKER_ENABLED = True
    api.repository = FakeRepository()

    api.sessions[SESSION_ID] = {"session_id": SESSION_ID}

    pending = {
        "type": "await_consumer_response",
        "question": "What do you think about the product?",
        "interaction_mode": "research_answer",
    }
    api.pending_interrupts[SESSION_ID] = pending
    api.repository.pending[SESSION_ID] = pending


def teardown_function(_function):
    api.repository = ORIGINAL_REPOSITORY
    api.WORKER_ENABLED = ORIGINAL_WORKER_ENABLED


def test_decision_policy_stop_is_reported_as_completed():
    result = api.extract_next_action(
        SESSION_ID,
        {
            "interview_complete": False,
            "next_action": "stop",
            "termination_reason": "critical_information_satisfied",
        },
    )

    assert result["status"] == "completed"
    assert result["termination_reason"] == (
        "critical_information_satisfied"
    )
    assert SESSION_ID not in api.pending_interrupts
    assert api.repository.get_pending_action(session_id=SESSION_ID) is None


def test_unexplained_finish_remains_finished():
    result = api.extract_next_action(
        SESSION_ID,
        {"interview_complete": False},
    )

    assert result["status"] == "finished"


def test_valid_response_is_enqueued_as_durable_job():
    request = api.VoiceResponseRequest(
        transcript="  It is easy to use.  ",
        speech_duration_ms=2500,
    )

    result = api.schedule_response_submission(SESSION_ID, request)

    assert result["status"] == "processing"
    assert result["session_id"] == SESSION_ID

    assert len(api.repository.jobs) == 1
    job = next(iter(api.repository.jobs.values()))

    assert job["job_type"] == "process_response"
    assert job["session_id"] == SESSION_ID
    assert job["status"] == "queued"

    response_event = job["payload"]
    assert response_event["transcript"] == "It is easy to use."
    assert response_event["speech_detected"] is True
    assert response_event["speech_duration_ms"] == 2500
    assert response_event["interaction_mode"] == "research_answer"

    assert api.response_jobs[SESSION_ID]["status"] == "processing"


def test_unknown_session_returns_404():
    with pytest.raises(HTTPException) as exc:
        api.schedule_response_submission(
            "unknown-session",
            api.VoiceResponseRequest(transcript="Hello"),
        )

    assert exc.value.status_code == 404


def test_missing_pending_response_returns_409():
    api.pending_interrupts.pop(SESSION_ID)
    api.repository.pending.pop(SESSION_ID)

    with pytest.raises(HTTPException) as exc:
        api.schedule_response_submission(
            SESSION_ID,
            api.VoiceResponseRequest(transcript="Hello"),
        )

    assert exc.value.status_code == 409


def test_wrong_pending_interrupt_type_returns_409():
    pending = {
        "type": "await_something_else",
        "question": "Question",
        "interaction_mode": "research_answer",
    }
    api.pending_interrupts[SESSION_ID] = pending
    api.repository.pending[SESSION_ID] = pending

    with pytest.raises(HTTPException) as exc:
        api.schedule_response_submission(
            SESSION_ID,
            api.VoiceResponseRequest(transcript="Hello"),
        )

    assert exc.value.status_code == 409


def test_duplicate_processing_response_returns_409():
    api.response_jobs[SESSION_ID] = {"status": "processing"}

    with pytest.raises(HTTPException) as exc:
        api.schedule_response_submission(
            SESSION_ID,
            api.VoiceResponseRequest(transcript="Hello"),
        )

    assert exc.value.status_code == 409
    assert api.repository.jobs == {}


def test_enqueue_failure_records_failed_job():
    api.repository.fail_enqueue = True

    with pytest.raises(HTTPException) as exc:
        api.schedule_response_submission(
            SESSION_ID,
            api.VoiceResponseRequest(transcript="Hello"),
        )

    assert exc.value.status_code == 500
    assert api.response_jobs[SESSION_ID]["status"] == "failed"
    assert api.response_jobs[SESSION_ID]["error"] == "RuntimeError"


def test_status_is_read_from_durable_job():
    job_id = "job_process_response_1"
    api.repository.jobs[job_id] = {
        "job_id": job_id,
        "job_type": "process_response",
        "session_id": SESSION_ID,
        "status": "completed",
        "payload": {},
        "result": {"status": "waiting"},
        "error": None,
    }

    result = api.get_response_status(SESSION_ID)

    assert result["status"] == "completed"
    assert result["result"] == {"status": "waiting"}


def test_duplicate_job_error_is_reported_as_conflict():
    api.repository.duplicate_enqueue = True

    with pytest.raises(HTTPException) as exc:
        api.schedule_response_submission(
            SESSION_ID,
            api.VoiceResponseRequest(transcript="Hello"),
        )

    assert exc.value.status_code == 409


def test_worker_mode_voice_events_come_from_durable_outbox():
    event = {"type": "contextual_engagement", "text": "Tell me more."}
    api.repository.enqueue_voice_event(
        event_id="voice-1",
        session_id=SESSION_ID,
        event=event,
    )
    api.repository.pending_counts[SESSION_ID] = 2

    result = api.get_voice_events(SESSION_ID)

    assert result["events"][0]["event_id"] == "voice-1"
    assert result["events"][0]["text"] == "Tell me more."
    assert result["pending"] == 2

    repeated = api.get_voice_events(SESSION_ID)
    assert [item["event_id"] for item in repeated["events"]] == ["voice-1"]

    api.repository.acknowledge_voice_events(
        session_id=SESSION_ID,
        event_ids=["voice-1"],
    )

    assert api.get_voice_events(SESSION_ID) == {
        "session_id": SESSION_ID,
        "events": [],
        "pending": 2,
    }


FAILED_PAYLOAD = {
    "response_id": "response_retry_test",
    "transcript": "The setup was simple.",
    "turn": 1,
    "question": "What do you think about the product?",
    "submitted_monotonic_ms": 1234,
}


def _failed_worker_job(result=None):
    return {
        "job_id": "job_failed_response",
        "job_type": "process_response",
        "session_id": SESSION_ID,
        "status": "completed",
        "payload": dict(FAILED_PAYLOAD),
        "result": result or {
            "status": "failed",
            "error": "Response processing failed: RateLimitError",
        },
        "error": None,
    }


class FakeInterrupt:
    def __init__(self, value):
        self.value = value


class FakeSnapshot:
    def __init__(self, interrupts=()):
        self.interrupts = tuple(interrupts)


def test_failed_status_exposes_safe_retry_flag():
    api.repository.jobs["job_failed_response"] = _failed_worker_job()

    result = api.get_response_status(SESSION_ID)

    assert result["status"] == "failed"
    assert "RateLimitError" in result["error"]
    assert result["can_retry"] is True


def test_retry_reuses_original_response_payload(monkeypatch):
    api.repository.jobs["job_failed_response"] = _failed_worker_job()
    monkeypatch.setattr(
        api.graph,
        "get_state",
        lambda config: FakeSnapshot(
            [
                FakeInterrupt(
                    {
                        "type": "await_consumer_response",
                        "turn": 1,
                        "question": FAILED_PAYLOAD["question"],
                    }
                )
            ]
        ),
    )

    result = api.retry_response_processing(SESSION_ID)

    assert result["status"] == "processing"
    assert result["response_id"] == FAILED_PAYLOAD["response_id"]
    assert result["mode"] == "resume"

    jobs = [job for job in api.repository.jobs.values() if job["status"] == "queued"]
    assert len(jobs) == 1

    retry_job = jobs[0]
    assert retry_job["job_id"] != "job_failed_response"
    assert retry_job["payload"]["response_id"] == FAILED_PAYLOAD["response_id"]
    assert retry_job["payload"]["transcript"] == FAILED_PAYLOAD["transcript"]
    assert retry_job["payload"]["retry_of_job_id"] == "job_failed_response"
    assert api.response_jobs[SESSION_ID]["status"] == "processing"


def test_retry_continues_from_checkpoint_when_interrupt_is_gone(monkeypatch):
    api.repository.jobs["job_failed_response"] = _failed_worker_job()
    monkeypatch.setattr(
        api.graph,
        "get_state",
        lambda config: FakeSnapshot(),
    )

    result = api.retry_response_processing(SESSION_ID)

    assert result["mode"] == "continue"


def test_retry_rejects_session_without_failed_response():
    with pytest.raises(HTTPException) as exc:
        api.retry_response_processing(SESSION_ID)

    assert exc.value.status_code == 409
    assert exc.value.detail == "There is no failed response to retry."


def test_offline_retry_schedules_original_payload(monkeypatch):
    api.WORKER_ENABLED = False
    api.response_jobs[SESSION_ID] = {
        "status": "failed",
        "error": "Response processing failed: RateLimitError",
        "payload": dict(FAILED_PAYLOAD),
    }
    submitted = []
    monkeypatch.setattr(
        api.graph,
        "get_state",
        lambda config: FakeSnapshot(),
    )
    monkeypatch.setattr(
        api.response_executor,
        "submit",
        lambda function, *args: submitted.append((function, args)),
    )

    result = api.retry_response_processing(SESSION_ID)

    assert result["status"] == "processing"
    assert result["mode"] == "continue"
    assert len(submitted) == 1
    assert submitted[0][0] is api.process_response_in_background
    assert submitted[0][1][1]["response_id"] == FAILED_PAYLOAD["response_id"]
