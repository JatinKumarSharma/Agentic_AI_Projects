import io
import wave

from fastapi.testclient import TestClient

from deploy.speech import app as speech_app


def wav_bytes() -> bytes:
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * 160)

    return buffer.getvalue()


def test_transcribe_endpoint_returns_stt_adapter_contract(monkeypatch):
    captured = {}

    def fake_transcribe(audio, *, language, prompt):
        captured.update(
            audio=audio,
            language=language,
            prompt=prompt,
        )
        return {
            "text": "setup was smooth",
            "language": "en",
            "confidence": -0.12,
            "metadata": {"model": "small"},
        }

    monkeypatch.setattr(speech_app, "transcribe_audio", fake_transcribe)
    client = TestClient(speech_app.app)

    response = client.post(
        "/transcribe",
        files={"file": ("response.wav", wav_bytes(), "audio/wav")},
        data={"language": "en", "prompt": "Describe setup."},
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "setup was smooth",
        "language": "en",
        "confidence": -0.12,
        "metadata": {"model": "small"},
    }
    assert captured["language"] == "en"
    assert captured["prompt"] == "Describe setup."
    assert captured["audio"].startswith(b"RIFF")


def test_transcribe_endpoint_rejects_non_wav_payload():
    client = TestClient(speech_app.app)

    response = client.post(
        "/transcribe",
        files={"file": ("response.wav", b"not-a-wav", "audio/wav")},
    )

    assert response.status_code == 400
    assert "WAV" in response.json()["detail"]


def test_synthesize_endpoint_returns_audio_with_requested_prosody(monkeypatch):
    captured = {}

    def fake_synthesize(payload):
        captured.update(payload.model_dump())
        return b"RIFF-fake-wav", "audio/wav"

    monkeypatch.setattr(speech_app, "synthesize_audio", fake_synthesize)
    client = TestClient(speech_app.app)

    response = client.post(
        "/synthesize",
        json={
            "text": "What happened next?",
            "language": "en-IN",
            "voice": "local-voice",
            "rate": 0.9,
            "pitch": 1.0,
            "volume": 0.8,
            "format": "mp3",
        },
    )

    assert response.status_code == 200
    assert response.content == b"RIFF-fake-wav"
    assert response.headers["content-type"].startswith("audio/wav")
    assert captured["text"] == "What happened next?"
    assert captured["rate"] == 0.9
    assert captured["volume"] == 0.8
    assert captured["format"] == "mp3"


def test_ready_endpoint_reports_engine_configuration(monkeypatch):
    monkeypatch.setenv("CVI_SPEECH_STT_ENABLED", "true")
    monkeypatch.setenv("CVI_LOCAL_STT_MODEL", "small")
    monkeypatch.setenv("CVI_LOCAL_TTS_MODEL", "/models/tts/voice.onnx")

    client = TestClient(speech_app.app)
    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["stt"] == "configured"
    assert body["checks"]["tts"] == "configured"


def test_warmup_endpoint_loads_configured_engines(monkeypatch):
    calls = []

    monkeypatch.setattr(
        speech_app,
        "warm_transcriber",
        lambda: calls.append("stt") or {"model": "small"},
    )
    monkeypatch.setattr(
        speech_app,
        "warm_voice",
        lambda: calls.append("tts") or {"voice": "local"},
    )
    monkeypatch.setattr(
        speech_app,
        "warm_vad",
        lambda: calls.append("vad") or {"provider": "fake"},
    )

    client = TestClient(speech_app.app)
    response = client.post("/admin/warmup")

    assert response.status_code == 200
    assert response.json() == {
        "stt": {"model": "small"},
        "tts": {"voice": "local"},
        "vad": {"provider": "fake"},
    }
    assert calls == ["stt", "tts", "vad"]
