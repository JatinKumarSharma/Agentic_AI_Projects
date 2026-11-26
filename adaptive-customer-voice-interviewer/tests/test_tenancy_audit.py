import base64
import sqlite3

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.tenancy import normalize_tenant_id
from scripts.migrate import run_migrations


ACCESS_CODE = "tenant-audit-code"


def auth_header():
    token = base64.b64encode(
        f"researcher:{ACCESS_CODE}".encode("utf-8")
    ).decode("ascii")

    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def tenant_db(tmp_path, monkeypatch):
    database = tmp_path / "tenancy.sqlite3"
    read_model.configure(database)
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", ACCESS_CODE)
    monkeypatch.setenv("CVI_TENANT_ID", "tenant-a")

    yield database

    read_model.configure()
    monkeypatch.delenv("CVI_TENANT_ID", raising=False)


def session(session_id, tenant_id):
    return {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "company_name": "Example Co",
        "product_name": "Example Kit",
        "product_category": "Analytics",
        "customer_segment": "small business",
        "research_goal": "Understand onboarding.",
        "start_date": "2026-01-10",
        "end_date": "2026-01-20",
        "status": "completed",
    }


def state(session_record):
    return {
        "research_session": session_record,
        "interview_complete": True,
        "termination_reason": "sufficiency_reached",
        "questions_asked": ["What was frustrating?"],
        "responses": ["Setup takes too long."],
        "response_records": [
            {
                "response_id": "resp_1",
                "turn": 1,
                "question": "What was frustrating?",
                "text": "Setup takes too long.",
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


def test_normalize_tenant_id_accepts_safe_values_and_rejects_others():
    assert normalize_tenant_id("acme.eu") == "acme.eu"
    assert normalize_tenant_id(None) == "default"

    with pytest.raises(ValueError):
        normalize_tenant_id("tenant' OR 1=1")


def test_read_model_scopes_sessions_events_snapshots_and_timings(tenant_db):
    first = session("session-a", "tenant-a")
    second = session("session-b", "tenant-b")

    read_model.upsert_session(first)
    read_model.record_event("session-a", "session.created", {"owner": "a"})
    read_model.record_snapshot("session-a", state(first), "test")
    read_model.record_timing(
        "session-a", 1, "first_question", 0.0, 1.0, 1.0
    )

    read_model.upsert_session(second)

    assert [
        row["session_id"]
        for row in read_model.list_sessions(tenant_id="tenant-a")
    ] == ["session-a"]
    assert [
        row["session_id"]
        for row in read_model.list_sessions(tenant_id="tenant-b")
    ] == ["session-b"]
    assert read_model.get_session("session-a", tenant_id="tenant-b") is None
    assert read_model.list_events(
        "session-a", tenant_id="tenant-b"
    ) == []
    assert read_model.list_timings(
        "session-a", tenant_id="tenant-b"
    ) == []
    assert read_model.get_latest_snapshot(
        "session-a", tenant_id="tenant-b"
    ) is None


def test_read_model_scopes_reports_and_evidence(tenant_db, monkeypatch):
    from agent.analysis_report import build_session_report

    first = session("report-session-a", "tenant-a")
    second = session("report-session-b", "tenant-b")

    read_model.upsert_session(first)
    report_a = build_session_report(first, state(first))
    read_model.store_report(report_a)

    monkeypatch.setenv("CVI_TENANT_ID", "tenant-b")
    read_model.upsert_session(second)
    second_state = state(second)
    second_state["response_records"][0]["response_id"] = "resp_b"
    second_state["pain_points"]["setup"]["values"][0][
        "response_id"
    ] = "resp_b"
    second_state["pain_points"]["setup"]["values"][0][
        "evidence_id"
    ] = "ev_b"
    report_b = build_session_report(second, second_state)
    read_model.store_report(report_b)

    assert [
        row["report_id"]
        for row in read_model.list_reports(tenant_id="tenant-a")
    ] == [report_a["report_id"]]
    assert read_model.get_report(
        report_b["report_id"], tenant_id="tenant-a"
    ) is None


def test_audit_log_records_actions_and_is_tenant_scoped(tenant_db):
    read_model.record_audit(
        action="session.created",
        actor="participant",
        resource_type="session",
        resource_id="session-a",
        tenant_id="tenant-a",
        request_id="request-1",
        metadata={"invitation": "issued"},
    )
    read_model.record_audit(
        action="session.created",
        actor="participant",
        resource_type="session",
        resource_id="session-b",
        tenant_id="tenant-b",
    )

    entries = read_model.list_audit(tenant_id="tenant-a")

    assert len(entries) == 1
    assert entries[0]["action"] == "session.created"
    assert entries[0]["actor"] == "participant"
    assert entries[0]["resource_id"] == "session-a"
    assert entries[0]["request_id"] == "request-1"
    assert entries[0]["metadata"] == {"invitation": "issued"}
    assert read_model.list_audit(tenant_id="tenant-b")[0]["tenant_id"] == (
        "tenant-b"
    )


def test_migration_records_tenant_audit_version(tmp_path, monkeypatch):
    database = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, "
        "payload_json TEXT NOT NULL)"
    )
    connection.close()
    monkeypatch.setenv("CVI_TENANT_ID", "tenant-a")

    run_migrations(database)

    connection = sqlite3.connect(database)
    versions = [
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ]
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(sessions)")
    }
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    connection.close()

    assert versions == [
        "001_baseline",
        "002_tenant_audit_fields",
        "003_authoritative_memory_contract",
        "004_durable_langgraph_checkpoints",
        "005_durable_worker_queue",
        "006_session_leases",
        "007_worker_fencing",
    ]
    assert {"tenant_id", "created_by", "updated_by"} <= columns
    assert "audit_log" in tables
    assert "research_memory_records" in tables
    assert "langgraph_checkpoints" in tables
    assert "langgraph_checkpoint_blobs" in tables
    assert "langgraph_checkpoint_writes" in tables


def test_debug_endpoint_requires_researcher_auth(tenant_db):
    client = TestClient(api.app)

    unauthorized = client.get("/debug/session-state/session-a")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["WWW-Authenticate"].startswith("Basic ")

    allowed = client.get(
        "/debug/session-state/session-a",
        headers=auth_header(),
    )
    assert allowed.status_code == 404


def test_debug_endpoint_denied_when_access_code_missing(
    tenant_db, monkeypatch
):
    monkeypatch.delenv("RESEARCHER_ACCESS_CODE")

    response = TestClient(api.app).get(
        "/debug/session-state/session-a",
        headers=auth_header(),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Researcher access is not configured."
    )


def test_researcher_endpoints_scope_data_to_configured_tenant(
    tenant_db, monkeypatch
):
    read_model.upsert_session(session("session-a", "tenant-a"))

    monkeypatch.setenv("CVI_TENANT_ID", "tenant-b")
    read_model.upsert_session(session("session-b", "tenant-b"))

    monkeypatch.setenv("CVI_TENANT_ID", "tenant-a")
    client = TestClient(api.app)

    listing = client.get(
        "/researcher/sessions",
        headers=auth_header(),
    )
    ids = [row["session_id"] for row in listing.json()["sessions"]]

    assert listing.status_code == 200
    assert ids == ["session-a"]

    other = client.get(
        "/researcher/sessions/session-b",
        headers=auth_header(),
    )
    assert other.status_code == 404


def test_audit_endpoint_is_tenant_scoped(tenant_db):
    read_model.record_audit(
        action="session.created",
        actor="researcher",
        resource_type="session",
        resource_id="session-a",
        tenant_id="tenant-a",
    )
    read_model.record_audit(
        action="session.created",
        actor="researcher",
        resource_type="session",
        resource_id="session-b",
        tenant_id="tenant-b",
    )

    client = TestClient(api.app)
    response = client.get(
        "/researcher/audit",
        headers=auth_header(),
    )

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert [entry["resource_id"] for entry in entries] == ["session-a"]


def test_create_session_requires_auth_when_code_configured(tenant_db):
    client = TestClient(api.app)
    payload = {
        "company_id": "acme",
        "company_name": "Acme Technologies",
        "product_id": "insight-kit",
        "product_name": "Insight Kit",
        "product_category": "Analytics",
        "research_goal": "Understand setup friction.",
    }

    assert client.post("/sessions", json=payload).status_code == 401
    assert client.post(
        "/sessions",
        json=payload,
        headers=auth_header(),
    ).status_code == 201


def test_report_generation_and_export_are_audited(tenant_db):
    record = session("session-a", "tenant-a")
    read_model.upsert_session(record)
    read_model.record_snapshot(
        "session-a",
        state(record),
        "test",
        "completed",
    )

    client = TestClient(api.app)
    generated = client.post(
        "/researcher/analysis/reports/session-a/generate",
        headers=auth_header(),
    )
    exported = client.get(
        "/researcher/analysis/reports/export.csv",
        headers=auth_header(),
    )

    assert generated.status_code == 200
    assert exported.status_code == 200
    actions = [entry["action"] for entry in read_model.list_audit()]
    assert "report.generated" in actions
    assert "report.exported" in actions
