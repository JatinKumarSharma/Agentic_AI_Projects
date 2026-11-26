from deploy.speech import stt as speech_stt
from agent.voice.stt_providers import STTResult


class FakeGroqProvider:
    name = "groq"

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, *, filename, language, prompt):
        self.calls.append(
            {
                "audio": audio,
                "filename": filename,
                "language": language,
                "prompt": prompt,
            }
        )
        return STTResult(
            text="the setup was smooth",
            provider="groq",
            language="en",
            confidence=-0.1,
            metadata={"model": "whisper-large-v3-turbo"},
        )


def test_groq_speech_engine_uses_agent_stt_boundary():
    provider = FakeGroqProvider()
    engine = speech_stt.GroqSTTEngine(provider=provider)
    wav = speech_stt.pcm16_to_wav(b"\x00\x00" * 160)

    result = engine.transcribe(
        wav,
        language="en-IN",
        prompt="Describe setup.",
    )

    assert result["text"] == "the setup was smooth"
    assert result["language"] == "en"
    assert result["confidence"] == -0.1
    assert result["metadata"]["engine"] == "groq"
    assert result["metadata"]["model"] == "whisper-large-v3-turbo"
    assert provider.calls[0]["audio"] == wav
    assert provider.calls[0]["filename"] == "segment.wav"
    assert provider.calls[0]["language"] == "en-IN"
    assert provider.calls[0]["prompt"] == "Describe setup."


def test_create_stt_engine_supports_groq(monkeypatch):
    monkeypatch.setenv("CVI_SPEECH_STT_ENGINE", "groq")

    engine = speech_stt.create_stt_engine()

    assert isinstance(engine, speech_stt.GroqSTTEngine)
    assert engine.name == "groq"
