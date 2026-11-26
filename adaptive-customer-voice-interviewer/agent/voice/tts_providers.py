"""Provider-neutral text-to-speech adapters.

The research plane emits typed voice events with delivery metadata. These
adapters turn that metadata into provider-specific synthesis requests without
leaking provider SDKs into the graph or the participant UI.
"""

import json
import os
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_GROQ_TTS_ENDPOINT = (
    "https://api.groq.com/openai/v1/audio/speech"
)

BROWSER_CONTENT_TYPE = "application/x-browser-speech-synthesis"


class TTSProviderError(RuntimeError):
    """Raised when a TTS provider cannot synthesize speech."""


@dataclass(frozen=True)
class TTSResult:
    audio_bytes: bytes
    content_type: str
    provider: str
    playback: str
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "content_type": self.content_type,
            "provider": self.provider,
            "playback": self.playback,
            "audio_bytes": len(self.audio_bytes),
            "metadata": self.metadata or {},
        }


class TTSProvider:
    name = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def synthesize(
        self,
        text: str,
        delivery: Optional[dict] = None,
    ) -> TTSResult:
        raise NotImplementedError


class BrowserTTSProvider(TTSProvider):
    """Browser-native playback; returns metadata instead of audio bytes."""

    name = "browser"

    def is_available(self) -> bool:
        return True

    def synthesize(
        self,
        text: str,
        delivery: Optional[dict] = None,
    ) -> TTSResult:
        return TTSResult(
            audio_bytes=b"",
            content_type=BROWSER_CONTENT_TYPE,
            provider=self.name,
            playback="browser",
            metadata={
                "text_length": len(text),
                "delivery": delivery or {},
            },
        )


class GroqTTSProvider(TTSProvider):
    """Groq TTS through an OpenAI-compatible audio endpoint."""

    name = "groq"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        endpoint: str | None = None,
        timeout: float = 60.0,
    ):
        self.api_key = api_key if api_key is not None else os.getenv(
            "GROQ_API_KEY"
        )
        self.model = model or os.getenv(
            "GROQ_TTS_MODEL",
            "playai-tts",
        )
        self.voice = (
            voice
            or os.getenv("CVI_VOICE_TTS_VOICE")
            or os.getenv("GROQ_TTS_VOICE")
            or "Arista"
        )
        self.endpoint = endpoint or os.getenv(
            "CVI_GROQ_TTS_ENDPOINT",
            DEFAULT_GROQ_TTS_ENDPOINT,
        )
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    def synthesize(
        self,
        text: str,
        delivery: Optional[dict] = None,
    ) -> TTSResult:
        if not self.is_available():
            raise TTSProviderError("GROQ_API_KEY is not configured.")

        active_delivery = delivery or {}
        payload = {
            "model": self.model,
            "voice": active_delivery.get("voice") or self.voice,
            "input": text,
            "response_format": "mp3",
            "speed": _bounded_rate(active_delivery.get("rate", 1.0)),
        }

        audio_bytes, content_type = _audio_request(
            self.endpoint,
            payload=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

        return TTSResult(
            audio_bytes=audio_bytes,
            content_type=content_type,
            provider=self.name,
            playback="audio",
            metadata={
                "model": self.model,
                "voice": payload["voice"],
                "language": active_delivery.get("language"),
                "pitch": active_delivery.get("pitch"),
                "volume": active_delivery.get("volume"),
            },
        )


class LocalHTTPTTSProvider(TTSProvider):
    """Generic adapter for a self-hosted neural TTS service."""

    name = "local"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ):
        self.endpoint = endpoint or os.getenv("CVI_LOCAL_TTS_URL")
        self.api_key = api_key if api_key is not None else os.getenv(
            "CVI_LOCAL_TTS_API_KEY"
        )
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.endpoint)

    def synthesize(
        self,
        text: str,
        delivery: Optional[dict] = None,
    ) -> TTSResult:
        if not self.is_available():
            raise TTSProviderError("CVI_LOCAL_TTS_URL is not configured.")

        active_delivery = delivery or {}
        payload = {
            "text": text,
            "language": active_delivery.get("language"),
            "voice": active_delivery.get("voice"),
            "rate": _bounded_rate(active_delivery.get("rate", 1.0)),
            "pitch": active_delivery.get("pitch", 1.0),
            "volume": active_delivery.get("volume", 1.0),
            "format": "mp3",
        }

        headers = {}

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        audio_bytes, content_type = _audio_request(
            self.endpoint,
            payload=payload,
            headers=headers,
            timeout=self.timeout,
        )

        return TTSResult(
            audio_bytes=audio_bytes,
            content_type=content_type,
            provider=self.name,
            playback="audio",
            metadata={
                "endpoint_configured": True,
                "voice": payload["voice"],
                "language": payload["language"],
            },
        )


PROVIDERS = {
    "browser": BrowserTTSProvider,
    "groq": GroqTTSProvider,
    "local": LocalHTTPTTSProvider,
}


def _bounded_rate(value) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 1.0

    return max(0.5, min(1.5, rate))


def _audio_request(
    url: str,
    *,
    payload: dict,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[bytes, str]:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            audio_bytes = response.read()
            content_type = response.headers.get_content_type()
    except (HTTPError, URLError, OSError) as exc:
        raise TTSProviderError(
            f"TTS HTTP request failed: {type(exc).__name__}"
        ) from exc

    if not audio_bytes:
        raise TTSProviderError("TTS provider returned empty audio.")

    return audio_bytes, content_type


def create_tts_provider(name: str | None = None) -> TTSProvider:
    """Create one configured TTS provider without importing provider SDKs."""

    provider_name = (name or os.getenv(
        "CVI_VOICE_TTS_PROVIDER",
        "browser",
    )).strip().lower()

    provider_class = PROVIDERS.get(provider_name)

    if provider_class is None:
        raise TTSProviderError(
            f"Unsupported TTS provider: {provider_name}"
        )

    return provider_class()


def tts_chain(primary: str, fallbacks: tuple[str, ...]) -> tuple[str, ...]:
    chain = []

    for name in (primary,) + fallbacks:
        if name not in chain:
            chain.append(name)

    return tuple(chain)


def synthesize_with_fallback(
    text: str,
    *,
    primary: str,
    fallbacks: tuple[str, ...],
    delivery: Optional[dict] = None,
) -> tuple[TTSResult, list[dict]]:
    """Try configured providers in order and report every failed attempt."""

    if not text.strip():
        raise TTSProviderError("TTS input cannot be empty.")

    failures = []

    for name in tts_chain(primary, fallbacks):
        provider = create_tts_provider(name)

        if not provider.is_available():
            failures.append({"provider": name, "reason": "unavailable"})
            continue

        try:
            result = provider.synthesize(text, delivery)
        except TTSProviderError as exc:
            failures.append({"provider": name, "reason": str(exc)})
            continue

        return result, failures

    raise TTSProviderError(
        "No configured TTS provider could synthesize the text."
    )
