"""Provider-neutral voice delivery configuration.

The profile describes *how* the interviewer should sound and which provider
chains may satisfy STT/TTS. Providers are deliberately not imported here so
enterprise deployments can swap adapters without changing the research graph.
"""

import os
from dataclasses import dataclass
from typing import Literal


DeliveryKind = Literal[
    "question",
    "acknowledgement",
    "reflection",
    "system",
    "recovery",
]

VoiceProviderName = Literal[
    "browser",
    "browser_speech",
    "groq",
    "local",
    "sarvam",
    "typed",
]


SOFTNESS_PROFILES = {
    "neutral": {
        "rate": 1.0,
        "pitch": 1.0,
        "volume": 1.0,
    },
    "gentle": {
        "rate": 0.97,
        "pitch": 1.01,
        "volume": 0.90,
    },
    "soft": {
        "rate": 0.93,
        "pitch": 1.02,
        "volume": 0.78,
    },
}

BASE_PROSODY = {
    "question": {"rate": 0.94, "pitch": 1.0, "volume": 1.0},
    "acknowledgement": {"rate": 1.05, "pitch": 1.02, "volume": 0.95},
    "reflection": {"rate": 0.96, "pitch": 1.0, "volume": 0.93},
    "system": {"rate": 0.98, "pitch": 1.0, "volume": 0.92},
    "recovery": {"rate": 0.92, "pitch": 1.0, "volume": 0.94},
}


@dataclass(frozen=True)
class Prosody:
    rate: float
    pitch: float
    volume: float

    def to_dict(self) -> dict:
        return {
            "rate": round(self.rate, 3),
            "pitch": round(self.pitch, 3),
            "volume": round(self.volume, 3),
        }


@dataclass(frozen=True)
class VoiceProfile:
    language: str
    softness: str
    tts_provider: str
    stt_provider: str
    tts_fallbacks: tuple[str, ...]
    stt_fallbacks: tuple[str, ...]
    voice: str | None
    question: Prosody
    acknowledgement: Prosody
    reflection: Prosody
    system: Prosody
    recovery: Prosody

    def prosody_for(self, kind: DeliveryKind) -> Prosody:
        try:
            return getattr(self, kind)
        except AttributeError as exc:
            raise ValueError(f"Unknown delivery kind: {kind}") from exc

    def to_public_dict(self) -> dict:
        """Return participant-safe voice settings (no keys or endpoints)."""

        return {
            "language": self.language,
            "softness": self.softness,
            "tts_provider": self.tts_provider,
            "stt_provider": self.stt_provider,
            "tts_fallbacks": list(self.tts_fallbacks),
            "stt_fallbacks": list(self.stt_fallbacks),
            "voice": self.voice,
            "prosody": {
                kind: self.prosody_for(kind).to_dict()
                for kind in BASE_PROSODY
            },
        }


def _env_float(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc

    return max(minimum, min(maximum, value))


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    return tuple(
        item.strip()
        for item in raw.split(",")
        if item.strip()
    )


def get_voice_profile() -> VoiceProfile:
    """Build the active profile from environment configuration."""

    softness = os.getenv("CVI_VOICE_SOFTNESS", "gentle").strip().lower()

    if softness not in SOFTNESS_PROFILES:
        raise ValueError(
            "CVI_VOICE_SOFTNESS must be one of "
            f"{', '.join(SOFTNESS_PROFILES)}."
        )

    adjustment = SOFTNESS_PROFILES[softness]
    global_rate = _env_float("CVI_VOICE_RATE", 1.0, 0.5, 1.5)
    global_pitch = _env_float("CVI_VOICE_PITCH", 1.0, 0.5, 1.5)
    global_volume = _env_float("CVI_VOICE_VOLUME", 1.0, 0.0, 1.0)

    prosody_values = {}

    for kind, base in BASE_PROSODY.items():
        prosody_values[kind] = Prosody(
            rate=base["rate"]
            * adjustment["rate"]
            * global_rate,
            pitch=base["pitch"]
            * adjustment["pitch"]
            * global_pitch,
            volume=base["volume"]
            * adjustment["volume"]
            * global_volume,
        )

    voice = os.getenv("CVI_VOICE_TTS_VOICE", "").strip() or None

    return VoiceProfile(
        language=os.getenv("CVI_VOICE_LANGUAGE", "en-IN").strip(),
        softness=softness,
        tts_provider=os.getenv(
            "CVI_VOICE_TTS_PROVIDER",
            "browser",
        ).strip().lower(),
        stt_provider=os.getenv(
            "CVI_VOICE_STT_PROVIDER",
            "groq",
        ).strip().lower(),
        tts_fallbacks=_env_list(
            "CVI_VOICE_TTS_FALLBACKS",
            ("browser", "local"),
        ),
        stt_fallbacks=_env_list(
            "CVI_VOICE_STT_FALLBACKS",
            ("groq", "local", "typed"),
        ),
        voice=voice,
        **prosody_values,
    )
