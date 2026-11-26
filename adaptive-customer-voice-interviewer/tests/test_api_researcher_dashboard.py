import base64

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api
from agent import read_model


SESSION_ID = "researcher-session"
OTHER_SESSION_ID = "other-researcher-session"

SESSION = {
    "session_id": SESSION_ID,
    "company_name": "Test Company",
    "product_name": "Test Product",
    "product_category": "Consumer Software",
    "research_goal": "Understand product satisfaction.",
    "start_date": "2026-11-21",
    "status": "active",
}

OTHER_SESSION = {
    "session_id": OTHER_SESSION_ID,
    "company_name": "Other Company",
    "product_name": "Other Product",
    "product_category": "Hardware",
    "research_goal": "Understand purchase intent.",
    "start_date": "2026-11-21",
    "status": "active",
}

STATE = {
    "research_context": {
        "company_name": "Test Company",
        "product_name": "Test Product",
        "product_category": "Consumer Software",
        "research_goal": "Understand product satisfaction.",
    },
    "meta_plan": {
        "research_goal": "Understand product satisfaction.",
    },
    "questions_asked": [
        "How satisfied are you?",
        "What frustrates you?",
    ],
    "responses": [
        "Very satisfied with the camera.",
        "Battery life is frustrating.",
    ],
    "product_relationship": {
        "values": [
            {
                "value": "using the product daily",
                "source": "consumer_response",
                "confidence": "high",
                "sentiment": None,
                "evidence_type": "explicit",
            }
        ],
        "sufficiency": "basic",
        "confidence": "high",
        "criticality": "high",
        "verification_status": "unverified",
        "applicability_status": "applicable",
    },
    "pain_points": {
        "battery": {
            "values": [
                {
                    "value": "battery life is frustrating",
                    "source": "consumer_response",
                    "confidence": "high",
                    "sentiment": "negative",
                    "evidence_type": "explicit",
                }
            ],
            "sufficiency": "basic",
            "confidence": "high",
            "criticality": "critical",
            "verification_status": "unverified",
            "applicability_status": "applicable",
        }
    },
    "unresolved_gaps": [
        {
            "parameter": "Purchase intent",
            "category": "purchase_intent",
            "gap_type": "missing",
            "importance": "high",
            "goal_alignment": "high",
            "criticality": "high",
        }
    ],
    "information_sufficiency": {
        "pain_points": "basic",
    },
    "confidence": {
        "pain_points": "high",
    },
    "verification_status": {
        "pain_points": "unverified",
    },
    "iteration_count": 2,
    "selected_proposal": {
        "proposal_id": "proposal-1",
        "question": "How likely are you to renew?",
    },
    "reflection": {
        "sufficiency_change": "increased",
        "confidence_change": "unchanged",
        "remaining_uncertainty": "medium",
        "additional_question_value": "high",
    },
    "final_insights": None,
    "state_history": [{}, {}],
}


@pytest.fixture(autouse=True)
def reset_researcher_state(tmp_path):
    read_model.configure(tmp_path / "researcher.sqlite3")

    api.sessions.clear()
    api.pending_interrupts.clear()
    api.response_jobs.clear()
    api.voice_output_queues.clear()
    api.voice_output_events.clear()

    read_model.upsert_session(SESSION)
    read_model.upsert_session(OTHER_SESSION)

    read_model.record_snapshot(
        OTHER_SESSION_ID,
        {},
        "session_created",
        "active",
    )
    read_model.record_snapshot(
        SESSION_ID,
        STATE,
        "response_processed",
        "active",
    )
    read_model.record_event(
        SESSION_ID,
        "response_submitted",
        {"transcript": "Battery life is frustrating."},
    )
    read_model.record_timing(
        session_id=SESSION_ID,
        turn=2,
        span_name="response_processing",
        started_ms=100.0,
        ended_ms=275.0,
        duration_ms=175.0,
        source="runtime",
        metadata={"status": "completed"},
    )

    yield

    read_model.configure()


def test_list_researcher_sessions_exposes_grouping_and_expiry_fields():
    enriched = {
        **SESSION,
        "customer_segment": "existing customer",
        "link_expiry": "custom",
        "link_expires_at": "2026-12-01T09:30:00.000+00:00",
    }
    read_model.upsert_session(enriched)

    result = api.list_researcher_sessions()
    summary = next(
        item
        for item in result["sessions"]
        if item["session_id"] == SESSION_ID
    )

    assert summary["customer_segment"] == "existing customer"
    assert summary["link_expiry"] == "custom"
    assert summary["link_expires_at"] == (
        "2026-12-01T09:30:00.000+00:00"
    )
    assert summary["created_at"]


def test_list_researcher_sessions_summarizes_persisted_state():
    api.pending_interrupts[SESSION_ID] = {
        "type": "await_consumer_response",
        "question": "What do you think?",
    }
    api.persist_graph_state(
        SESSION_ID,
        STATE,
        "response_processed",
    )

    result = api.list_researcher_sessions()
    summaries = {
        item["session_id"]: item
        for item in result["sessions"]
    }

    assert set(summaries) == {SESSION_ID, OTHER_SESSION_ID}

    summary = summaries[SESSION_ID]
    assert summary["activity"] == "awaiting_response"
    assert summary["company_name"] == "Test Company"
    assert summary["product_name"] == "Test Product"
    assert summary["research_goal"] == (
        "Understand product satisfaction."
    )
    assert summary["iteration_count"] == 2
    assert summary["questions_asked_count"] == 2
    assert summary["responses_count"] == 2
    assert summary["evidence_count"] == 2
    assert summary["unresolved_gaps_count"] == 1
    assert summary["has_final_insights"] is False

    assert summaries[OTHER_SESSION_ID]["activity"] == "active"
    assert summaries[OTHER_SESSION_ID]["evidence_count"] == 0


def test_get_researcher_session_returns_traceable_structured_view():
    result = api.get_researcher_session(SESSION_ID)

    assert result["checkpoint_found"] is True
    assert result["iteration_count"] == 2
    assert result["session"]["activity"] == "active"
    assert result["research_context"]["product_name"] == (
        "Test Product"
    )
    assert result["meta_plan"] == {
        "research_goal": "Understand product satisfaction."
    }

    assert result["conversation"] == [
        {
            "turn": 1,
            "question": "How satisfied are you?",
            "response": "Very satisfied with the camera.",
        },
        {
            "turn": 2,
            "question": "What frustrates you?",
            "response": "Battery life is frustrating.",
        },
    ]

    knowledge = {
        (item["category"], item["parameter"]): item
        for item in result["knowledge"]
    }

    assert set(knowledge) == {
        ("product_relationship", "product_relationship"),
        ("pain_points", "battery"),
    }

    battery = knowledge[("pain_points", "battery")]
    assert battery["criticality"] == "critical"
    assert battery["values"][0]["value"] == (
        "battery life is frustrating"
    )
    assert battery["values"][0]["source"] == "consumer_response"

    assert result["gaps"][0]["parameter"] == "Purchase intent"
    assert result["quality"]["information_sufficiency"] == {
        "pain_points": "basic"
    }
    assert result["selected_proposal"]["proposal_id"] == (
        "proposal-1"
    )
    assert result["reflection"]["remaining_uncertainty"] == (
        "medium"
    )
    assert result["final_insights"] is None
    assert result["state_history_count"] == 2

    assert result["snapshots"][0]["source"] == "response_processed"
    assert result["snapshot"]["source"] == "response_processed"
    assert result["events"][0]["event_type"] == "response_submitted"
    assert result["timings"][0]["span_name"] == "response_processing"
    assert result["timings"][0]["duration_ms"] == 175.0


def test_get_researcher_session_returns_404_for_unknown_session():
    with pytest.raises(HTTPException) as error:
        api.get_researcher_session("missing-session")

    assert error.value.status_code == 404


def test_researcher_dashboard_static_view_is_served(monkeypatch):
    access_code = "dashboard-test-code"
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", access_code)

    token = base64.b64encode(
        f"researcher:{access_code}".encode("utf-8")
    ).decode("ascii")
    headers = {"Authorization": f"Basic {token}"}

    client = TestClient(api.app)

    response = client.get("/researcher", headers=headers)

    assert response.status_code == 200
    assert "Research dashboard" in response.text
    assert "/researcher.js" in response.text
    assert 'id="themeToggle"' in response.text
    assert 'src="/theme.js"' in response.text
    assert 'id="reportPanel"' in response.text
    assert 'id="reportFilterForm"' in response.text
    assert 'id="reportStatus"' in response.text
    assert 'id="reportPattern"' in response.text
    assert 'id="reportList"' in response.text
    assert 'id="sessionReportSummary"' in response.text
    assert 'id="timingList"' in response.text

    # Three-workspace researcher UI.
    assert 'id="createPanel"' in response.text
    assert 'id="sessionsView"' in response.text
    assert 'id="analysisView"' in response.text
    assert 'id="insightsPlaceholder"' in response.text
    assert 'id="createSessionForm"' in response.text
    assert 'id="researchGoal"' in response.text
    assert 'id="linkExpiry"' in response.text
    assert 'id="expiryDateTime"' in response.text
    assert 'id="sessionGroupBy"' in response.text
    assert 'id="sessionSortBy"' in response.text
    assert 'id="sessionSortOrder"' in response.text
    assert 'id="sessionDetailPanel"' in response.text

    # Create-form placeholders are muted examples, never sample user values.
    for placeholder in (
        'placeholder="e.g. acme"',
        'placeholder="e.g. Acme Technologies"',
        'placeholder="e.g. Insight Kit"',
        'placeholder="e.g. existing customer"',
        'placeholder="e.g. Understand setup friction with onboarding."',
        'placeholder="e.g. Existing customers"',
        'placeholder="e.g. participant-42"',
        'placeholder="e.g. keep answers under 2 minutes"',
        'placeholder="e.g. +05:45"',
    ):
        assert placeholder in response.text

    style_response = client.get("/style.css")

    assert style_response.status_code == 200
    assert "--placeholder:" in style_response.text
    assert "color: var(--placeholder)" in style_response.text
    assert "opacity: 1" in style_response.text

    script_response = client.get("/researcher.js", headers=headers)

    assert script_response.status_code == 200
    assert "loadSessionDetail" in script_response.text
    assert "loadReports" in script_response.text
    assert "renderClaimLedger" in script_response.text
    assert "renderTimings" in script_response.text
    assert "duration_ms" in script_response.text
    assert "response_pattern_label" in script_response.text
    assert "showView" in script_response.text
    assert "sessionGroupKey" in script_response.text
    assert "compareSessions" in script_response.text
    assert "renderSessionFacts" in script_response.text
    assert "buildLinkExpiry" in script_response.text
