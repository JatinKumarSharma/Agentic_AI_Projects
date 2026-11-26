import base64
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.session import create_research_session


ACCESS_CODE = "link-expiry-code"

SESSION_IDS = (
    "expired-invitation-session",
    "expired-start-session",
    "started-session",
)


@pytest.fixture(autouse=True)
def researcher_access(monkeypatch):
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", ACCESS_CODE)


def researcher_headers():
    token = base64.b64encode(
        f"researcher:{ACCESS_CODE}".encode("utf-8")
    ).decode("ascii")

    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def link_expiry_state(tmp_path):
    read_model.configure(tmp_path / "link-expiry.sqlite3")

    for session_id in SESSION_IDS:
        api.sessions.pop(session_id, None)

    yield

    for session_id in SESSION_IDS:
        api.sessions.pop(session_id, None)
    read_model.configure()


def store_session(session):
    read_model.upsert_session(session)
    api.sessions[session["session_id"]] = session
    return session


def base_session(session_id):
    session = create_research_session(
        company_id="acme",
        company_name="Acme Technologies",
        product_id="insight-kit",
        product_name="Insight Kit",
        product_category="Analytics",
        research_goal="Understand setup friction.",
        start_date=datetime.now(timezone.utc).date(),
    )
    return {**session, "session_id": session_id, "status": "active"}


def test_create_session_accepts_a_preset_expiry():
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
            "link_expiry": "3h",
        },
        headers=researcher_headers(),
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["link_expiry"] == "3h"

    expires_at = datetime.fromisoformat(payload["link_expires_at"])
    assert expires_at.tzinfo is not None
    assert expires_at > datetime.now(timezone.utc)

    api.sessions.pop(payload["session_id"], None)


def test_create_session_converts_custom_timezone_to_utc():
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
            "link_expiry": "2026-11-22T15:00:00+05:30",
        },
        headers=researcher_headers(),
    )

    assert response.status_code == 201
    payload = response.json()

    assert payload["link_expiry"] == "custom"
    assert payload["link_expires_at"] == "2026-11-22T09:30:00.000+00:00"

    api.sessions.pop(payload["session_id"], None)


def test_create_session_rejects_naive_custom_expiry():
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
            "link_expiry": "2026-11-22T15:00:00",
        },
        headers=researcher_headers(),
    )

    assert response.status_code == 422
    assert "timezone" in response.json()["detail"]


def test_invitation_rejects_an_expired_link():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    session = store_session(
        {
            **base_session(SESSION_IDS[0]),
            "link_expiry": "1h",
            "link_expires_at": past,
        }
    )
    client = TestClient(api.app)

    response = client.get(
        f"/sessions/{session['session_id']}/invitation"
    )

    assert response.status_code == 410
    assert "expired" in response.json()["detail"].lower()


def test_start_rejects_an_expired_link():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    session = store_session(
        {
            **base_session(SESSION_IDS[1]),
            "link_expiry": "1h",
            "link_expires_at": past,
        }
    )
    client = TestClient(api.app)

    response = client.post(f"/sessions/{session['session_id']}/start")

    assert response.status_code == 410
    assert "expired" in response.json()["detail"].lower()


def test_started_interview_can_reopen_after_the_deadline():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    session = store_session(
        {
            **base_session(SESSION_IDS[2]),
            "link_expiry": "1h",
            "link_expires_at": past,
            "interview_started_at": (
                datetime.now(timezone.utc) - timedelta(minutes=30)
            ).isoformat(),
        }
    )
    client = TestClient(api.app)

    response = client.get(
        f"/sessions/{session['session_id']}/invitation"
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == session["session_id"]


EXPIRY_PROTECTED_ENDPOINTS = [
    ("GET", "/invitation", None),
    ("POST", "/voice/transcribe", {"audio_base64": "ZmFrZQ=="}),
    ("POST", "/voice/synthesize", {"text": "Hello?"}),
    (
        "POST",
        "/timings",
        {
            "turn": 1,
            "span_name": "probe",
            "started_ms": 1.0,
            "ended_ms": 2.0,
        },
    ),
    ("POST", "/start", None),
    ("POST", "/advance", None),
    ("POST", "/responses", {"transcript": "A valid answer."}),
    ("GET", "/status", None),
    ("GET", "/voice-events", None),
    ("POST", "/voice-events/ack", {"event_ids": ["voice_test"]}),
]


@pytest.fixture
def expired_session():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return store_session(
        {
            **base_session("expiry-all-endpoints-session"),
            "link_expiry": "1h",
            "link_expires_at": past,
        }
    )


@pytest.mark.parametrize("method,path,body", EXPIRY_PROTECTED_ENDPOINTS)
def test_all_participant_endpoints_reject_expired_links(
    expired_session,
    method,
    path,
    body,
):
    client = TestClient(api.app)

    response = client.request(
        method,
        f"/sessions/{expired_session['session_id']}{path}",
        json=body,
    )

    assert response.status_code == 410
    assert "expired" in response.json()["detail"].lower()


@pytest.mark.parametrize("path", ["/voice", "/voice/local"])
def test_participant_websockets_reject_expired_links(expired_session, path):
    with TestClient(api.app) as client:
        with client.websocket_connect(
            f"/sessions/{expired_session['session_id']}{path}"
        ) as websocket:
            message = websocket.receive_json()

    assert message["type"] == "error"
    assert "expired" in message["message"].lower()
