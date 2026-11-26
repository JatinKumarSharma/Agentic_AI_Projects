from copy import deepcopy

import pytest

from agent import read_model
from agent.evidence_retrieval import (
    get_evidence_reference,
    retrieve_evidence,
)


TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
SESSION = "evidence-session"
RESPONSE = "Setup takes too long. It breaks daily work."

STATE = {
    "research_session": {
        "session_id": SESSION,
        "status": "completed",
    },
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
def evidence_db(tmp_path):
    read_model.configure(tmp_path / "evidence.sqlite3")

    yield

    read_model.configure()


def seed_session(session_id=SESSION, tenant_id=TENANT_A, state=None):
    read_model.upsert_session({
        "session_id": session_id,
        "tenant_id": tenant_id,
        "status": "completed",
    })
    read_model.record_snapshot(
        session_id,
        state or STATE,
        source="response_processed",
        tenant_id=tenant_id,
    )


def test_retrieve_evidence_from_authoritative_snapshot(evidence_db):
    seed_session()

    items = retrieve_evidence(tenant_id=TENANT_A, session_id=SESSION)

    assert len(items) == 1
    item = items[0]
    assert item["tenant_id"] == TENANT_A
    assert item["session_id"] == SESSION
    assert item["record_type"] == "state_snapshot"
    assert item["source"] == "response_processed"
    assert item["evidence_id"] == "ev_1"
    assert item["response_id"] == "resp_1"
    assert item["turn"] == 1
    assert item["category"] == "pain_points"
    assert item["parameter"] == "setup"
    assert item["source_quote"] == "Setup takes too long."
    assert item["response_text"] == RESPONSE
    assert item["question"] == "What is frustrating?"
    assert item["quote_is_verbatim"] is True
    assert item["item_confidence"] == "high"
    assert item["item_sufficiency"] == "basic"
    assert item["item_criticality"] == "critical"
    assert item["item_verification_status"] == "unverified"


def test_retrieve_evidence_filters_and_citation_lookup(evidence_db):
    seed_session()

    items = retrieve_evidence(tenant_id=TENANT_A, session_id=SESSION)

    assert retrieve_evidence(
        tenant_id=TENANT_A,
        session_id=SESSION,
        evidence_id="ev_1",
    ) == items
    assert retrieve_evidence(
        tenant_id=TENANT_A,
        session_id=SESSION,
        response_id="resp_1",
    ) == items
    assert retrieve_evidence(
        tenant_id=TENANT_A,
        session_id=SESSION,
        turn=1,
    ) == items
    assert retrieve_evidence(
        tenant_id=TENANT_A,
        session_id=SESSION,
        category="pain_points",
        parameter="setup",
    ) == items
    assert retrieve_evidence(
        tenant_id=TENANT_A,
        session_id=SESSION,
        evidence_id="ev_missing",
    ) == []
    assert retrieve_evidence(
        tenant_id=TENANT_A,
        session_id=SESSION,
        turn=2,
    ) == []

    assert get_evidence_reference(
        "ev_1",
        tenant_id=TENANT_A,
        session_id=SESSION,
    ) == items[0]
    assert get_evidence_reference(
        "ev_missing",
        tenant_id=TENANT_A,
        session_id=SESSION,
    ) is None


def test_retrieve_evidence_is_tenant_scoped(evidence_db):
    seed_session()

    other_state = deepcopy(STATE)
    other_state["response_records"][0]["response_id"] = "resp_2"
    other_state["pain_points"]["setup"]["values"][0].update({
        "evidence_id": "ev_2",
        "response_id": "resp_2",
    })
    seed_session(
        session_id="other-tenant-session",
        tenant_id=TENANT_B,
        state=other_state,
    )

    assert [
        item["evidence_id"]
        for item in retrieve_evidence(tenant_id=TENANT_A)
    ] == ["ev_1"]
    assert retrieve_evidence(
        tenant_id=TENANT_A,
        session_id="other-tenant-session",
    ) == []
    assert retrieve_evidence(
        tenant_id=TENANT_B,
        session_id=SESSION,
    ) == []


def test_retrieve_evidence_hides_unverified_quotes_by_default(evidence_db):
    state = deepcopy(STATE)
    state["pain_points"]["setup"]["values"][0][
        "source_quote"
    ] = "This line was never spoken."
    seed_session(state=state)

    assert retrieve_evidence(tenant_id=TENANT_A, session_id=SESSION) == []

    items = retrieve_evidence(
        tenant_id=TENANT_A,
        session_id=SESSION,
        include_unverified=True,
    )

    assert len(items) == 1
    assert items[0]["quote_is_verbatim"] is False


def test_retrieve_evidence_uses_latest_authoritative_snapshot(evidence_db):
    seed_session()

    newer_state = deepcopy(STATE)
    newer_state["pain_points"]["setup"]["values"][0].update({
        "evidence_id": "ev_new",
        "value": "daily work is disrupted",
        "source_quote": "It breaks daily work.",
    })
    read_model.record_snapshot(
        SESSION,
        newer_state,
        source="interview_completed",
        tenant_id=TENANT_A,
    )

    items = retrieve_evidence(tenant_id=TENANT_A, session_id=SESSION)

    assert [item["evidence_id"] for item in items] == ["ev_new"]
    assert items[0]["source"] == "interview_completed"
