from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.participant_token import hash_participant_token
from agent.session import create_research_session


SESSION_ID = "participant-capability-session"
PARTICIPANT_TOKEN = "capability-token-with-high-entropy"
HEADERS = {"X-CVI-Participant-Token": PARTICIPANT_TOKEN}


class WaitingGraph:
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


@pytest.fixture
def participant_session(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "participant-capability.sqlite3")

    api.sessions.clear()
    api.pending_interrupts.clear()
    api.response_jobs.clear()
    api.session_locks.clear()
    api.voice_output_queues.clear()
    api.voice_output_events.clear()
    api.pending_engagement_tasks.clear()

    monkeypatch.setattr(api, "WORKER_ENABLED", False)
    monkeypatch.setattr(
        api,
        "persist_graph_state",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(api, "graph", WaitingGraph())

    session = {
        **create_research_session(
            company_id="acme",
            company_name="Acme Technologies",
            product_id="insight-kit",
            product_name="Insight Kit",
            product_category="Analytics",
            research_goal="Understand setup friction.",
            start_date=datetime.now(timezone.utc).date(),
        ),
        "session_id": SESSION_ID,
        "status": "active",
        "participant_token_hash": hash_participant_token(PARTICIPANT_TOKEN),
    }

    read_model.upsert_session(session)
    api.sessions[SESSION_ID] = session

    yield session

    api.sessions.clear()
    api.pending_interrupts.clear()
    read_model.configure()


def test_invitation_requires_participant_capability(participant_session):
    client = TestClient(api.app)

    denied = client.get(f"/sessions/{SESSION_ID}/invitation")

    assert denied.status_code == 401
    assert "participant link" in denied.json()["detail"].lower()

    allowed = client.get(
        f"/sessions/{SESSION_ID}/invitation",
        headers=HEADERS,
    )

    assert allowed.status_code == 200
    assert allowed.json()["session_id"] == SESSION_ID


def test_invitation_accepts_capability_from_query_string(participant_session):
    client = TestClient(api.app)

    response = client.get(
        f"/sessions/{SESSION_ID}/invitation",
        params={"participant_token": PARTICIPANT_TOKEN},
    )

    assert response.status_code == 200


def test_wrong_participant_capability_is_denied(participant_session):
    client = TestClient(api.app)

    response = client.get(
        f"/sessions/{SESSION_ID}/invitation",
        headers={"X-CVI-Participant-Token": "wrong-token"},
    )

    assert response.status_code == 401


def test_start_requires_participant_capability(participant_session):
    client = TestClient(api.app)

    denied = client.post(f"/sessions/{SESSION_ID}/start")

    assert denied.status_code == 401

    allowed = client.post(
        f"/sessions/{SESSION_ID}/start",
        headers=HEADERS,
    )

    assert allowed.status_code == 200
    assert allowed.json()["status"] == "waiting"


def test_legacy_session_without_capability_still_works(
    participant_session,
):
    legacy = {
        **participant_session,
        "participant_token_hash": None,
    }

    read_model.upsert_session(legacy)
    api.sessions[SESSION_ID] = legacy

    client = TestClient(api.app)

    response = client.get(f"/sessions/{SESSION_ID}/invitation")

    assert response.status_code == 200


PROTECTED_ENDPOINTS = [
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


@pytest.mark.parametrize("method,path,body", PROTECTED_ENDPOINTS)
def test_participant_endpoints_reject_missing_capability(
    participant_session,
    method,
    path,
    body,
):
    client = TestClient(api.app)

    response = client.request(
        method,
        f"/sessions/{SESSION_ID}{path}",
        json=body,
    )

    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/voice", "/voice/local"])
def test_participant_websockets_reject_missing_capability(
    participant_session,
    path,
):
    with TestClient(api.app) as client:
        with client.websocket_connect(f"/sessions/{SESSION_ID}{path}") as ws:
            message = ws.receive_json()

    assert message["type"] == "error"
    assert "participant link" in message["message"].lower()


@pytest.mark.parametrize("path", ["/voice", "/voice/local"])
def test_participant_websockets_accept_valid_capability(
    participant_session,
    path,
):
    with TestClient(api.app) as client:
        with client.websocket_connect(
            f"/sessions/{SESSION_ID}{path}"
            f"?participant_token={PARTICIPANT_TOKEN}"
        ) as ws:
            message = ws.receive_json()

    # A valid capability gets past authorization. These tests do not provide
    # an awaiting response interrupt, so the next message is the normal state
    # guard rather than an authorization failure.
    assert message["type"] == "error"
    assert "waiting for a response" in message["message"].lower()
