"""Provider-neutral speech-to-text adapters.

No provider SDK is imported at module load. Groq and local deployments are
called through small HTTP adapters so tests stay offline and an enterprise can
point CVI at a self-hosted STT service with configuration alone.
"""

import io
import json
import os
import uuid
import wave
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_GROQ_STT_ENDPOINT = (
    "https://api.groq.com/openai/v1/audio/transcriptions"
)


class STTProviderError(RuntimeError):
    """Raised when an STT provider cannot produce a transcript."""


def stt_language_code(language: Optional[str]) -> Optional[str]:
    """Convert a display locale to the ISO-639-1 code STT APIs expect.

    The voice profile keeps BCP-47 locales such as ``en-IN`` for speech
    delivery, but transcription endpoints require a two-letter language code.
    """

    if not language:
        return None

    code = language.strip().split("-")[0].split("_")[0].lower()

    return code or None


@dataclass(frozen=True)
class STTResult:
    text: str
    provider: str
    language: Optional[str] = None
    confidence: Optional[float] = None
    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "provider": self.provider,
            "language": self.language,
            "confidence": self.confidence,
            "metadata": self.metadata or {},
        }


class STTProvider:
    name = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "response.wav",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> STTResult:
        raise NotImplementedError


def pcm16_chunks_to_wav(
    chunks: list[bytes],
    sample_rate: int = 16000,
) -> bytes:
    """Wrap recorded mono PCM16 chunks in an in-memory WAV container."""

    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"".join(chunks))

    return buffer.getvalue()


def _multipart_request(
    url: str,
    *,
    fields: dict[str, str],
    file_name: str,
    file_bytes: bytes,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict:
    boundary = f"----cvi-{uuid.uuid4().hex}"
    body = bytearray()

    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        )
        body.extend(f"{value}\r\n".encode())

    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{file_name}"\r\n'
        ).encode()
    )
    body.extend(b"Content-Type: audio/wav\r\n\r\n")
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    request_headers = {
        "Accept": "application/json",
        "User-Agent": "customer-voice-interviewer/1.0",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        **(headers or {}),
    }

    request = Request(
        url,
        data=bytes(body),
        headers=request_headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:300]
        except Exception:
            detail = ""

        reason = detail or str(exc.reason or "")

        raise STTProviderError(
            f"STT HTTP {exc.code}: {reason}"
        ) from exc
    except Exception as exc:
        raise STTProviderError(
            f"STT HTTP request failed: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise STTProviderError("STT provider returned invalid JSON.") from exc

    if not isinstance(result, dict):
        raise STTProviderError("STT provider returned an unexpected payload.")

    return result


class GroqSTTProvider(STTProvider):
    """Groq Whisper transcription using the free-tier-compatible endpoint."""

    name = "groq"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        endpoint: str | None = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key if api_key is not None else os.getenv(
            "GROQ_API_KEY"
        )
        self.model = model or os.getenv(
            "GROQ_STT_MODEL",
            "whisper-large-v3-turbo",
        )
        self.endpoint = endpoint or os.getenv(
            "CVI_GROQ_STT_ENDPOINT",
            DEFAULT_GROQ_STT_ENDPOINT,
        )
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "response.wav",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> STTResult:
        if not self.is_available():
            raise STTProviderError("GROQ_API_KEY is not configured.")

        fields = {"model": self.model, "response_format": "verbose_json"}

        language_code = stt_language_code(language)

        if language_code:
            fields["language"] = language_code

        if prompt:
            fields["prompt"] = prompt

        result = _multipart_request(
            self.endpoint,
            fields=fields,
            file_name=filename,
            file_bytes=audio,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )

        text = str(result.get("text") or "").strip()

        return STTResult(
            text=text,
            provider=self.name,
            language=language or result.get("language"),
            confidence=_extract_confidence(result),
            metadata={
                "model": self.model,
                "duration": result.get("duration"),
            },
        )


class LocalHTTPSTTProvider(STTProvider):
    """Generic adapter for a self-hosted STT service."""

    name = "local"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ):
        self.endpoint = endpoint or os.getenv("CVI_LOCAL_STT_URL")
        self.api_key = api_key if api_key is not None else os.getenv(
            "CVI_LOCAL_STT_API_KEY"
        )
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.endpoint)

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "response.wav",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> STTResult:
        if not self.is_available():
            raise STTProviderError("CVI_LOCAL_STT_URL is not configured.")

        fields = {}

        language_code = stt_language_code(language)

        if language_code:
            fields["language"] = language_code

        if prompt:
            fields["prompt"] = prompt

        headers = {}

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        result = _multipart_request(
            self.endpoint,
            fields=fields,
            file_name=filename,
            file_bytes=audio,
            headers=headers,
            timeout=self.timeout,
        )

        text = str(
            result.get("text")
            or result.get("transcript")
            or ""
        ).strip()

        return STTResult(
            text=text,
            provider=self.name,
            language=language or result.get("language"),
            confidence=result.get("confidence"),
            metadata={"endpoint_configured": True},
        )


class TypedSTTProvider(STTProvider):
    """Terminal fallback: no speech transcription, typed answers only."""

    name = "typed"

    def is_available(self) -> bool:
        return True

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "response.wav",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> STTResult:
        return STTResult(
            text="",
            provider=self.name,
            language=language,
            metadata={"typed_fallback": True},
        )


PROVIDERS = {
    "groq": GroqSTTProvider,
    "local": LocalHTTPSTTProvider,
    "typed": TypedSTTProvider,
}


def _extract_confidence(result: dict) -> Optional[float]:
    for key in ("confidence", "avg_logprob"):
        value = result.get(key)

        if isinstance(value, (int, float)):
            return float(value)

    segments = result.get("segments")

    if isinstance(segments, list) and segments:
        first_segment = segments[0]

        if isinstance(first_segment, dict):
            value = first_segment.get("avg_logprob")

            if isinstance(value, (int, float)):
                return float(value)

    return None


def create_stt_provider(name: str | None = None) -> STTProvider:
    """Create one configured STT provider without importing provider SDKs."""

    provider_name = (name or os.getenv(
        "CVI_VOICE_STT_PROVIDER",
        "groq",
    )).strip().lower()

    provider_class = PROVIDERS.get(provider_name)

    if provider_class is None:
        raise STTProviderError(
            f"Unsupported STT provider: {provider_name}"
        )

    return provider_class()


def stt_chain(primary: str, fallbacks: tuple[str, ...]) -> tuple[str, ...]:
    chain = []

    for name in (primary,) + fallbacks:
        if name not in chain:
            chain.append(name)

    return tuple(chain)


def transcribe_with_fallback(
    audio: bytes,
    *,
    primary: str,
    fallbacks: tuple[str, ...],
    filename: str = "response.wav",
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> tuple[STTResult, list[dict]]:
    """Try configured providers in order and report every failed attempt."""

    failures = []
    last_result: Optional[STTResult] = None

    for name in stt_chain(primary, fallbacks):
        provider = create_stt_provider(name)

        if not provider.is_available():
            failures.append({"provider": name, "reason": "unavailable"})
            continue

        try:
            result = provider.transcribe(
                audio,
                filename=filename,
                language=language,
                prompt=prompt,
            )
        except STTProviderError as exc:
            failures.append(
                {"provider": name, "reason": str(exc)}
            )
            continue

        last_result = result

        if result.text.strip() or name == "typed":
            return result, failures

        failures.append(
            {"provider": name, "reason": "empty transcript"}
        )

    if last_result is not None:
        return last_result, failures

    raise STTProviderError(
        "No configured STT provider could transcribe the audio."
    )
