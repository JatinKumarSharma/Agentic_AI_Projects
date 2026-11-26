import json

import pytest
from pytest import approx

from agent.voice.voice_profile import get_voice_profile


VOICE_ENV_NAMES = (
    "CVI_VOICE_LANGUAGE",
    "CVI_VOICE_SOFTNESS",
    "CVI_VOICE_RATE",
    "CVI_VOICE_PITCH",
    "CVI_VOICE_VOLUME",
    "CVI_VOICE_STT_PROVIDER",
    "CVI_VOICE_STT_FALLBACKS",
    "CVI_VOICE_TTS_PROVIDER",
    "CVI_VOICE_TTS_FALLBACKS",
    "CVI_VOICE_TTS_VOICE",
)


@pytest.fixture(autouse=True)
def clean_voice_env(monkeypatch):
    for name in VOICE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_default_profile_is_english_indian_gentle():
    profile = get_voice_profile()

    assert profile.language == "en-IN"
    assert profile.softness == "gentle"
    assert profile.stt_provider == "groq"
    assert profile.tts_provider == "browser"
    assert profile.stt_fallbacks == ("groq", "local", "typed")
    assert profile.tts_fallbacks == ("browser", "local")

    question = profile.prosody_for("question")
    acknowledgement = profile.prosody_for("acknowledgement")

    assert question.rate == approx(0.94 * 0.97, abs=0.001)
    assert question.volume == approx(1.0 * 0.90, abs=0.001)
    assert acknowledgement.rate > question.rate
    assert acknowledgement.volume <= 1.0


def test_profile_bounds_global_overrides(monkeypatch):
    monkeypatch.setenv("CVI_VOICE_SOFTNESS", "soft")
    monkeypatch.setenv("CVI_VOICE_RATE", "9")
    monkeypatch.setenv("CVI_VOICE_VOLUME", "0.25")
    monkeypatch.setenv("CVI_VOICE_TTS_PROVIDER", "groq")
    monkeypatch.setenv("CVI_VOICE_TTS_FALLBACKS", "groq, browser ,local")
    monkeypatch.setenv("CVI_VOICE_TTS_VOICE", "Arista")

    profile = get_voice_profile()
    question = profile.prosody_for("question")

    assert profile.softness == "soft"
    assert profile.tts_provider == "groq"
    assert profile.tts_fallbacks == ("groq", "browser", "local")
    assert profile.voice == "Arista"
    assert question.rate == approx(0.94 * 0.93 * 1.5, abs=0.001)
    assert question.volume == approx(1.0 * 0.78 * 0.25, abs=0.001)


def test_invalid_softness_is_rejected(monkeypatch):
    monkeypatch.setenv("CVI_VOICE_SOFTNESS", "shouting")

    with pytest.raises(ValueError, match="CVI_VOICE_SOFTNESS"):
        get_voice_profile()


def test_public_profile_contains_no_secrets_or_endpoints():
    public = get_voice_profile().to_public_dict()
    serialized = json.dumps(public).lower()

    assert public["language"] == "en-IN"
    assert public["prosody"]["reflection"]["rate"] > 0
    assert "api_key" not in serialized
    assert "endpoint" not in serialized
    assert "authorization" not in serialized
