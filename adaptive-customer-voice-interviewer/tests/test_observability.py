import json
import logging

from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.observability import (
    JsonFormatter,
    get_request_id,
    reset_request_id,
    set_request_id,
)


def test_json_formatter_includes_request_id_and_extra_fields():
    record = logging.LogRecord(
        "cvi.test",
        logging.INFO,
        __file__,
        1,
        "hello %s",
        ("world",),
        None,
    )
    record.node = "meta_planner"

    token = set_request_id("request-123")
    try:
        payload = json.loads(JsonFormatter().format(record))
        assert get_request_id() == "request-123"
    finally:
        reset_request_id(token)

    assert payload["request_id"] == "request-123"
    assert payload["message"] == "hello world"
    assert payload["node"] == "meta_planner"
    assert payload["level"] == "INFO"


def test_live_endpoint_returns_request_id_header():
    client = TestClient(api.app)

    generated = client.get("/health/live")
    assert generated.status_code == 200
    assert generated.headers.get("X-Request-ID")

    echoed = client.get(
        "/health/live",
        headers={"X-Request-ID": "external-request"},
    )
    assert echoed.headers["X-Request-ID"] == "external-request"


def test_ready_endpoint_reports_database_and_provider(tmp_path, monkeypatch):
    database = tmp_path / "ready.sqlite3"
    read_model.configure(database)
    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CVI_OLLAMA_BASE_URL", "http://ollama:11434")

    client = TestClient(api.app)
    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["persistence_backend"] == "sqlite"
    assert body["checks"]["model_provider"] == "ollama"

    read_model.configure()


def test_ready_endpoint_fails_without_groq_key(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "ready.sqlite3")
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    client = TestClient(api.app)
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]

    read_model.configure()


def test_ready_endpoint_uses_repository_health_check(monkeypatch):
    class FailingRepository:
        def health_check(self):
            return "unavailable", "database file is missing"

    monkeypatch.setattr(api, "repository", FailingRepository())
    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")

    response = TestClient(api.app).get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["database"] == "unavailable"
    assert "database file is missing" in body["detail"]
