from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api
from agent import read_model


SESSION_ID = "timing-session"
SESSION = {
    "session_id": SESSION_ID,
    "company_name": "Timing Company",
    "company_id": "timing-company",
    "product_name": "Timing Product",
    "product_id": "timing-product",
    "product_category": "Analytics",
    "research_goal": "Understand timing behaviour.",
    "status": "active",
}

DECOMPOSITION_EVENT = {
    "event": "contextual_engagement_input",
    "session_id": SESSION_ID,
    "interview_id": SESSION_ID,
    "decomposition": {
        "summary": "The participant values ease of use.",
        "evidence": [
            {
                "category": "usability",
                "parameter": "ease_of_use",
                "value": "easy to use",
                "sentiment": "positive",
                "evidence_type": "explicit",
                "confidence": "high",
            }
        ],
        "ambiguity_detected": False,
        "potential_contradictions": [],
    },
}


class ControlledClock:
    def __init__(self, start: float = 100.0):
        self.value = start
        self.lock = Lock()

    def __call__(self) -> float:
        with self.lock:
            self.value += 1.0
            return self.value


class ThreadExecutor:
    """Runs submitted work on a thread so it can be held deliberately."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.futures = []

    def submit(self, function, *args):
        future = self.pool.submit(function, *args)
        self.futures.append(future)
        return future

    def shutdown(self):
        self.pool.shutdown(wait=True)


@pytest.fixture(autouse=True)
def reset_timing_state(tmp_path):
    read_model.configure(tmp_path / "conversation-timing.sqlite3")

    api.sessions.clear()
    api.pending_interrupts.clear()
    api.response_jobs.clear()
    api.session_locks.clear()
    api.voice_output_queues.clear()
    api.voice_output_events.clear()
    api.pending_engagement_tasks.clear()

    read_model.upsert_session(SESSION)

    yield

    read_model.configure()


def test_read_model_persists_and_filters_timing_spans():
    read_model.record_timing(
        session_id=SESSION_ID,
        turn=1,
        span_name="response_processing",
        started_ms=100.0,
        ended_ms=185.5,
        duration_ms=85.5,
        source="runtime",
        parent_span_id=None,
        metadata={"status": "completed"},
    )
    read_model.record_timing(
        session_id=SESSION_ID,
        turn=1,
        span_name="first_question_rendered",
        started_ms=90.0,
        ended_ms=98.0,
        duration_ms=8.0,
        source="frontend",
        metadata={"phase": "dom_render"},
    )

    spans = read_model.list_timings(SESSION_ID)

    assert [span["span_name"] for span in spans] == [
        "first_question_rendered",
        "response_processing",
    ]
    assert spans[1]["duration_ms"] == 85.5
    assert spans[1]["metadata"] == {"status": "completed"}

    frontend_spans = read_model.list_timings(
        SESSION_ID,
        source="frontend",
    )

    assert len(frontend_spans) == 1
    assert frontend_spans[0]["span_name"] == "first_question_rendered"
    assert frontend_spans[0]["metadata"]["phase"] == "dom_render"


def test_participant_timing_endpoint_records_browser_clock():
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/timings",
        json={
            "turn": 1,
            "span_name": "participant_response_time",
            "started_ms": 1200.25,
            "ended_ms": 5800.75,
            "metadata": {"input_mode": "typed"},
        },
    )

    assert response.status_code == 202

    spans = read_model.list_timings(SESSION_ID)

    assert len(spans) == 1
    assert spans[0]["source"] == "frontend"
    assert spans[0]["span_name"] == "participant_response_time"
    assert spans[0]["duration_ms"] == 4600.5
    assert spans[0]["metadata"]["input_mode"] == "typed"
    assert spans[0]["metadata"]["browser_clock"] == "performance.now"

    invalid_response = client.post(
        f"/sessions/{SESSION_ID}/timings",
        json={
            "turn": 1,
            "span_name": "invalid_span",
            "started_ms": 20.0,
            "ended_ms": 10.0,
        },
    )

    assert invalid_response.status_code == 422


def test_participant_timing_endpoint_rejects_unknown_session():
    client = TestClient(api.app)

    response = client.post(
        "/sessions/missing-session/timings",
        json={
            "turn": 0,
            "span_name": "invitation_resolved",
            "started_ms": 0.0,
            "ended_ms": 10.0,
        },
    )

    assert response.status_code == 404


def test_researcher_timings_endpoint_filters_spans():
    read_model.record_timing(
        session_id=SESSION_ID,
        turn=1,
        span_name="first_question_rendered",
        started_ms=10.0,
        ended_ms=18.0,
        duration_ms=8.0,
        source="frontend",
    )
    read_model.record_timing(
        session_id=SESSION_ID,
        turn=1,
        span_name="response_processing",
        started_ms=20.0,
        ended_ms=100.0,
        duration_ms=80.0,
        source="runtime",
    )

    result = api.get_session_timings(SESSION_ID, source="frontend")

    assert result["session_id"] == SESSION_ID
    assert [span["span_name"] for span in result["timings"]] == [
        "first_question_rendered"
    ]

    with pytest.raises(HTTPException) as error:
        api.get_session_timings("missing-session")

    assert error.value.status_code == 404


def test_first_question_span_measures_interview_start(monkeypatch):
    clock = ControlledClock()
    monkeypatch.setattr(api.timing, "now_ms", clock)

    class FakeGraph:
        def invoke(self, *args, **kwargs):
            return {
                "__interrupt__": [
                    SimpleNamespace(
                        value={
                            "type": "deliver_question",
                            "question": "What matters most to you?",
                            "turn": 1,
                        }
                    )
                ]
            }

    monkeypatch.setattr(api, "graph", FakeGraph())

    result = api.start_interview(SESSION_ID)

    assert result["action"]["type"] == "deliver_question"

    spans = read_model.list_timings(SESSION_ID)

    assert len(spans) == 1
    assert spans[0]["span_name"] == "first_question_delivered"
    assert spans[0]["turn"] == 1
    assert spans[0]["started_ms"] == 101.0
    assert spans[0]["ended_ms"] == 102.0
    assert spans[0]["metadata"]["phase"] == "interview_start"


def test_slow_engagement_does_not_block_ack_or_response_processing(
    monkeypatch,
):
    clock = ControlledClock()
    monkeypatch.setattr(api.timing, "now_ms", clock)

    generation_started = Event()
    release_generation = Event()
    executor = ThreadExecutor()

    def slow_generator(decomposition):
        generation_started.set()
        assert release_generation.wait(timeout=5)
        return "Ease of use clearly matters to you."

    monkeypatch.setattr(
        api,
        "generate_contextual_engagement",
        slow_generator,
    )
    monkeypatch.setattr(api, "engagement_executor", executor)

    class FakeGraph:
        def stream(self, *args, **kwargs):
            yield "custom", DECOMPOSITION_EVENT

            # The response graph continues only after the engagement
            # worker has started and is deliberately held open.
            assert generation_started.wait(timeout=5)

            yield "values", {
                "__interrupt__": [
                    SimpleNamespace(
                        value={
                            "type": "deliver_question",
                            "question": "What would you improve?",
                            "turn": 2,
                        }
                    )
                ]
            }

    monkeypatch.setattr(api, "graph", FakeGraph())

    try:
        acknowledgement_messages = api.create_vad_speech_end_messages(
            SESSION_ID,
            1,
            turn=1,
            speech_ms=7000,
            pause_ms=800,
        )

        assert [
            message["type"] for message in acknowledgement_messages
        ] == ["vad_speech_end", "voice_output"]
        assert acknowledgement_messages[1]["event"]["event_type"] == (
            "immediate_acknowledgement"
        )

        api.process_response_in_background(
            SESSION_ID,
            {
                "transcript": "It is easy to use.",
                "turn": 1,
                "submitted_monotonic_ms": 200.0,
            },
        )

        assert api.response_jobs[SESSION_ID]["status"] == "completed"
        assert not executor.futures[0].done()

    finally:
        release_generation.set()
        executor.shutdown()

    spans = read_model.list_timings(SESSION_ID)
    by_name = {}

    for span in spans:
        by_name.setdefault(span["span_name"], []).append(span)

    acknowledgement = by_name["speech_end_acknowledgement_emitted"][0]
    queued = by_name["contextual_engagement_queued"][0]
    generation = by_name["contextual_engagement_generation"][0]
    emitted = by_name["contextual_engagement_emitted"][0]
    processing = by_name["response_processing"][0]
    next_question = by_name["next_question_delivered"][0]

    # The model-free acknowledgement is emitted first, while the
    # contextual model span is still blocked.
    assert acknowledgement["ended_ms"] < generation["started_ms"]
    assert acknowledgement["ended_ms"] < processing["ended_ms"]

    assert queued["source"] == "voice_chain"
    assert queued["parent_span_id"] == "response_processing"

    assert generation["metadata"]["status"] == "completed"
    assert generation["metadata"]["emitted"] is True

    assert emitted["source"] == "voice_chain"
    assert emitted["metadata"]["emitted"] is True
    assert emitted["ended_ms"] > generation["ended_ms"]

    assert processing["metadata"]["status"] == "completed"
    assert next_question["turn"] == 2
    assert next_question["metadata"]["from_turn"] == 1
