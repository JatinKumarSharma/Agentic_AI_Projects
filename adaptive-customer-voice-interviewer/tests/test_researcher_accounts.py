"""Named researcher accounts and session ownership isolation."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from fastapi import HTTPException
from fastapi.requests import Request
from fastapi.testclient import TestClient

import api
from agent import auth, read_model
from agent.auth import ResearcherContext


def request_with_basic(username: str, password: str) -> Request:
    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    return Request(
        {
            "type": "http",
            "headers": [(b"authorization", f"Basic {token}".encode("ascii"))],
        }
    )


def clear_auth_env(monkeypatch):
    monkeypatch.delenv("RESEARCHER_ACCESS_CODE", raising=False)
    monkeypatch.delenv("CVI_RESEARCHER_ACCOUNTS", raising=False)


def test_legacy_researcher_code_keeps_admin_access(monkeypatch):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", "legacy-code")

    context = auth.authenticate_researcher(
        request_with_basic("researcher", "legacy-code")
    )

    assert context is not None
    assert context.username == "researcher"
    assert context.role == "admin"


def test_named_accounts_are_role_and_tenant_scoped(monkeypatch):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv(
        "CVI_RESEARCHER_ACCOUNTS",
        json.dumps({
            "alice": {
                "password": "alice-secret",
                "tenant_id": "tenant-a",
                "role": "researcher",
            },
            "bob": "bob-secret",
        }),
    )

    alice = auth.authenticate_researcher(
        request_with_basic("alice", "alice-secret")
    )
    bob = auth.authenticate_researcher(
        request_with_basic("bob", "bob-secret")
    )

    assert alice is not None
    assert alice.tenant_id == "tenant-a"
    assert alice.role == "researcher"

    assert bob is not None
    assert bob.role == "researcher"

    assert auth.authenticate_researcher(
        request_with_basic("alice", "wrong")
    ) is None


def test_missing_auth_configuration_returns_503(monkeypatch):
    clear_auth_env(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            auth.require_researcher(
                request_with_basic("researcher", "anything")
            )
        )

    assert excinfo.value.status_code == 503


def test_session_creation_fails_closed_without_auth_configuration(monkeypatch):
    clear_auth_env(monkeypatch)
    monkeypatch.delenv(auth.LOCAL_UNAUTHENTICATED_ENV, raising=False)

    client = TestClient(api.app)
    response = client.post(
        "/sessions",
        json={
            "company_id": "acme",
            "company_name": "Acme",
            "product_id": "kit",
            "product_name": "Insight Kit",
            "product_category": "Analytics",
            "research_goal": "Understand setup friction.",
        },
    )

    assert response.status_code == 503


def test_session_creation_allows_explicit_local_unauthenticated_mode(
    monkeypatch,
    owned_sessions,
):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv(auth.LOCAL_UNAUTHENTICATED_ENV, "1")

    client = TestClient(api.app)
    response = client.post(
        "/sessions",
        json={
            "company_id": "acme",
            "company_name": "Acme",
            "product_id": "kit",
            "product_name": "Insight Kit",
            "product_category": "Analytics",
            "research_goal": "Understand setup friction.",
        },
    )

    assert response.status_code == 201


def test_researcher_dashboard_rejects_wrong_password(monkeypatch):
    clear_auth_env(monkeypatch)
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", "legacy-code")

    client = TestClient(api.app)
    response = client.get(
        "/researcher",
        auth=("researcher", "wrong-code"),
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith(
        'Basic realm="CVI Researcher"'
    )


@pytest.fixture
def owned_sessions(tmp_path):
    read_model.configure(tmp_path / "researchers.sqlite3")

    read_model.upsert_session({
        "session_id": "alice-session",
        "created_by": "alice",
        "company_name": "Alice Co",
        "product_name": "Alice Product",
        "product_category": "Software",
        "research_goal": "Alice research",
        "status": "active",
    })
    read_model.upsert_session({
        "session_id": "bob-session",
        "created_by": "bob",
        "company_name": "Bob Co",
        "product_name": "Bob Product",
        "product_category": "Hardware",
        "research_goal": "Bob research",
        "status": "active",
    })
    read_model.upsert_session({
        "session_id": "legacy-session",
        "created_by": "",
        "company_name": "Legacy Co",
        "product_name": "Legacy Product",
        "product_category": "Services",
        "research_goal": "Legacy research",
        "status": "active",
    })

    yield

    read_model.configure()


def test_list_researcher_sessions_only_returns_owned_sessions(owned_sessions):
    alice = ResearcherContext(username="alice", tenant_id="default")
    admin = ResearcherContext(
        username="researcher",
        tenant_id="default",
        role="admin",
    )

    alice_ids = {
        item["session_id"]
        for item in api.list_researcher_sessions(context=alice)["sessions"]
    }
    admin_ids = {
        item["session_id"]
        for item in api.list_researcher_sessions(context=admin)["sessions"]
    }

    assert alice_ids == {"alice-session", "legacy-session"}
    assert admin_ids == {
        "alice-session",
        "bob-session",
        "legacy-session",
    }


def test_get_researcher_session_hides_another_owner(owned_sessions):
    alice = ResearcherContext(username="alice", tenant_id="default")

    assert api.get_researcher_session(
        "alice-session",
        context=alice,
    )["session"]["session_id"] == "alice-session"

    with pytest.raises(HTTPException) as excinfo:
        api.get_researcher_session(
            "bob-session",
            context=alice,
        )

    assert excinfo.value.status_code == 404


def test_create_session_records_the_authenticated_owner(owned_sessions):
    alice = ResearcherContext(username="alice", tenant_id="default")

    result = api.create_session(
        api.CreateSessionRequest(
            company_id="company-c",
            company_name="Company C",
            product_id="product-c",
            product_name="Product C",
            product_category="Software",
            research_goal="Understand onboarding.",
        ),
        Request({"type": "http", "headers": []}),
        context=alice,
    )

    session = read_model.get_session(
        result["session_id"],
        tenant_id="default",
    )

    assert session["created_by"] == "alice"


def test_create_session_stamps_authenticated_tenant_everywhere(owned_sessions):
    alice = ResearcherContext(
        username="alice",
        tenant_id="tenant-a",
        request_id="req-alice",
        role="researcher",
    )

    result = api.create_session(
        api.CreateSessionRequest(
            company_id="company-tenant",
            company_name="Tenant Co",
            product_id="product-tenant",
            product_name="Tenant Product",
            product_category="Software",
            research_goal="Understand tenant isolation.",
        ),
        Request({"type": "http", "headers": []}),
        context=alice,
    )

    session_id = result["session_id"]
    stored = read_model.get_session(session_id, tenant_id="tenant-a")

    assert stored is not None
    assert stored["tenant_id"] == "tenant-a"
    assert ("tenant-a", session_id) in api.sessions.keys()
    assert read_model.get_latest_snapshot(
        session_id,
        tenant_id="tenant-a",
    ) is not None
    assert read_model.list_events(session_id, tenant_id="tenant-a")
    audit = read_model.list_audit(
        tenant_id="tenant-a",
        resource_id=session_id,
    )
    assert len(audit) == 1
    assert audit[0]["tenant_id"] == "tenant-a"
    assert audit[0]["request_id"] == "req-alice"
    assert read_model.get_session(session_id, tenant_id="default") is None


def test_evidence_search_hides_another_owner(owned_sessions, monkeypatch):
    alice = ResearcherContext(username="alice", tenant_id="default")

    monkeypatch.setattr(
        api.evidence_retrieval,
        "retrieve_evidence",
        lambda **kwargs: [
            {
                "session_id": "alice-session",
                "evidence_id": "ev-alice",
                "source_quote": "Easy to use.",
            },
            {
                "session_id": "bob-session",
                "evidence_id": "ev-bob",
                "source_quote": "Too complicated.",
            },
        ],
    )

    result = api.list_researcher_evidence(context=alice)

    assert [item["evidence_id"] for item in result["items"]] == ["ev-alice"]

    with pytest.raises(HTTPException) as excinfo:
        api.list_researcher_evidence(
            session_id="bob-session",
            context=alice,
        )

    assert excinfo.value.status_code == 404


def test_reports_and_audit_are_owner_scoped(owned_sessions):
    alice = ResearcherContext(username="alice", tenant_id="default")

    for session_id, report_id in (
        ("alice-session", "report-alice"),
        ("bob-session", "report-bob"),
    ):
        read_model.store_report({
            "report_id": report_id,
            "session_id": session_id,
            "tenant_id": "default",
            "dimensions": {
                "customer_segment": "existing customer",
                "product_category": "Software",
                "status_label": "unfinished",
                "response_pattern_label": "unclassified",
                "tags": [],
            },
            "claims": [],
        })
        read_model.record_audit(
            action="session.created",
            actor="researcher",
            resource_type="session",
            resource_id=session_id,
            tenant_id="default",
        )

    reports = api.list_analysis_reports(context=alice)["reports"]
    audit = api.list_audit_entries(context=alice)["entries"]

    assert [report["report_id"] for report in reports] == ["report-alice"]
    assert {entry["resource_id"] for entry in audit} == {"alice-session"}

    with pytest.raises(HTTPException) as excinfo:
        api.get_analysis_report("report-bob", context=alice)

    assert excinfo.value.status_code == 404
