import base64

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.voice.stt_providers import STTProviderError, STTResult


SESSION_ID = "voice-transcription-session"
SESSION = {
    "session_id": SESSION_ID,
    "company_name": "Transcription Company",
    "company_id": "transcription-company",
    "product_name": "Transcription Product",
    "product_id": "transcription-product",
    "product_category": "Analytics",
    "research_goal": "Understand transcription quality.",
    "status": "active",
}


@pytest.fixture(autouse=True)
def transcription_api_state(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "voice-transcription.sqlite3")
    api.sessions[SESSION_ID] = SESSION

    for name in (
        "CVI_VOICE_LANGUAGE",
        "CVI_VOICE_STT_PROVIDER",
        "CVI_VOICE_STT_FALLBACKS",
    ):
        monkeypatch.delenv(name, raising=False)

    yield

    read_model.configure()
    api.sessions.pop(SESSION_ID, None)


def upload(audio=b"RIFF-fake-wav"):
    return {
        "audio_base64": base64.b64encode(audio).decode("ascii"),
        "filename": "response.wav",
        "turn": 1,
    }


def test_transcribe_returns_groq_result_without_ack_for_unknown_duration(monkeypatch):
    captured = {}

    def fake_transcribe(audio, *, primary, fallbacks, **kwargs):
        captured["audio"] = audio
        captured["primary"] = primary
        captured["fallbacks"] = fallbacks
        captured["language"] = kwargs.get("language")

        return (
            STTResult(
                text="Setup was easy.",
                provider="groq",
                language="en-IN",
                confidence=0.91,
                metadata={"model": "whisper-large-v3-turbo"},
            ),
            [],
        )

    monkeypatch.setattr(
        api,
        "transcribe_with_fallback",
        fake_transcribe,
    )
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json=upload(),
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["transcript"] == "Setup was easy."
    assert payload["provider"] == "groq"
    assert payload["language"] == "en-IN"
    assert payload["confidence"] == 0.91
    assert payload["audio_issue"] is False
    assert captured["audio"] == b"RIFF-fake-wav"
    assert captured["primary"] == "groq"
    assert captured["fallbacks"] == ("groq", "local", "typed")
    assert captured["language"] == "en-IN"

    # The non-streaming transcribe endpoint has no speech-duration signal, so
    # the ack policy correctly suppresses a prompt for this short/unknown turn.
    assert payload["acknowledgement"] is None

    timings = read_model.list_timings(SESSION_ID)
    span_names = {timing["span_name"] for timing in timings}

    assert "speech_end_acknowledgement_emitted" not in span_names


def test_typed_fallback_marks_audio_issue(monkeypatch):
    def fake_transcribe(*args, **kwargs):
        return (
            STTResult(
                text="",
                provider="typed",
                metadata={"typed_fallback": True},
            ),
            [{"provider": "groq", "reason": "unavailable"}],
        )

    monkeypatch.setattr(
        api,
        "transcribe_with_fallback",
        fake_transcribe,
    )
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json=upload(),
    )

    assert response.status_code == 200
    assert response.json()["transcript"] == ""
    assert response.json()["audio_issue"] is True
    assert response.json()["provider"] == "typed"
    assert "groq: unavailable" in response.json()["failure_detail"]


def test_successful_transcription_has_no_failure_detail(monkeypatch):
    def fake_transcribe(*args, **kwargs):
        return (
            STTResult(text="Setup was easy.", provider="groq"),
            [],
        )

    monkeypatch.setattr(
        api,
        "transcribe_with_fallback",
        fake_transcribe,
    )
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json=upload(),
    )

    assert response.status_code == 200
    assert response.json()["failure_detail"] is None


def test_transcription_validation_and_failure_codes(monkeypatch):
    client = TestClient(api.app)

    missing = client.post(
        "/sessions/missing-session/voice/transcribe",
        json=upload(),
    )

    assert missing.status_code == 404

    empty = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json=upload(audio=b""),
    )

    assert empty.status_code == 422

    def failing_transcribe(*args, **kwargs):
        raise STTProviderError("All STT providers failed.")

    monkeypatch.setattr(
        api,
        "transcribe_with_fallback",
        failing_transcribe,
    )

    failed = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json=upload(),
    )

    assert failed.status_code == 503
    assert failed.json()["detail"] == "All STT providers failed."
