from agent import graph as graph_module
from agent import response_triage
from agent.response_outcomes import (
    PATTERN_RECOVERED,
    PATTERN_UNCLASSIFIED,
    PATTERN_UNRESOLVED,
    STATUS_COMPLETED,
    STATUS_UNFINISHED,
    append_outcome,
    classify_response_pattern,
    derive_report_labels,
)


BASE_STATE = {
    "research_session": {
        "session_id": "outcome-session",
        "participant_id": "participant-1",
    },
    "current_question": "What frustrates you?",
    "latest_response": "Setup takes too long.",
    "latest_response_id": "resp_1",
    "latest_response_captured_at": "2026-01-12T10:00:00.000+00:00",
    "latest_response_question": "What frustrates you?",
    "latest_participant_id": "participant-1",
    "response_outcomes": [],
}


class FakeLLM:
    def __init__(self, status):
        self.status = status

    def invoke(self, prompt):
        return type("Result", (), {"content": self.status})()


def test_append_outcome_is_deterministic_and_idempotent():
    first = append_outcome(BASE_STATE, "valid", "response_triage")
    outcome = first["response_outcomes"][0]

    assert outcome["sequence"] == 1
    assert outcome["status"] == "valid"
    assert outcome["transcript"] == "Setup takes too long."
    assert outcome["response_id"] == "resp_1"
    assert outcome["classification_source"] == "response_triage"

    replayed = append_outcome(
        {**BASE_STATE, "response_outcomes": first["response_outcomes"]},
        "valid",
        "response_triage",
    )

    assert replayed["response_outcomes"] == first["response_outcomes"]


def test_classify_response_pattern_detects_recovery_after_invalid():
    outcomes = [
        {"status": "valid"},
        {"status": "unrelated"},
        {"status": "valid"},
    ]

    assert classify_response_pattern(outcomes) == PATTERN_RECOVERED


def test_report_labels_treat_decision_policy_stop_as_completed():
    stop_signals = (
        {"interview_complete": True},
        {"next_action": "stop"},
        {"termination_reason": "critical_information_satisfied"},
        {
            "next_action": "stop",
            "termination_reason": "critical_information_satisfied",
        },
    )

    for signal in stop_signals:
        labels = derive_report_labels({**BASE_STATE, **signal})
        assert labels["status_label"] == STATUS_COMPLETED


def test_report_labels_keep_unfinished_without_stop_signal():
    labels = derive_report_labels(BASE_STATE)

    assert labels["status_label"] == STATUS_UNFINISHED


def test_classify_response_pattern_detects_unresolved_invalid():
    assert classify_response_pattern([
        {"status": "valid"},
        {"status": "unclear"},
    ]) == PATTERN_UNRESOLVED
    assert classify_response_pattern(
        []
    ) == PATTERN_UNCLASSIFIED


def test_triage_response_records_classified_outcome(monkeypatch):
    monkeypatch.setattr(
        response_triage,
        "get_llm",
        lambda: FakeLLM("unrelated"),
    )

    result = response_triage.triage_response(BASE_STATE)

    assert result["response_status"] == "unrelated"
    assert len(result["response_outcomes"]) == 1
    assert result["response_outcomes"][0]["status"] == "unrelated"
    assert result["response_outcomes"][0]["transcript"] == (
        "Setup takes too long."
    )


def test_runtime_audio_issue_is_recorded_only_for_research_answers(
    monkeypatch,
):
    monkeypatch.setattr(
        graph_module,
        "interrupt",
        lambda payload: {
            "response_id": "resp_audio",
            "captured_at": "2026-01-12T10:01:00.000+00:00",
            "participant_id": "participant-1",
            "audio_issue": True,
        },
    )

    research_result = graph_module.receive_consumer_response(BASE_STATE)

    assert research_result["response_status"] == "audio_issue"
    assert research_result["latest_response_id"] == "resp_audio"
    assert research_result["response_outcomes"][0]["status"] == (
        "audio_issue"
    )

    confirmation_state = {
        **BASE_STATE,
        "voice_interaction_mode": "audibility_confirmation",
        "response_outcomes": [],
    }
    confirmation_result = graph_module.receive_consumer_response(
        confirmation_state
    )

    assert confirmation_result["response_status"] == "audio_issue"
    assert "response_outcomes" not in confirmation_result
