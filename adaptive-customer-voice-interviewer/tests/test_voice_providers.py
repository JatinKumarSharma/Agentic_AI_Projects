import io
import json
import wave

import pytest
from pytest import approx

from agent.voice import stt_providers, tts_providers


class FakeHeaders:
    def __init__(self, content_type):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class FakeResponse:
    def __init__(self, body, content_type):
        self.body = body
        self.headers = FakeHeaders(content_type)

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_pcm16_chunks_to_wav_produces_readable_wav():
    chunk = b"\x00\x01" * 160

    wav_bytes = stt_providers.pcm16_chunks_to_wav(
        [chunk, chunk],
        sample_rate=16000,
    )

    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.getnframes() == 320


def test_groq_stt_sends_auth_and_model(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["data"] = request.data
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout

        return FakeResponse(
            json.dumps(
                {
                    "text": "Easy to use.",
                    "language": "en",
                    "duration": 1.4,
                    "segments": [{"avg_logprob": -0.12}],
                }
            ).encode("utf-8"),
            "application/json",
        )

    monkeypatch.setattr(stt_providers, "urlopen", fake_urlopen)

    provider = stt_providers.GroqSTTProvider(api_key="test-key")
    result = provider.transcribe(
        b"audio-bytes",
        language="en",
        prompt="Interview answer about usability.",
    )

    assert result.text == "Easy to use."
    assert result.provider == "groq"
    assert result.confidence == approx(-0.12)
    assert result.metadata["model"] == "whisper-large-v3-turbo"
    assert captured["authorization"] == "Bearer test-key"

    body = captured["data"].decode("utf-8")

    assert "whisper-large-v3-turbo" in body
    assert "verbose_json" in body
    assert "Interview answer about usability." in body
    assert "audio-bytes" in body


def test_groq_stt_normalizes_locale_language_to_iso_code(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["data"] = request.data
        return FakeResponse(
            json.dumps({"text": "Easy to use."}).encode("utf-8"),
            "application/json",
        )

    monkeypatch.setattr(stt_providers, "urlopen", fake_urlopen)

    provider = stt_providers.GroqSTTProvider(api_key="test-key")
    provider.transcribe(b"audio-bytes", language="en-IN")

    body = captured["data"].decode("utf-8")

    assert 'name="language"\r\n\r\nen\r\n' in body
    assert "en-IN" not in body


def test_multipart_request_reports_http_error_details(monkeypatch):
    from urllib.error import HTTPError

    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":"invalid language"}'),
        )

    monkeypatch.setattr(stt_providers, "urlopen", fake_urlopen)

    with pytest.raises(stt_providers.STTProviderError) as error:
        stt_providers._multipart_request(
            "https://example.test/transcriptions",
            fields={"model": "test"},
            file_name="response.wav",
            file_bytes=b"audio",
        )

    assert "HTTP 400" in str(error.value)
    assert "invalid language" in str(error.value)


def test_stt_fallback_skips_empty_transcript_from_provider(monkeypatch):
    class EmptyProvider:
        name = "groq"

        def is_available(self):
            return True

        def transcribe(self, *args, **kwargs):
            return stt_providers.STTResult(text="", provider=self.name)

    class Working:
        name = "local"

        def is_available(self):
            return True

        def transcribe(self, audio, **kwargs):
            return stt_providers.STTResult(
                text="Local transcript",
                provider=self.name,
            )

    providers = {"groq": EmptyProvider(), "local": Working()}
    monkeypatch.setattr(
        stt_providers,
        "create_stt_provider",
        lambda name: providers[name],
    )

    result, failures = stt_providers.transcribe_with_fallback(
        b"audio",
        primary="groq",
        fallbacks=("local",),
    )

    assert result.text == "Local transcript"
    assert failures == [
        {"provider": "groq", "reason": "empty transcript"}
    ]


def test_stt_fallback_skips_unavailable_provider(monkeypatch):
    class Unavailable:
        name = "groq"

        def is_available(self):
            return False

        def transcribe(self, *args, **kwargs):
            raise AssertionError("Unavailable provider must not be called.")

    class Working:
        name = "local"

        def is_available(self):
            return True

        def transcribe(self, audio, **kwargs):
            return stt_providers.STTResult(
                text="Local transcript",
                provider=self.name,
            )

    providers = {"groq": Unavailable(), "local": Working()}
    monkeypatch.setattr(
        stt_providers,
        "create_stt_provider",
        lambda name: providers[name],
    )

    result, failures = stt_providers.transcribe_with_fallback(
        b"audio",
        primary="groq",
        fallbacks=("local", "typed"),
    )

    assert result.text == "Local transcript"
    assert result.provider == "local"
    assert failures == [{"provider": "groq", "reason": "unavailable"}]


def test_browser_tts_returns_metadata_only():
    result = tts_providers.BrowserTTSProvider().synthesize(
        "What matters most?",
        {"language": "en-IN", "rate": 0.912},
    )

    assert result.playback == "browser"
    assert result.audio_bytes == b""
    assert result.content_type == tts_providers.BROWSER_CONTENT_TYPE
    assert result.metadata["delivery"]["language"] == "en-IN"


def test_groq_tts_sends_delivery_metadata(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["data"] = request.data
        captured["authorization"] = request.get_header("Authorization")

        return FakeResponse(
            b"mp3-audio",
            "audio/mpeg",
        )

    monkeypatch.setattr(tts_providers, "urlopen", fake_urlopen)

    provider = tts_providers.GroqTTSProvider(api_key="test-key")
    result = provider.synthesize(
        "What matters most?",
        {
            "language": "en-IN",
            "voice": "Arista",
            "rate": 0.912,
            "pitch": 1.01,
            "volume": 0.9,
        },
    )

    assert result.playback == "audio"
    assert result.audio_bytes == b"mp3-audio"
    assert result.content_type == "audio/mpeg"
    assert captured["authorization"] == "Bearer test-key"

    payload = json.loads(captured["data"].decode("utf-8"))

    assert payload["input"] == "What matters most?"
    assert payload["voice"] == "Arista"
    assert payload["speed"] == approx(0.912)
    assert payload["model"] == "playai-tts"


def test_local_tts_supports_enterprise_self_hosting(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["data"] = request.data
        captured["authorization"] = request.get_header("Authorization")

        return FakeResponse(
            b"local-audio",
            "audio/wav",
        )

    monkeypatch.setattr(tts_providers, "urlopen", fake_urlopen)

    provider = tts_providers.LocalHTTPTTSProvider(
        endpoint="http://localhost:9001/synthesize",
        api_key="local-key",
    )
    result = provider.synthesize(
        "What would you improve?",
        {
            "language": "en-IN",
            "voice": "indic-voice",
            "rate": 0.912,
            "pitch": 1.01,
            "volume": 0.9,
        },
    )

    assert result.playback == "audio"
    assert result.audio_bytes == b"local-audio"
    assert result.provider == "local"
    assert captured["authorization"] == "Bearer local-key"

    payload = json.loads(captured["data"].decode("utf-8"))

    assert payload["language"] == "en-IN"
    assert payload["voice"] == "indic-voice"
    assert payload["pitch"] == approx(1.01)
    assert payload["volume"] == approx(0.9)


def test_tts_fallback_reaches_browser_when_cloud_unavailable(monkeypatch):
    class Unavailable:
        name = "groq"

        def is_available(self):
            return False

        def synthesize(self, *args, **kwargs):
            raise AssertionError("Unavailable provider must not be called.")

    providers = {
        "groq": Unavailable(),
        "browser": tts_providers.BrowserTTSProvider(),
    }
    monkeypatch.setattr(
        tts_providers,
        "create_tts_provider",
        lambda name: providers[name],
    )

    result, failures = tts_providers.synthesize_with_fallback(
        "Got it.",
        primary="groq",
        fallbacks=("browser",),
        delivery={"language": "en-IN"},
    )

    assert result.playback == "browser"
    assert failures == [{"provider": "groq", "reason": "unavailable"}]
