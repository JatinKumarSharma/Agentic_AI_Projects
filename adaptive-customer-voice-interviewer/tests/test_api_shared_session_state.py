from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.session import create_research_session


SESSION_ID = "shared-session"


@pytest.fixture
def durable_session(tmp_path):
    read_model.configure(tmp_path / "shared-session.sqlite3")

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
    }
    read_model.upsert_session(session)

    api.sessions.clear()
    api.pending_interrupts.clear()
    api.response_jobs.clear()
    api.voice_output_queues.clear()
    api.voice_output_events.clear()

    yield session

    api.sessions.clear()
    api.pending_interrupts.clear()
    read_model.configure()


def test_status_finds_durable_session_without_in_memory_cache(durable_session):
    client = TestClient(api.app)

    response = client.get(f"/sessions/{SESSION_ID}/status")

    assert response.status_code == 200
    assert response.json()["status"] == "idle"


def test_voice_events_find_durable_session_without_in_memory_cache(
    durable_session,
):
    client = TestClient(api.app)

    response = client.get(f"/sessions/{SESSION_ID}/voice-events")

    assert response.status_code == 200
    assert response.json()["events"] == []


def test_start_checks_durable_pending_interrupt(durable_session, monkeypatch):
    monkeypatch.setattr(api, "WORKER_ENABLED", True)
    api.repository.save_pending_action(
        session_id=SESSION_ID,
        payload={"type": "deliver_question"},
    )

    client = TestClient(api.app)

    response = client.post(f"/sessions/{SESSION_ID}/start")

    assert response.status_code == 409
    assert response.json()["detail"] == "This interview has already started."
