from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import api
from agent import read_model
from agent.session import create_research_session


SESSION_ID = "interview-lifecycle-session"


class CompletedGraph:
    def __init__(self):
        self.calls = 0

    def invoke(self, *args, **kwargs):
        self.calls += 1
        return {
            "interview_complete": True,
            "termination_reason": "research_goal_met",
            "questions_asked": [],
            "responses": [],
        }


class WaitingGraph:
    def __init__(self):
        self.calls = 0

    def invoke(self, *args, **kwargs):
        self.calls += 1
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
def lifecycle_session(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "interview-lifecycle.sqlite3")

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
    api.sessions[SESSION_ID] = session

    yield session

    api.sessions.clear()
    api.pending_interrupts.clear()
    read_model.configure()


def test_completed_interview_cannot_be_restarted(lifecycle_session, monkeypatch):
    graph = CompletedGraph()
    monkeypatch.setattr(api, "graph", graph)

    first = api.start_interview(SESSION_ID)

    assert first["status"] == "completed"

    stored = read_model.get_session(SESSION_ID)
    assert stored["interview_status"] == "completed"

    with pytest.raises(HTTPException) as excinfo:
        api.start_interview(SESSION_ID)

    assert excinfo.value.status_code == 409
    assert graph.calls == 1


def test_in_progress_interview_cannot_be_restarted_without_interrupt(
    lifecycle_session,
    monkeypatch,
):
    graph = WaitingGraph()
    monkeypatch.setattr(api, "graph", graph)

    first = api.start_interview(SESSION_ID)

    assert first["status"] == "waiting"

    stored = read_model.get_session(SESSION_ID)
    assert stored["interview_status"] == "in_progress"

    # Simulate a lost/corrupted pending action. The persisted lifecycle must
    # still prevent a second graph start on the same thread.
    api.clear_pending_interrupt(SESSION_ID)

    with pytest.raises(HTTPException) as excinfo:
        api.start_interview(SESSION_ID)

    assert excinfo.value.status_code == 409
    assert graph.calls == 1
