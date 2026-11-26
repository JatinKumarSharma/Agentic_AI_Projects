import base64
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import api
from agent import analysis_report, read_model


ACCESS_CODE = "memory-report-code"
SESSION_ID = "memory-report-session"
TENANT = "memory-tenant"
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
def memory_report_db(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "memory-report.sqlite3")
    monkeypatch.setenv("CVI_TENANT_ID", TENANT)
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", ACCESS_CODE)

    yield

    read_model.configure()


def seed_snapshot(state=None):
    read_model.upsert_session(SESSION)
    read_model.record_snapshot(
        SESSION_ID,
        state or STATE,
        "response_processed",
        "completed",
    )


def auth_headers():
    token = base64.b64encode(
        f"researcher:{ACCESS_CODE}".encode("utf-8")
    ).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_memory_report_builds_from_authoritative_snapshot(monkeypatch):
    seed_snapshot()

    def fail_live_builder(*args, **kwargs):
        raise AssertionError("memory report must not use live-state builder")

    monkeypatch.setattr(
        analysis_report,
        "build_session_report",
        fail_live_builder,
    )
    monkeypatch.setattr(
        analysis_report,
        "_flatten_knowledge_items",
        fail_live_builder,
    )

    report = analysis_report.build_session_report_from_memory(
        SESSION_ID,
        tenant_id=TENANT,
    )

    assert report["session_id"] == SESSION_ID
    assert report["validation"]["valid"] is True
    assert len(report["claims"]) == 1
    assert report["claims"][0]["evidence_ids"] == ["ev_1"]
    assert report["claims"][0]["statement"] == "Setup takes too long."
    assert report["response_evidence"][0]["response_id"] == "resp_1"


def test_memory_report_rejects_suppressed_ungrounded_evidence():
    state = deepcopy(STATE)
    state["pain_points"]["setup"]["values"][0]["source_quote"] = (
        "This line was never spoken."
    )
    seed_snapshot(state)

    with pytest.raises(ValueError, match="ungrounded"):
        analysis_report.build_session_report_from_memory(
            SESSION_ID,
            tenant_id=TENANT,
        )


def test_memory_report_requires_an_authoritative_snapshot():
    read_model.upsert_session(SESSION)

    with pytest.raises(ValueError, match="authoritative memory snapshot"):
        analysis_report.build_session_report_from_memory(
            SESSION_ID,
            tenant_id=TENANT,
        )


def test_generate_report_endpoint_uses_authoritative_memory(monkeypatch):
    seed_snapshot()

    def fail_live_builder(*args, **kwargs):
        raise AssertionError("generate endpoint must use authoritative memory")

    monkeypatch.setattr(
        analysis_report,
        "build_session_report",
        fail_live_builder,
    )

    client = TestClient(api.app)
    response = client.post(
        f"/researcher/analysis/reports/{SESSION_ID}/generate",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    report = response.json()
    assert report["validation"]["valid"] is True
    assert report["claims"][0]["evidence_ids"] == ["ev_1"]
