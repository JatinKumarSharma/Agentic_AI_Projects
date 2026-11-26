import pytest

from agent import read_model


SESSION = {
    "session_id": "read-model-session",
    "company_name": "Read Model Co",
    "product_name": "Insight Kit",
    "product_category": "Analytics",
    "research_goal": "Understand analyst needs.",
    "status": "active",
}

STATE = {
    "iteration_count": 2,
    "research_context": {
        "company_name": "Read Model Co",
        "product_name": "Insight Kit",
    },
    "questions_asked": ["What is frustrating?"],
    "responses": ["Setup takes too long."],
    "pain_points": {
        "setup": {
            "values": [
                {
                    "value": "setup takes too long",
                    "source": "consumer_response",
                    "confidence": "high",
                }
            ],
            "criticality": "critical",
            "sufficiency": "basic",
        }
    },
    "unresolved_gaps": [
        {
            "parameter": "Purchase intent",
            "gap_type": "missing",
        }
    ],
    "confidence": {"pain_points": "high"},
    "state_history": [{}, {}],
}


@pytest.fixture
def read_model_db(tmp_path):
    database_path = tmp_path / "research_read_model.sqlite3"
    read_model.configure(database_path)

    yield database_path

    read_model.configure()


def test_derive_activity_treats_decision_policy_stop_as_completed():
    state = {"next_action": "stop", "termination_reason": "sufficiency_reached"}

    assert read_model.derive_activity(SESSION, state) == "completed"


def test_build_projection_flattens_traceable_dashboard_data():
    projection = read_model.build_projection(SESSION, STATE, "awaiting_response")

    assert projection["session"]["activity"] == "awaiting_response"
    assert projection["iteration_count"] == 2
    assert projection["conversation"] == [
        {
            "turn": 1,
            "question": "What is frustrating?",
            "response": "Setup takes too long.",
        }
    ]

    knowledge = {
        (item["category"], item["parameter"]): item
        for item in projection["knowledge"]
    }

    assert set(knowledge) == {("pain_points", "setup")}
    assert knowledge[("pain_points", "setup")]["criticality"] == "critical"
    assert projection["evidence_count"] == 1
    assert projection["questions_asked_count"] == 1
    assert projection["responses_count"] == 1
    assert projection["unresolved_gaps_count"] == 1
    assert projection["quality"]["confidence"] == {"pain_points": "high"}
    assert projection["state_history_count"] == 2


def test_session_snapshots_and_events_survive_reconfiguration(read_model_db):
    read_model.upsert_session(SESSION)

    snapshot = read_model.record_snapshot(
        SESSION["session_id"],
        STATE,
        "response_processed",
        "awaiting_response",
    )
    event = read_model.record_event(
        SESSION["session_id"],
        "response_submitted",
        {"transcript": "Setup takes too long."},
    )

    assert snapshot["source"] == "response_processed"
    assert event["event_type"] == "response_submitted"

    # Simulate a process restart against the same durable file.
    read_model.configure(read_model_db)

    assert read_model.get_session(SESSION["session_id"]) == SESSION
    assert [item["session_id"] for item in read_model.list_sessions()] == [
        SESSION["session_id"]
    ]

    latest = read_model.get_latest_snapshot(SESSION["session_id"])

    assert latest["session"]["activity"] == "awaiting_response"
    assert latest["conversation"][0]["response"] == "Setup takes too long."
    assert latest["snapshot"]["source"] == "response_processed"
    assert latest["snapshot"]["state"]["iteration_count"] == 2

    metadata = read_model.list_snapshot_metadata(SESSION["session_id"])

    assert len(metadata) == 1
    assert metadata[0]["snapshot_id"] == snapshot["snapshot_id"]

    events = read_model.list_events(SESSION["session_id"])

    assert len(events) == 1
    assert events[0]["event_type"] == "response_submitted"
    assert events[0]["payload"]["transcript"] == "Setup takes too long."


def test_latest_snapshot_recomputes_activity_for_decision_policy_stop(
    read_model_db,
):
    read_model.upsert_session(SESSION)

    read_model.record_snapshot(
        SESSION["session_id"],
        STATE,
        "response_processed",
        "processing_response",
    )
    read_model.record_snapshot(
        SESSION["session_id"],
        {
            **STATE,
            "next_action": "stop",
            "termination_reason": "critical_information_satisfied",
        },
        "interview_advanced",
        "processing_response",
    )

    latest = read_model.get_latest_snapshot(SESSION["session_id"])

    assert latest["session"]["activity"] == "completed"
    assert latest["snapshot"]["source"] == "interview_advanced"


def test_latest_snapshot_prefers_most_recent_state(read_model_db):
    read_model.upsert_session(SESSION)

    read_model.record_snapshot(
        SESSION["session_id"],
        {"iteration_count": 1},
        "interview_started",
    )
    read_model.record_snapshot(
        SESSION["session_id"],
        STATE,
        "response_processed",
    )

    latest = read_model.get_latest_snapshot(SESSION["session_id"])

    assert latest["iteration_count"] == 2
    assert latest["snapshot"]["source"] == "response_processed"
    assert len(read_model.list_snapshot_metadata(SESSION["session_id"])) == 2
