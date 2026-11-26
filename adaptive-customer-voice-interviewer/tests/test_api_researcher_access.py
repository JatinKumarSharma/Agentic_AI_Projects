import base64

import pytest
from fastapi.testclient import TestClient

import api


ACCESS_CODE = "test-researcher-access-code"


def auth_header(username, password):
    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")

    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def configured_client(monkeypatch):
    monkeypatch.setenv("RESEARCHER_ACCESS_CODE", ACCESS_CODE)

    return TestClient(api.app)


def test_participant_view_has_no_researcher_link(configured_client):
    response = configured_client.get("/")

    assert response.status_code == 200
    assert 'href="/researcher"' not in response.text
    assert ">Participant<" in response.text


def test_researcher_pages_require_access_code(configured_client):
    for path in ("/researcher", "/researcher.js", "/researcher.html"):
        response = configured_client.get(path)

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"].startswith("Basic ")


def test_researcher_pages_reject_wrong_code(configured_client):
    response = configured_client.get(
        "/researcher",
        headers=auth_header("researcher", "wrong-code"),
    )

    assert response.status_code == 401


def test_researcher_pages_allow_correct_code(configured_client):
    headers = auth_header("researcher", ACCESS_CODE)

    page = configured_client.get("/researcher", headers=headers)
    script = configured_client.get("/researcher.js", headers=headers)

    assert page.status_code == 200
    assert "Research dashboard" in page.text

    assert script.status_code == 200
    assert "loadSessionDetail" in script.text


def test_researcher_data_endpoints_require_access_code(configured_client):
    protected = configured_client.get("/researcher/sessions")

    assert protected.status_code == 401

    allowed = configured_client.get(
        "/researcher/sessions",
        headers=auth_header("researcher", ACCESS_CODE),
    )

    assert allowed.status_code == 200
    assert "sessions" in allowed.json()


def test_researcher_access_is_denied_when_not_configured(monkeypatch):
    monkeypatch.delenv("RESEARCHER_ACCESS_CODE", raising=False)

    client = TestClient(api.app)
    response = client.get("/researcher")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Researcher access is not configured."
    )
