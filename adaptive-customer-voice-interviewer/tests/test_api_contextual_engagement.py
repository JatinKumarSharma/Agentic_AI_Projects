
import api


SESSION_ID = "test-session-123"

DECOMPOSITION_EVENT = {
    "event": "contextual_engagement_input",
    "session_id": SESSION_ID,
    "interview_id": SESSION_ID,
    "decomposition": {
        "summary": "The consumer values ease of use.",
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


class FakeExecutor:
    """Records submitted tasks without executing them."""

    def __init__(self):
        self.submissions = []

    def submit(self, function, *args):
        self.submissions.append((function, args))


class ImmediateExecutor:
    """Executes submitted tasks immediately for deterministic tests."""

    def submit(self, function, *args):
        function(*args)


def reset_api_state():
    api.response_jobs.clear()
    api.session_locks.clear()
    api.voice_output_queues.clear()
    api.voice_output_events.clear()
    api.pending_engagement_tasks.clear()
    api.sessions.clear()


def test_engagement_task_is_submitted_before_graph_finishes(monkeypatch):
    reset_api_state()

    executor = FakeExecutor()
    monkeypatch.setattr(api, "engagement_executor", executor)

    class FakeGraph:

        def stream(self, *args, **kwargs):
            yield "custom", DECOMPOSITION_EVENT

            # This assertion executes only when the API asks
            # the stream for its next event.
            # Therefore, the custom event must already
            # have been handled.
            assert len(executor.submissions) == 1

            yield "values", {
                "interview_complete": True,
            }

    monkeypatch.setattr(api, "graph", FakeGraph())

    api.process_response_in_background(
        SESSION_ID,
        {"transcript": "It is easy to use."},
    )

    assert len(executor.submissions) == 1

    function, args = executor.submissions[0]

    assert function == api.process_contextual_engagement
    assert args[0] == SESSION_ID
    assert args[1] == SESSION_ID
    assert args[2] == DECOMPOSITION_EVENT["decomposition"]

    assert api.response_jobs[SESSION_ID]["status"] == "completed"


def test_contextual_engagement_is_queued_for_correct_session(monkeypatch):
    reset_api_state()

    session_a = "session-a"
    session_b = "session-b"

    api.voice_output_queues[session_a] = api.Queue()
    api.voice_output_queues[session_b] = api.Queue()

    monkeypatch.setattr(
        api,
        "generate_contextual_engagement",
        lambda decomposition: "Ease of use clearly matters to you.",
    )

    api.process_contextual_engagement(
        session_a,
        session_a,
        DECOMPOSITION_EVENT["decomposition"],
    )

    record_a = api.voice_output_events[session_a][0]
    event_a = record_a["event"]

    assert record_a["event_id"].startswith("voice_")
    assert event_a["session_id"] == session_a
    assert event_a["interview_id"] == session_a
    assert event_a["event_type"] == "contextual_engagement"
    assert event_a["text"] == "Ease of use clearly matters to you."

    assert api.voice_output_events.get(session_b, []) == []


def test_engagement_generation_failure_does_not_fail_research_graph(
    monkeypatch,
):
    reset_api_state()

    api.voice_output_queues[SESSION_ID] = api.Queue()

    monkeypatch.setattr(
        api,
        "engagement_executor",
        ImmediateExecutor(),
    )

    def failing_generator(decomposition):
        raise RuntimeError("Simulated LLM failure")

    monkeypatch.setattr(
        api,
        "generate_contextual_engagement",
        failing_generator,
    )

    class FakeGraph:

        def stream(self, *args, **kwargs):
            yield "custom", DECOMPOSITION_EVENT

            yield "values", {
                "interview_complete": True,
            }

    monkeypatch.setattr(api, "graph", FakeGraph())

    api.process_response_in_background(
        SESSION_ID,
        {"transcript": "It is easy to use."},
    )

    assert api.response_jobs[SESSION_ID]["status"] == "completed"

    assert (
        api.response_jobs[SESSION_ID]["result"]["status"]
        == "completed"
    )

    assert api.voice_output_queues[SESSION_ID].empty()



def test_voice_events_endpoint_keeps_events_until_acknowledged():
    reset_api_state()

    session_a = "session-a"
    api.sessions[session_a] = {}

    api.voice_output_queues[session_a] = api.Queue()

    event = {
        "session_id": session_a,
        "interview_id": session_a,
        "event_type": "contextual_engagement",
        "text": "Ease of use clearly matters to you.",
        "timestamp": 123.0,
    }

    api.voice_output_queues[session_a].put(event)
    api.pending_engagement_tasks[session_a] = 1

    result = api.get_voice_events(session_a)

    assert result["session_id"] == session_a
    assert result["events"][0]["event_type"] == event["event_type"]
    assert result["events"][0]["text"] == event["text"]
    assert result["events"][0]["event_id"].startswith("voice_")
    assert result["pending"] == 1

    event_id = result["events"][0]["event_id"]

    second_result = api.get_voice_events(session_a)

    assert [item["event_id"] for item in second_result["events"]] == [
        event_id
    ]

    api._acknowledge_offline_voice_events(session_a, [event_id])

    third_result = api.get_voice_events(session_a)

    assert third_result["events"] == []
    assert third_result["pending"] == 1


def test_voice_events_endpoint_isolates_sessions():
    reset_api_state()

    session_a = "session-a"
    session_b = "session-b"

    api.sessions[session_a] = {}
    api.sessions[session_b] = {}

    api.voice_output_queues[session_a] = api.Queue()
    api.voice_output_queues[session_b] = api.Queue()

    event_a = {
        "session_id": session_a,
        "event_type": "contextual_engagement",
        "text": "Your experience sounds straightforward.",
    }

    event_b = {
        "session_id": session_b,
        "event_type": "contextual_engagement",
        "text": "Reliability matters to you.",
    }

    api.voice_output_queues[session_a].put(event_a)
    api.voice_output_queues[session_b].put(event_b)

    result_a = api.get_voice_events(session_a)
    result_b = api.get_voice_events(session_b)

    assert [item["text"] for item in result_a["events"]] == [
        event_a["text"]
    ]
    assert [item["text"] for item in result_b["events"]] == [
        event_b["text"]
    ]


def test_voice_events_endpoint_returns_empty_when_no_events():
    reset_api_state()

    session_id = "session-empty"
    api.sessions[session_id] = {}

    result = api.get_voice_events(session_id)

    assert result["events"] == []
    assert result["pending"] == 0


def test_contextual_engagement_decrements_pending_count(monkeypatch):
    reset_api_state()

    session_id = "session-pending"

    api.sessions[session_id] = {}

    api.voice_output_queues[session_id] = api.Queue()
    api.pending_engagement_tasks[session_id] = 1

    monkeypatch.setattr(
        api,
        "generate_contextual_engagement",
        lambda decomposition: "Ease of use matters to you.",
    )

    api.process_contextual_engagement(
        session_id,
        session_id,
        DECOMPOSITION_EVENT["decomposition"],
    )

    assert api.pending_engagement_tasks.get(session_id, 0) == 0

    result = api.get_voice_events(session_id)

    assert len(result["events"]) == 1
    assert result["pending"] == 0


def test_contextual_engagement_failure_decrements_pending_count(
    monkeypatch,
):
    reset_api_state()

    session_id = "session-failure"

    api.voice_output_queues[session_id] = api.Queue()
    api.pending_engagement_tasks[session_id] = 1

    def failing_generator(decomposition):
        raise RuntimeError("Simulated failure")

    monkeypatch.setattr(
        api,
        "generate_contextual_engagement",
        failing_generator,
    )

    api.process_contextual_engagement(
        session_id,
        session_id,
        DECOMPOSITION_EVENT["decomposition"],
    )

    assert api.pending_engagement_tasks.get(session_id, 0) == 0
    assert api.voice_output_queues[session_id].empty()

