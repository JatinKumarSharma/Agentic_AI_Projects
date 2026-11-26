import base64

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model


ACCESS_CODE = "evidence-api-code"
TENANT_A = "evidence-tenant-a"
TENANT_B = "evidence-tenant-b"
SESSION_A = "evidence-api-session-a"
SESSION_B = "evidence-api-session-b"
SESSION_C = "evidence-api-session-c"
RESPONSE = "Setup takes too long. It breaks daily work."


@pytest.fixture(autouse=True)
def evidence_api_db(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "evidence-api.sqlite3")
    monkeypatch.setenv("CVI_TENANT_ID", TENANT_A)
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", ACCESS_CODE)

    yield

    read_model.configure()


def auth_headers():
    token = base64.b64encode(
        f"researcher:{ACCESS_CODE}".encode("utf-8")
    ).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def make_state(*evidence):
    values = []
    for evidence_id, quote in evidence:
        values.append({
            "value": evidence_id,
            "source_quote": quote,
            "response_id": "resp_1",
            "evidence_id": evidence_id,
            "turn": 1,
            "captured_at": "2026-01-12T10:00:00.000+00:00",
            "participant_id": "participant-1",
            "sentiment": "negative",
            "confidence": "high",
            "evidence_type": "explicit",
        })

    return {
        "research_session": {"session_id": "unused"},
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
                "values": values,
                "sufficiency": "basic",
                "confidence": "high",
                "criticality": "critical",
                "verification_status": "unverified",
            }
        },
    }


def seed_session(session_id, tenant_id, *evidence):
    read_model.upsert_session({
        "session_id": session_id,
        "tenant_id": tenant_id,
        "status": "completed",
    })
    read_model.record_snapshot(
        session_id,
        make_state(*evidence),
        "response_processed",
        "completed",
        tenant_id=tenant_id,
    )


def seed_default():
    seed_session(
        SESSION_A,
        TENANT_A,
        ("ev_setup", "Setup takes too long."),
        ("ev_daily", "It breaks daily work."),
    )
    seed_session(
        SESSION_B,
        TENANT_B,
        ("ev_other", "Billing is confusing."),
    )


def test_evidence_endpoint_requires_researcher_access():
    client = TestClient(api.app)

    response = client.get("/researcher/evidence")

    assert response.status_code == 401


def test_evidence_endpoint_filters_by_citation_id():
    seed_default()
    client = TestClient(api.app)

    response = client.get(
        "/researcher/evidence",
        params={
            "session_id": SESSION_A,
            "evidence_id": "ev_setup",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["evidence_id"] == "ev_setup"
    assert item["response_id"] == "resp_1"
    assert item["source_quote"] == "Setup takes too long."
    assert item["quote_is_verbatim"] is True


def test_evidence_endpoint_ranks_lexical_query_deterministically():
    seed_default()
    client = TestClient(api.app)

    response = client.get(
        "/researcher/evidence",
        params={
            "session_id": SESSION_A,
            "q": "daily work",
        },
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert [
        item["evidence_id"] for item in response.json()["items"]
    ] == ["ev_daily", "ev_setup"]


def test_evidence_endpoint_is_tenant_scoped():
    seed_default()
    client = TestClient(api.app)

    response = client.get(
        "/researcher/evidence",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["tenant_id"] for item in items} == {TENANT_A}
    assert {item["evidence_id"] for item in items} == {
        "ev_setup",
        "ev_daily",
    }


def test_evidence_endpoint_can_surface_unverified_quotes_explicitly():
    seed_session(
        SESSION_C,
        TENANT_A,
        ("ev_bad", "This line was never spoken."),
    )
    client = TestClient(api.app)

    default = client.get(
        "/researcher/evidence",
        params={"session_id": SESSION_C},
        headers=auth_headers(),
    )
    included = client.get(
        "/researcher/evidence",
        params={
            "session_id": SESSION_C,
            "include_unverified": "true",
        },
        headers=auth_headers(),
    )

    assert default.json()["items"] == []
    assert included.json()["count"] == 1
    assert included.json()["items"][0]["quote_is_verbatim"] is False


def test_evidence_endpoint_records_audit_entry():
    seed_default()
    client = TestClient(api.app)

    response = client.get(
        "/researcher/evidence",
        params={"session_id": SESSION_A, "q": "setup"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    entries = read_model.list_audit(
        tenant_id=TENANT_A,
        action="evidence.retrieved",
    )
    assert len(entries) == 1
    assert entries[0]["actor"] == "researcher"
    assert entries[0]["metadata"]["result_count"] == 2


def test_evidence_endpoint_rejects_negative_limit():
    client = TestClient(api.app)

    response = client.get(
        "/researcher/evidence",
        params={"limit": -1},
        headers=auth_headers(),
    )

    assert response.status_code == 422
