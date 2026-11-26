import base64
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.analysis_report import build_session_report


ACCESS_CODE = "analysis-report-code"
SESSION_ID = "api-report-session"
RESPONSE = "Setup takes too long. It breaks daily work."

SESSION = {
    "session_id": SESSION_ID,
    "company_name": "Analytics Co",
    "product_name": "Insight Kit",
    "product_category": "Analytics",
    "customer_segment": "small business",
    "research_goal": "Understand setup friction.",
    "start_date": "2026-01-10",
    "end_date": "2026-01-20",
    "status": "completed",
}

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


@pytest.fixture(autouse=True)
def analysis_api_state(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "analysis-api.sqlite3")
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", ACCESS_CODE)

    yield

    read_model.configure()


def headers():
    token = base64.b64encode(
        f"researcher:{ACCESS_CODE}".encode("utf-8")
    ).decode("ascii")

    return {"Authorization": f"Basic {token}"}


def seed_report(monkeypatch=None):
    report = build_session_report(SESSION, STATE)
    read_model.upsert_session(SESSION)
    read_model.store_report(report)

    return report


def test_list_reports_filters_and_aggregates():
    report = seed_report()
    client = TestClient(api.app)

    response = client.get(
        "/researcher/analysis/reports",
        params={
            "period_start": "2026-01-15",
            "period_end": "2026-01-15",
            "customer_segment": "small business",
            "product_category": "Analytics",
            "research_goal": "Understand setup friction.",
            "session_id": SESSION_ID,
        },
        headers=headers(),
    )

    assert response.status_code == 200
    payload = response.json()

    assert [item["report_id"] for item in payload["reports"]] == [
        report["report_id"]
    ]
    assert payload["aggregate"]["report_count"] == 1
    assert payload["aggregate"]["by_customer_segment"][0]["value"] == (
        "small business"
    )

    excluded = client.get(
        "/researcher/analysis/reports",
        params={"customer_segment": "enterprise"},
        headers=headers(),
    )

    assert excluded.status_code == 200
    assert excluded.json()["reports"] == []


def test_export_reports_csv_and_require_researcher_access():
    report = seed_report()
    client = TestClient(api.app)

    unauthorized = client.get(
        "/researcher/analysis/reports/export.csv"
    )

    assert unauthorized.status_code == 401

    response = client.get(
        "/researcher/analysis/reports/export.csv",
        headers=headers(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "source_quote" in response.text
    assert report["claims"][0]["claim_id"] in response.text
    assert "Setup takes too long." in response.text


def test_generate_report_from_latest_snapshot():
    read_model.upsert_session(SESSION)
    read_model.record_snapshot(
        SESSION_ID,
        STATE,
        "response_processed",
        "completed",
    )
    client = TestClient(api.app)

    response = client.post(
        f"/researcher/analysis/reports/{SESSION_ID}/generate",
        headers=headers(),
    )

    assert response.status_code == 200
    report = response.json()
    assert report["validation"]["valid"] is True
    assert len(report["claims"]) == 1

    detail = client.get(
        f"/researcher/analysis/reports/{report['report_id']}",
        headers=headers(),
    )

    assert detail.status_code == 200
    assert detail.json()["session_id"] == SESSION_ID


def test_generate_report_rejects_ungrounded_state():
    ungrounded_state = deepcopy(STATE)
    ungrounded_state["pain_points"]["setup"]["values"][0][
        "response_id"
    ] = "resp_from_another_session"

    read_model.upsert_session(SESSION)
    read_model.record_snapshot(
        SESSION_ID,
        ungrounded_state,
        "response_processed",
        "completed",
    )
    client = TestClient(api.app)

    response = client.post(
        f"/researcher/analysis/reports/{SESSION_ID}/generate",
        headers=headers(),
    )

    assert response.status_code == 422
    assert "ungrounded" in response.json()["detail"]


def test_generate_report_returns_404_for_unknown_session():
    client = TestClient(api.app)

    response = client.post(
        "/researcher/analysis/reports/missing/generate",
        headers=headers(),
    )

    assert response.status_code == 404


def api_outcome(status, sequence, response_id):
    return {
        "outcome_id": f"outcome_{sequence}",
        "sequence": sequence,
        "session_id": SESSION_ID,
        "response_id": response_id,
        "status": status,
        "question": "What is frustrating?",
        "transcript": RESPONSE,
        "captured_at": "2026-01-12T10:00:00.000+00:00",
        "participant_id": "participant-1",
        "classification_source": "response_triage",
    }


def test_reports_endpoint_filters_unfinished_and_recovery_labels():
    state = deepcopy(STATE)
    state["interview_complete"] = False
    state.pop("termination_reason", None)
    state["response_outcomes"] = [
        api_outcome("valid", 1, "resp_1"),
        api_outcome("unrelated", 2, "resp_invalid"),
        api_outcome("valid", 3, "resp_2"),
    ]
    report = build_session_report(SESSION, state)
    read_model.upsert_session(SESSION)
    read_model.store_report(report)
    client = TestClient(api.app)

    response = client.get(
        "/researcher/analysis/reports",
        params={
            "status_label": "unfinished",
            "response_pattern_label": "recovered_after_invalid",
            "tag": "unfinished",
        },
        headers=headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["report_id"] for item in payload["reports"]] == [
        report["report_id"]
    ]
    assert payload["aggregate"]["by_status_label"][0]["value"] == (
        "unfinished"
    )
    assert payload["aggregate"]["by_tag"][0]["value"] == (
        "recovered_after_invalid"
    )

    excluded = client.get(
        "/researcher/analysis/reports",
        params={"status_label": "completed"},
        headers=headers(),
    )

    assert excluded.json()["reports"] == []


def test_persisting_unfinished_interview_stores_then_replaces_report():
    read_model.upsert_session(SESSION)
    state = deepcopy(STATE)
    state["interview_complete"] = False
    state.pop("termination_reason", None)
    state["response_outcomes"] = [
        api_outcome("valid", 1, "resp_1"),
        api_outcome("unrelated", 2, "resp_invalid"),
        api_outcome("valid", 3, "resp_2"),
    ]

    api.persist_graph_state(SESSION_ID, state, "response_processed")
    unfinished = read_model.list_reports()

    assert len(unfinished) == 1
    assert unfinished[0]["dimensions"]["status_label"] == "unfinished"
    assert unfinished[0]["dimensions"]["response_pattern_label"] == (
        "recovered_after_invalid"
    )
    assert unfinished[0]["dimensions"]["tags"] == [
        "unfinished",
        "recovered_after_invalid",
    ]

    completed_state = {**state, "interview_complete": True}
    api.persist_graph_state(
        SESSION_ID,
        completed_state,
        "response_processed",
    )
    completed = read_model.list_reports()

    assert len(completed) == 1
    assert completed[0]["dimensions"]["status_label"] == "completed"
    assert completed[0]["dimensions"]["response_pattern_label"] == (
        "recovered_after_invalid"
    )
