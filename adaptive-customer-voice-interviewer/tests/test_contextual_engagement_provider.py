import types

import agent.voice.contextual_engagement as engagement


class FakeLLM:
    def invoke(self, prompt):
        assert "ANSWER DECOMPOSITION" in prompt
        assert "warm, upbeat, and active" in prompt
        assert "lightly creative" in prompt
        assert "This is not the next question." in prompt
        return types.SimpleNamespace(content="  I heard that.  ")


def test_contextual_engagement_uses_provider_neutral_llm(monkeypatch):
    calls = []

    def fake_get_llm(**kwargs):
        calls.append(kwargs)
        return FakeLLM()

    def fail_direct_groq(*args, **kwargs):
        raise AssertionError("contextual engagement must not create ChatGroq directly")

    monkeypatch.setattr(
        engagement,
        "get_llm",
        fake_get_llm,
        raising=False,
    )
    monkeypatch.setattr(
        engagement,
        "ChatGroq",
        fail_direct_groq,
        raising=False,
    )

    result = engagement.generate_contextual_engagement(
        {"summary": "The participant uses the product weekly."}
    )

    assert calls == [{"purpose": "engagement"}]
    assert result == "I heard that."
