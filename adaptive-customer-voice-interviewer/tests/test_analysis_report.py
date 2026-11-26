from copy import deepcopy

import pytest

from agent import read_model
from agent.analysis_report import (
    aggregate_reports,
    build_session_report,
)
from agent.provenance import attach_response_provenance
from agent.state_updater import update_state


SESSION = {
    "session_id": "report-session",
    "company_name": "Analytics Co",
    "product_name": "Insight Kit",
    "product_category": "Analytics",
    "customer_segment": "small business",
    "research_goal": "Understand setup friction.",
    "start_date": "2026-01-10",
    "end_date": "2026-01-20",
    "status": "completed",
}

RESPONSE = "Setup takes too long. It breaks daily work."

STATE = {
    "research_session": SESSION,
    "interview_complete": True,
    "termination_reason": "sufficiency_reached",
    "questions_asked": ["What is frustrating?"],
    "responses": [RESPONSE],
    "response_records": [
        {
            "response_id": "resp_1",
            "turn": 1,
            "question": "What is frustrating?",
            "text": RESPONSE,
            "captured_at": "2026-01-12T10:00:00.000+00:00",
            "participant_id": "participant-1",
        }
    ],
    "pain_points": {
        "setup": {
            "values": [
                {
                    "value": "setup takes too long",
                    "source": "consumer_response",
                    "confidence": "high",
                    "sentiment": "negative",
                    "evidence_type": "explicit",
                    "evidence_id": "ev_1",
                    "response_id": "resp_1",
                    "turn": 1,
                    "captured_at": "2026-01-12T10:00:00.000+00:00",
                    "participant_id": "participant-1",
                    "source_quote": "Setup takes too long.",
                }
            ],
            "sufficiency": "basic",
            "confidence": "high",
            "criticality": "critical",
            "verification_status": "unverified",
            "applicability_status": "applicable",
        }
    },
}


@pytest.fixture
def report_db(tmp_path):
    database_path = tmp_path / "reports.sqlite3"
    read_model.configure(database_path)

    yield database_path

    read_model.configure()


def test_build_session_report_emits_only_grounded_claims():
    report = build_session_report(SESSION, STATE)

    assert report["validation"] == {
        "valid": True,
        "claim_count": 1,
        "evidence_reference_count": 1,
        "issues": [],
    }
    assert report["dimensions"]["customer_segment"] == "small business"
    assert report["dimensions"]["period_start"] == "2026-01-10"
    assert report["summary"]["grounding"] == "deterministic_evidence_only"
    assert report["summary"]["claim_count"] == 1
    assert report["summary"]["evidence_count"] == 1

    claim = report["claims"][0]
    assert claim["category"] == "pain_points"
    assert claim["parameter"] == "setup"
    assert claim["statement"] == "Setup takes too long."
    assert claim["evidence_ids"] == ["ev_1"]

    reference = claim["evidence"][0]
    assert reference["response_id"] == "resp_1"
    assert reference["source_quote"] == "Setup takes too long."
    assert reference["source_quote"] in RESPONSE


def test_repeated_quotes_are_deduplicated_in_claim_statement():
    state = deepcopy(STATE)
    duplicate = deepcopy(state["pain_points"]["setup"]["values"][0])
    duplicate.update({
        "evidence_id": "ev_2",
        "response_id": "resp_2",
        "turn": 2,
    })
    state["pain_points"]["setup"]["values"].append(duplicate)
    state["responses"].append(RESPONSE)
    state["response_records"].append({
        "response_id": "resp_2",
        "turn": 2,
        "question": "What is frustrating?",
        "text": RESPONSE,
        "captured_at": "2026-01-12T10:01:00.000+00:00",
        "participant_id": "participant-1",
    })

    report = build_session_report(SESSION, state)

    claim = report["claims"][0]
    assert claim["statement"] == "Setup takes too long."
    assert claim["evidence_ids"] == ["ev_1", "ev_2"]
    assert len(claim["evidence"]) == 2


def test_build_session_report_rejects_cross_session_evidence():
    state = deepcopy(STATE)
    state["pain_points"]["setup"]["values"][0]["response_id"] = (
        "resp_from_other_session"
    )

    with pytest.raises(ValueError, match="ungrounded"):
        build_session_report(SESSION, state)


def test_build_session_report_rejects_missing_source_quote():
    state = deepcopy(STATE)
    state["pain_points"]["setup"]["values"][0]["source_quote"] = ""

    with pytest.raises(ValueError, match="ungrounded"):
        build_session_report(SESSION, state)


def test_build_session_report_rejects_non_verbatim_source_quote():
    state = deepcopy(STATE)
    state["pain_points"]["setup"]["values"][0]["source_quote"] = (
        "The setup experience was terrible"
    )

    with pytest.raises(ValueError, match="not verbatim"):
        build_session_report(SESSION, state)


def test_stored_reports_can_be_filtered_by_reporting_dimensions(report_db):
    report = build_session_report(SESSION, STATE)
    read_model.upsert_session(SESSION)
    read_model.store_report(report)

    assert read_model.get_report(report["report_id"]) == report

    matching = read_model.list_reports(
        period_start="2026-01-15",
        period_end="2026-01-15",
        customer_segment="small business",
        product_category="Analytics",
        research_goal="Understand setup friction.",
        session_id="report-session",
    )

    assert [item["report_id"] for item in matching] == [
        report["report_id"]
    ]

    assert read_model.list_reports(
        period_start="2026-02-01",
    ) == []
    assert read_model.list_reports(
        customer_segment="enterprise",
    ) == []


def test_storing_report_replaces_previous_version(report_db):
    first = build_session_report(SESSION, STATE)
    read_model.store_report(first)

    updated_state = deepcopy(STATE)
    updated_state["pain_points"]["setup"]["values"][0]["source_quote"] = (
        "Setup takes too long. It breaks daily work."
    )
    second = build_session_report(SESSION, updated_state)
    read_model.store_report(second)

    stored = read_model.get_report(second["report_id"])

    assert stored["claims"][0]["statement"] == (
        "Setup takes too long. It breaks daily work."
    )
    assert len(read_model.list_reports()) == 1


def test_aggregate_reports_groups_by_segment_category_and_period():
    first = build_session_report(SESSION, STATE)

    other_session = {
        **SESSION,
        "session_id": "other-report-session",
        "customer_segment": "enterprise",
        "product_category": "Hardware",
        "start_date": "2026-02-05",
        "end_date": "2026-02-10",
    }
    second = build_session_report(other_session, {
        **STATE,
        "research_session": other_session,
        "response_records": [
            {
                **STATE["response_records"][0],
                "response_id": "resp_2",
            }
        ],
        "pain_points": {
            "setup": {
                **STATE["pain_points"]["setup"],
                "values": [
                    {
                        **STATE["pain_points"]["setup"]["values"][0],
                        "evidence_id": "ev_2",
                        "response_id": "resp_2",
                    }
                ],
            }
        },
    })

    aggregate = aggregate_reports([first, second])

    assert aggregate["report_count"] == 2
    assert aggregate["claim_count"] == 2
    assert aggregate["evidence_count"] == 2
    assert aggregate["by_customer_segment"] == [
        {
            "value": "enterprise",
            "report_count": 1,
            "claim_count": 1,
            "evidence_count": 1,
        },
        {
            "value": "small business",
            "report_count": 1,
            "claim_count": 1,
            "evidence_count": 1,
        },
    ]
    assert aggregate["by_period"][0]["value"] == "2026-01"
    assert aggregate["by_period"][1]["value"] == "2026-02"
    assert aggregate["by_product_category"][0]["value"] == "Analytics"
    assert aggregate["by_claim_category"][0]["value"] == "pain_points"


def test_provenance_helper_adds_verbatim_quote_and_evidence_id():
    decomposition = {
        "summary": "Setup is slow.",
        "evidence": [
            {
                "category": "pain_points",
                "parameter": "setup",
                "value": "Setup takes too long.",
                "sentiment": "negative",
                "evidence_type": "explicit",
                "confidence": "high",
            }
        ],
        "ambiguity_detected": False,
        "potential_contradictions": [],
    }
    state = {
        "latest_response_id": "resp_1",
        "latest_response": RESPONSE,
        "latest_response_turn": 1,
        "latest_response_captured_at": "2026-01-12T10:00:00.000+00:00",
        "latest_participant_id": "participant-1",
    }

    grounded = attach_response_provenance(decomposition, state)
    evidence = grounded["evidence"][0]

    assert evidence["evidence_id"].startswith("ev_")
    assert evidence["response_id"] == "resp_1"
    assert evidence["source_quote"] == "Setup takes too long."
    assert evidence["source_quote"] in RESPONSE

    unchanged = attach_response_provenance(decomposition, {})
    assert unchanged == decomposition


def test_state_updater_preserves_provenance_on_evidence():
    state = {
        "meta_plan": {"required_information": []},
        "pain_points": None,
        "latest_decomposition": {
            "evidence": [
                {
                    "category": "pain_points",
                    "parameter": "setup",
                    "value": "setup takes too long",
                    "sentiment": "negative",
                    "evidence_type": "explicit",
                    "confidence": "high",
                    "evidence_id": "ev_1",
                    "response_id": "resp_1",
                    "turn": 1,
                    "captured_at": "2026-01-12T10:00:00.000+00:00",
                    "participant_id": "participant-1",
                    "source_quote": "Setup takes too long.",
                }
            ]
        },
    }

    result = update_state(state)
    evidence = result["pain_points"]["setup"]["values"][0]

    assert evidence["evidence_id"] == "ev_1"
    assert evidence["response_id"] == "resp_1"
    assert evidence["source_quote"] == "Setup takes too long."


def make_outcome(status, sequence, response_id):
    return {
        "outcome_id": f"outcome_{sequence}",
        "sequence": sequence,
        "session_id": SESSION["session_id"],
        "response_id": response_id,
        "status": status,
        "question": "What is frustrating?",
        "transcript": RESPONSE,
        "captured_at": "2026-01-12T10:00:00.000+00:00",
        "participant_id": "participant-1",
        "classification_source": "response_triage",
    }


def test_unfinished_report_labels_partial_evidence():
    state = deepcopy(STATE)
    state["interview_complete"] = False
    state.pop("termination_reason", None)
    state["response_outcomes"] = [
        make_outcome("valid", 1, "resp_1"),
    ]

    report = build_session_report(SESSION, state)
    dimensions = report["dimensions"]

    assert dimensions["status_label"] == "unfinished"
    assert dimensions["response_pattern_label"] == "continuous_valid"
    assert dimensions["tags"] == ["unfinished", "continuous_valid"]
    assert dimensions["response_outcome_count"] == 1
    assert report["summary"]["claim_count"] == 1
    assert report["response_outcomes"] == state["response_outcomes"]


def test_recovered_after_invalid_response_gets_separate_label():
    state = deepcopy(STATE)
    state["interview_complete"] = False
    state.pop("termination_reason", None)
    state["response_outcomes"] = [
        make_outcome("valid", 1, "resp_1"),
        make_outcome("unrelated", 2, "resp_invalid"),
        make_outcome("valid", 3, "resp_2"),
    ]

    report = build_session_report(SESSION, state)
    dimensions = report["dimensions"]

    assert dimensions["status_label"] == "unfinished"
    assert dimensions["response_pattern_label"] == (
        "recovered_after_invalid"
    )
    assert dimensions["tags"] == [
        "unfinished",
        "recovered_after_invalid",
    ]
    assert dimensions["valid_response_count"] == 2
    assert dimensions["invalid_response_count"] == 1
    assert dimensions["valid_before_invalid"] is True
    assert dimensions["valid_after_invalid"] is True


def test_invalid_sequence_without_recovery_is_not_labeled_recovered():
    state = deepcopy(STATE)
    state["response_outcomes"] = [
        make_outcome("valid", 1, "resp_1"),
        make_outcome("unclear", 2, "resp_invalid"),
    ]

    report = build_session_report(SESSION, state)

    assert report["dimensions"]["response_pattern_label"] == (
        "unresolved_invalid"
    )
    assert report["dimensions"]["valid_after_invalid"] is False


def test_legacy_state_without_outcomes_is_unclassified():
    report = build_session_report(SESSION, STATE)

    assert report["dimensions"]["response_pattern_label"] == (
        "unclassified"
    )
    assert report["dimensions"]["status_label"] == "completed"
    assert report["dimensions"]["tags"] == ["completed", "unclassified"]


def test_reports_can_be_filtered_by_status_pattern_and_tag(report_db):
    state = deepcopy(STATE)
    state["interview_complete"] = False
    state.pop("termination_reason", None)
    state["response_outcomes"] = [
        make_outcome("valid", 1, "resp_1"),
        make_outcome("unrelated", 2, "resp_invalid"),
        make_outcome("valid", 3, "resp_2"),
    ]
    report = build_session_report(SESSION, state)
    read_model.upsert_session(SESSION)
    read_model.store_report(report)

    assert read_model.list_reports(status_label="unfinished") == [report]
    assert read_model.list_reports(
        response_pattern_label="recovered_after_invalid",
    ) == [report]
    assert read_model.list_reports(tag="unfinished") == [report]
    assert read_model.list_reports(tag="recovered_after_invalid") == [
        report
    ]
    assert read_model.list_reports(status_label="completed") == []
    assert read_model.list_reports(tag="completed") == []


def test_aggregate_reports_groups_by_status_pattern_and_tag():
    state = deepcopy(STATE)
    state["interview_complete"] = False
    state.pop("termination_reason", None)
    state["response_outcomes"] = [
        make_outcome("valid", 1, "resp_1"),
        make_outcome("unrelated", 2, "resp_invalid"),
        make_outcome("valid", 3, "resp_2"),
    ]
    first = build_session_report(SESSION, state)

    other_session = {
        **SESSION,
        "session_id": "other-report-session",
        "customer_segment": "enterprise",
    }
    second = build_session_report(other_session, {
        **state,
        "research_session": other_session,
        "response_records": [
            {
                **STATE["response_records"][0],
                "response_id": "resp_2",
            }
        ],
        "pain_points": {
            "setup": {
                **STATE["pain_points"]["setup"],
                "values": [
                    {
                        **STATE["pain_points"]["setup"]["values"][0],
                        "evidence_id": "ev_2",
                        "response_id": "resp_2",
                    }
                ],
            }
        },
    })

    aggregate = aggregate_reports([first, second])

    assert aggregate["by_status_label"] == [
        {
            "value": "unfinished",
            "report_count": 2,
            "claim_count": 2,
            "evidence_count": 2,
        }
    ]
    assert aggregate["by_response_pattern_label"][0]["value"] == (
        "recovered_after_invalid"
    )
    assert [item["value"] for item in aggregate["by_tag"]] == [
        "recovered_after_invalid",
        "unfinished",
    ]
