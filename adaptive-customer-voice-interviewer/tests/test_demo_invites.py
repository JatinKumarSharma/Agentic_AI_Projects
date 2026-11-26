from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.participant_token import hash_participant_token
from agent.session import create_research_session


DEMO_SLUG = "portfolio-demo"


def _make_demo_session(session_id, token=None):
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
        "session_id": session_id,
        "status": "active",
        "tenant_id": api.current_tenant_id(),
        "demo_slug": DEMO_SLUG,
    }

    if token:
        session["participant_token_hash"] = hash_participant_token(token)

    read_model.upsert_session(session)
    return session


@pytest.fixture
def demo_pool(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "demo-invites.sqlite3")

    api.sessions.clear()
    api.session_locks.clear()
    api.voice_output_queues.clear()
    api.voice_output_events.clear()
    api.pending_engagement_tasks.clear()
    monkeypatch.setattr(api, "WORKER_ENABLED", False)

    yield

    api.sessions.clear()
    api.session_locks.clear()
    read_model.configure()


def test_demo_invite_issues_short_lived_capability_without_public_url(demo_pool):
    session = _make_demo_session("demo-rotate-session", "old-capability")

    client = TestClient(api.app)
    response = client.post(f"/demo/invites/{DEMO_SLUG}")

    assert response.status_code == 201
    body = response.json()

    assert body["session_id"] == session["session_id"]
    assert body["demo_slug"] == DEMO_SLUG
    assert body["participant_token"]
    assert body["link_expires_at"]
    assert "participant_url" not in body

    stored = read_model.get_session(
        session["session_id"],
        tenant_id=api.current_tenant_id(),
    )

    assert stored["participant_token_hash"] == hash_participant_token(
        body["participant_token"]
    )
    assert stored["link_expiry"] == "demo"
    assert stored["link_expires_at"] == body["link_expires_at"]
    assert stored["demo_claimed_at"]

    denied = client.get(
        f"/sessions/{session['session_id']}/invitation",
        headers={"X-CVI-Participant-Token": "old-capability"},
    )

    assert denied.status_code == 401

    allowed = client.get(
        f"/sessions/{session['session_id']}/invitation",
        headers={
            "X-CVI-Participant-Token": body["participant_token"]
        },
    )

    assert allowed.status_code == 200


def test_demo_pool_moves_to_next_session_while_first_claim_is_active(demo_pool):
    _make_demo_session("demo-pool-one")
    _make_demo_session("demo-pool-two")

    client = TestClient(api.app)
    first = client.post(f"/demo/invites/{DEMO_SLUG}")
    second = client.post(f"/demo/invites/{DEMO_SLUG}")
    exhausted = client.post(f"/demo/invites/{DEMO_SLUG}")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["session_id"] == "demo-pool-one"
    assert second.json()["session_id"] == "demo-pool-two"
    assert exhausted.status_code == 503


def test_demo_invite_rejects_invalid_slug(demo_pool):
    client = TestClient(api.app)

    response = client.post("/demo/invites/a!")

    assert response.status_code == 422
