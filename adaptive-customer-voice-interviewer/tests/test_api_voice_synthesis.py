import json

import pytest
from fastapi.testclient import TestClient

import api
from agent import read_model
from agent.voice.tts_providers import TTSProviderError, TTSResult


SESSION_ID = "voice-synthesis-session"
SESSION = {
    "session_id": SESSION_ID,
    "company_name": "Voice Company",
    "company_id": "voice-company",
    "product_name": "Voice Product",
    "product_id": "voice-product",
    "product_category": "Analytics",
    "research_goal": "Understand voice experience.",
    "status": "active",
}


@pytest.fixture(autouse=True)
def voice_api_state(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "voice-api.sqlite3")
    api.sessions[SESSION_ID] = SESSION

    for name in (
        "CVI_VOICE_LANGUAGE",
        "CVI_VOICE_SOFTNESS",
        "CVI_VOICE_RATE",
        "CVI_VOICE_PITCH",
        "CVI_VOICE_VOLUME",
        "CVI_VOICE_TTS_PROVIDER",
        "CVI_VOICE_TTS_FALLBACKS",
        "CVI_VOICE_TTS_VOICE",
    ):
        monkeypatch.delenv(name, raising=False)

    yield

    read_model.configure()
    api.sessions.pop(SESSION_ID, None)


def test_invitation_exposes_participant_safe_voice_profile():
    client = TestClient(api.app)

    response = client.get(f"/sessions/{SESSION_ID}/invitation")

    assert response.status_code == 200

    profile = response.json()["voice_profile"]

    assert profile["language"] == "en-IN"
    assert profile["softness"] == "gentle"
    assert profile["tts_provider"] == "browser"
    assert profile["prosody"]["question"]["rate"] > 0
    assert "api_key" not in json.dumps(profile).lower()
    assert "endpoint" not in json.dumps(profile).lower()


def test_browser_synthesis_returns_playback_metadata():
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/voice/synthesize",
        json={
            "text": "What matters most to you?",
            "delivery": {"kind": "question"},
        },
    )

    assert response.status_code == 200
    assert response.headers["X-Voice-Playback"] == "browser"
    assert response.headers["X-Voice-Provider"] == "browser"

    payload = response.json()

    assert payload["playback"] == "browser"
    assert payload["delivery"]["kind"] == "question"
    assert payload["delivery"]["language"] == "en-IN"


def test_audio_synthesis_returns_provider_audio_bytes(monkeypatch):
    monkeypatch.setenv("CVI_VOICE_TTS_PROVIDER", "groq")

    def fake_synthesize(text, *, primary, fallbacks, delivery):
        assert text == "Got it."
        assert primary == "groq"
        assert delivery["kind"] == "acknowledgement"

        return (
            TTSResult(
                audio_bytes=b"audio-bytes",
                content_type="audio/mpeg",
                provider="groq",
                playback="audio",
                metadata={"model": "playai-tts"},
            ),
            [{"provider": "browser", "reason": "configured_after_cloud"}],
        )

    monkeypatch.setattr(
        api,
        "synthesize_with_fallback",
        fake_synthesize,
    )
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/voice/synthesize",
        json={
            "text": "Got it.",
            "delivery": {"kind": "acknowledgement"},
        },
    )

    assert response.status_code == 200
    assert response.content == b"audio-bytes"
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["X-Voice-Playback"] == "audio"
    assert response.headers["X-Voice-Provider"] == "groq"
    assert json.loads(response.headers["X-Voice-Fallbacks"]) == [
        {"provider": "browser", "reason": "configured_after_cloud"}
    ]


def test_synthesis_validation_and_failure_codes(monkeypatch):
    client = TestClient(api.app)

    missing = client.post(
        "/sessions/missing-session/voice/synthesize",
        json={"text": "Hello."},
    )

    assert missing.status_code == 404

    bad_delivery = client.post(
        f"/sessions/{SESSION_ID}/voice/synthesize",
        json={
            "text": "Hello.",
            "delivery": {"kind": "unknown-kind"},
        },
    )

    assert bad_delivery.status_code == 422

    def failing_synthesize(*args, **kwargs):
        raise TTSProviderError("All TTS providers failed.")

    monkeypatch.setattr(
        api,
        "synthesize_with_fallback",
        failing_synthesize,
    )

    failed = client.post(
        f"/sessions/{SESSION_ID}/voice/synthesize",
        json={"text": "Hello."},
    )

    assert failed.status_code == 503
    assert failed.json()["detail"] == "All TTS providers failed."
