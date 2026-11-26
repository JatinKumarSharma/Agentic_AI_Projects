import base64
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model


ACCESS_CODE = "participant-view-code"

SESSION = {
    "session_id": "invitation-session",
    "company_name": "Acme Technologies",
    "company_id": "acme",
    "product_name": "Insight Kit",
    "product_id": "insight-kit",
    "product_category": "Analytics",
    "research_goal": "Understand setup friction.",
    "customer_segment": "existing customer",
    "start_date": "2026-01-10",
    "end_date": "2026-01-20",
    "status": "active",
}


@pytest.fixture(autouse=True)
def researcher_access(monkeypatch):
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", ACCESS_CODE)


def researcher_headers():
    token = base64.b64encode(
        f"researcher:{ACCESS_CODE}".encode("utf-8")
    ).decode("ascii")

    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def participant_view_state(tmp_path):
    read_model.configure(tmp_path / "participant-view.sqlite3")
    api.sessions.pop(SESSION["session_id"], None)

    yield

    read_model.configure()
    api.sessions.pop(SESSION["session_id"], None)


def test_participant_view_exposes_live_feedback_surfaces():
    client = TestClient(api.app)

    response = client.get("/")

    assert response.status_code == 200

    for element_id in (
        "feedbackStrip",
        "feedbackLabel",
        "feedbackText",
        "recoveryNotice",
        "recoveryTitle",
        "recoveryMessage",
        "conversationRail",
        "activityEmpty",
        "activityList",
        "themeToggle",
    ):
        assert f'id="{element_id}"' in response.text

    assert 'class="participant-workspace"' in response.text
    assert 'src="/theme.js"' in response.text


def test_participant_script_wires_unified_feedback_path():
    client = TestClient(api.app)

    response = client.get("/app.js")

    assert response.status_code == 200

    for function_name in (
        "enqueueVoiceFeedback",
        "waitForVoiceFeedback",
        "handleVoiceOutputEvent",
        "showRecoveryNotice",
        "recordFrontendTiming",
        "markAcknowledgementRendered",
        "markContextualEngagementRendered",
        "markResponseTurnStarted",
        "voiceDelivery",
        "voiceSttProvider",
        "browserSpeak",
        "playRemoteAudio",
        "startBatchVoiceRecording",
        "submitRecordedAudio",
        "encodePcm16Wav",
    ):
        assert function_name in response.text

    assert "immediate_acknowledgement" in response.text
    assert "contextual_engagement" in response.text
    assert "voiceResult.pending === 0" in response.text
    assert "await waitForVoiceFeedback();" in response.text

    for span_name in (
        "invitation_resolved",
        "permission_check_completed",
        "first_question_rendered",
        "first_question_advanced_to_listening",
        "participant_response_time",
        "speech_end_acknowledgement_rendered",
        "contextual_engagement_rendered",
        "next_question_rendered",
    ):
        assert span_name in response.text

    assert "/timings" in response.text
    assert "/voice/synthesize" in response.text
    assert "/voice/transcribe" in response.text
    assert "failure_detail" in response.text
    assert "event.delivery" in response.text
    assert "voice_profile" in response.text

    theme_response = client.get("/theme.js")

    assert theme_response.status_code == 200
    assert 'STORAGE_KEY = "cvi-theme"' in theme_response.text


def test_participant_view_has_no_research_setup_form():
    client = TestClient(api.app)
    response = client.get("/")

    assert response.status_code == 200

    for removed_marker in (
        "sessionForm",
        "Set up your research",
        'name="company_name"',
        'name="research_goal"',
        "createSessionButton",
    ):
        assert removed_marker not in response.text

    for element_id in (
        "permissionPanel",
        "permissionButton",
        "compatibilityList",
        "welcomePanel",
        "welcomeMessage",
        "beginInterviewButton",
    ):
        assert f'id="{element_id}"' in response.text

    script_response = client.get("/app.js")

    for function_name in (
        "loadInvitation",
        "runCompatibilityCheck",
        "beginInterview",
    ):
        assert function_name in script_response.text

    assert "sessionForm" not in script_response.text
    assert "session_id" in script_response.text
    assert "answerGuidance" in script_response.text
    assert "Voice input is unavailable" in script_response.text


def test_voice_capture_is_automatic_with_manual_backup():
    client = TestClient(api.app)

    script_response = client.get("/app.js")

    for marker in (
        "armVoiceAnswerDetection",
        "automaticRecording",
        "autoSpeechStartedAt",
        "AUTO_SILENCE_MS",
        "startVoiceRecording(true)",
        "Voice input is unavailable. Type your answer below.",
        "Start speaking",
        "Submit response",
    ):
        assert marker in script_response.text

    html_response = client.get("/")

    assert "Listening automatically" in html_response.text


def test_setup_check_starts_automatically_and_begin_button_is_themed():
    client = TestClient(api.app)

    script_response = client.get("/app.js")

    assert "await runCompatibilityCheck()" in script_response.text
    assert "automatically" in script_response.text
    assert "Recheck setup" in script_response.text

    style_response = client.get("/style.css")

    assert "#beginInterviewButton" in style_response.text
    assert "background: var(--accent)" in style_response.text
    assert "color: var(--accent-contrast)" in style_response.text


def test_first_question_loading_state_is_visible_and_errors_are_explicit():
    client = TestClient(api.app)

    html_response = client.get("/")
    script_response = client.get("/app.js")
    style_response = client.get("/style.css")

    assert 'id="questionLoading"' in html_response.text
    assert "Preparing your first question" in html_response.text

    assert "showQuestionLoading" in script_response.text
    assert "hideQuestionLoading" in script_response.text
    assert "Something needs attention." not in script_response.text

    assert ".question-loading" in style_response.text
    assert "@keyframes spin" in style_response.text


def test_invitation_endpoint_uses_stored_configuration():
    read_model.upsert_session(SESSION)
    client = TestClient(api.app)

    response = client.get(
        f"/sessions/{SESSION['session_id']}/invitation"
    )

    assert response.status_code == 200
    payload = response.json()

    assert payload["session_id"] == SESSION["session_id"]
    assert payload["company_name"] == "Acme Technologies"
    assert payload["product_name"] == "Insight Kit"
    assert "Insight Kit" in payload["welcome_message"]
    assert "research_goal" not in payload
    assert "customer_segment" not in payload

    events = read_model.list_events(SESSION["session_id"])
    assert any(
        event["event_type"] == "participant_invitation_opened"
        for event in events
    )

    # The link works even when the in-memory registry was not populated.
    assert api.sessions[SESSION["session_id"]] == read_model.get_session(
        SESSION["session_id"]
    )


def test_invitation_endpoint_rejects_unknown_session():
    client = TestClient(api.app)

    response = client.get("/sessions/missing-session/invitation")

    assert response.status_code == 404
    assert "fresh interview link" in response.json()["detail"]


def test_create_session_returns_participant_link():
    client = TestClient(api.app)

    response = client.post(
        "/sessions",
        json={
            "company_id": "acme",
            "company_name": "Acme Technologies",
            "product_id": "insight-kit",
            "product_name": "Insight Kit",
            "product_category": "Analytics",
            "research_goal": "Understand setup friction.",
            "target_audience": "Existing customers",
            "customer_segment": "existing customer",
        },
        headers=researcher_headers(),
    )

    assert response.status_code == 201
    payload = response.json()

    url = urlparse(payload["participant_url"])
    params = parse_qs(url.query)

    assert url.path == "/"
    assert params["session_id"] == [payload["session_id"]]
    assert params["participant_token"]

    stored = read_model.get_session(payload["session_id"])
    assert stored["participant_token_hash"]
    assert params["participant_token"][0] not in str(stored)
