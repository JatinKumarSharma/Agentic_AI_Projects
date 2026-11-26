import pytest
from pydantic import BaseModel

from agent import llm


class ExampleSchema(BaseModel):
    value: str


def test_groq_provider_uses_configured_model(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.setenv("GROQ_MODEL", "model-under-test")
    monkeypatch.delenv("CVI_LLM_MAX_RETRIES", raising=False)
    monkeypatch.delenv("CVI_LLM_REQUEST_TIMEOUT", raising=False)

    model = llm.get_llm()

    assert model.model == "model-under-test"
    assert model.temperature == pytest.approx(0, abs=1e-6)
    assert model.max_tokens == 999
    assert model.max_retries == 0
    assert model.request_timeout == pytest.approx(12.0)


def test_groq_retry_and_timeout_can_be_configured(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.setenv("CVI_LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("CVI_LLM_REQUEST_TIMEOUT", "30")

    model = llm.get_llm()

    assert model.max_retries == 2
    assert model.request_timeout == pytest.approx(30.0)


def test_groq_model_can_be_overridden_for_one_boundary(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.setenv("GROQ_MODEL", "default-model")

    model = llm.get_llm(model="override-model")

    assert model.model == "override-model"


def test_purpose_temperatures_are_provider_neutral(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.delenv("CVI_LLM_TEMPERATURE", raising=False)
    monkeypatch.delenv("CVI_PROPOSAL_TEMPERATURE", raising=False)
    monkeypatch.delenv("CVI_ENGAGEMENT_TEMPERATURE", raising=False)

    assert llm.get_llm().temperature == pytest.approx(0.0, abs=1e-6)
    assert llm.get_llm(purpose="proposal").temperature == pytest.approx(0.3)
    assert llm.get_llm(purpose="engagement").temperature == pytest.approx(0.4)


def test_temperature_env_overrides_are_honoured(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.setenv("CVI_LLM_TEMPERATURE", "0.2")
    monkeypatch.setenv("CVI_PROPOSAL_TEMPERATURE", "0.15")
    monkeypatch.setenv("CVI_ENGAGEMENT_TEMPERATURE", "0.25")

    assert llm.get_llm().temperature == pytest.approx(0.2)
    assert llm.get_llm(purpose="proposal").temperature == pytest.approx(0.15)
    assert llm.get_llm(purpose="engagement").temperature == pytest.approx(0.25)


def test_unknown_llm_purpose_is_rejected(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")

    with pytest.raises(ValueError, match="Unknown LLM purpose"):
        llm.get_llm(purpose="creative_writing")


def test_temperature_must_be_within_supported_range(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.setenv("CVI_PROPOSAL_TEMPERATURE", "3")

    with pytest.raises(ValueError, match="0 and 2"):
        llm.get_llm(purpose="proposal")


def test_evaluator_llm_uses_evaluator_model_override(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.setenv("GROQ_MODEL", "default-model")
    monkeypatch.setenv("CVI_EVALUATOR_MODEL", "evaluator-model")

    model = llm.get_evaluator_llm()

    assert model.model == "evaluator-model"


def test_ollama_provider_uses_local_model(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LOCAL_LLM_MODEL", "local-under-test")

    model = llm.get_llm()

    assert model.model == "local-under-test"
    assert model.temperature == 0


def test_unsupported_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "unknown-provider")

    with pytest.raises(ValueError, match="CVI_LLM_PROVIDER"):
        llm.get_llm()


def test_structured_llm_uses_groq_function_calling_by_default(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.delenv("CVI_LLM_STRUCTURED_METHOD", raising=False)
    calls = {}

    class FakeStructuredModel:
        def with_structured_output(self, schema, *, method, **kwargs):
            calls["schema"] = schema
            calls["method"] = method
            calls["kwargs"] = kwargs
            return "structured"

    monkeypatch.setattr(
        llm,
        "get_llm",
        lambda: FakeStructuredModel(),
    )

    result = llm.get_structured_llm(ExampleSchema)

    assert result == "structured"
    assert calls["schema"] is ExampleSchema
    assert calls["method"] == "function_calling"
    assert calls["kwargs"]["max_tokens"] == 999


def test_structured_method_can_be_overridden(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CVI_LLM_STRUCTURED_METHOD", "json_schema")

    calls = {}

    class FakeStructuredModel:
        def with_structured_output(self, schema, *, method, **kwargs):
            calls["method"] = method
            calls["kwargs"] = kwargs
            return "structured"

    monkeypatch.setattr(llm, "get_llm", lambda: FakeStructuredModel())

    llm.get_structured_llm(ExampleSchema)

    assert calls["method"] == "json_schema"
    # langchain-ollama rejects extra kwargs here; num_predict is set on the
    # model in get_llm() instead.
    assert calls["kwargs"] == {}


def test_invoke_output_limit_is_provider_neutral(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    assert llm.invoke_output_limit(900) == {"max_tokens": 900}

    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")
    assert llm.invoke_output_limit(900) == {}
