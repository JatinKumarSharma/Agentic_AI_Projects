import sys
import types

from pydantic import BaseModel

from agent import llm


class _Schema(BaseModel):
    value: str


class _StructuredModel:
    def __init__(self):
        self.kwargs = None

    def with_structured_output(self, schema, **kwargs):
        self.kwargs = kwargs
        return self


class _FakeChatOllama:
    captured = {}

    def __init__(self, **kwargs):
        _FakeChatOllama.captured = kwargs


def _install_fake_ollama(monkeypatch):
    module = types.ModuleType("langchain_ollama")
    module.ChatOllama = _FakeChatOllama
    monkeypatch.setitem(sys.modules, "langchain_ollama", module)
    _FakeChatOllama.captured = {}


def test_ollama_uses_configured_base_url(monkeypatch):
    _install_fake_ollama(monkeypatch)
    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CVI_OLLAMA_BASE_URL", "http://ollama:11434")

    llm.get_llm()

    assert _FakeChatOllama.captured["base_url"] == "http://ollama:11434"


def test_ollama_base_url_defaults_to_localhost(monkeypatch):
    _install_fake_ollama(monkeypatch)
    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")
    monkeypatch.delenv("CVI_OLLAMA_BASE_URL", raising=False)

    llm.get_llm()

    assert _FakeChatOllama.captured["base_url"] == "http://localhost:11434"


def test_ollama_disables_reasoning_for_schema_valid_json(monkeypatch):
    _install_fake_ollama(monkeypatch)
    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")

    llm.get_llm()

    assert _FakeChatOllama.captured["reasoning"] is False


def test_structured_ollama_sends_no_unsupported_kwargs(monkeypatch):
    model = _StructuredModel()
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    monkeypatch.setenv("CVI_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("CVI_LLM_STRUCTURED_METHOD", "json_schema")

    assert llm.get_structured_llm(_Schema) is model
    assert model.kwargs == {"method": "json_schema"}


def test_structured_groq_keeps_output_token_cap(monkeypatch):
    model = _StructuredModel()
    monkeypatch.setattr(llm, "get_llm", lambda: model)
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("CVI_LLM_STRUCTURED_METHOD", "function_calling")

    assert llm.get_structured_llm(_Schema) is model
    assert model.kwargs == {
        "method": "function_calling",
        "max_tokens": 999,
    }
