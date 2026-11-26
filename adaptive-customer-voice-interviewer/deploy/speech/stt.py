"""Local STT engine boundary for the speech service.

The API keeps its own provider chain for batch transcription. The streaming
service uses this narrow boundary so faster-whisper can be replaced without
touching turn detection or the interview engine.
"""

import io
import os
import wave
from abc import ABC, abstractmethod


def pcm16_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap mono PCM16 bytes in an in-memory WAV container."""

    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)

    return buffer.getvalue()


def wav_to_float32(wav: bytes):
    """Decode a mono 16-bit WAV to float32 samples in [-1, 1].

    Passing a numpy array to faster-whisper avoids PyAV decoding and the
    av.open(metadata_errors=...) incompatibility seen with av>=19.
    """

    import numpy as np

    with wave.open(io.BytesIO(wav), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    return np.ascontiguousarray(audio, dtype=np.float32)


class STTEngine(ABC):
    name = "base"

    @abstractmethod
    def transcribe(
        self,
        wav: bytes,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> dict:
        """Return text, language, confidence and metadata for one WAV clip."""

    def warm(self) -> dict:
        return {"engine": self.name, "status": "ready"}


class FasterWhisperSTTEngine(STTEngine):
    name = "faster-whisper"

    def __init__(
        self,
        *,
        model: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ):
        self.model = model or os.getenv("CVI_LOCAL_STT_MODEL", "small")
        self.device = device or os.getenv("CVI_LOCAL_STT_DEVICE", "cpu")
        self.compute_type = compute_type or os.getenv(
            "CVI_LOCAL_STT_COMPUTE_TYPE", "int8"
        )
        self._model = None
        self._loaded_model = None

    def _get_model(self):
        if self._model is None or self._loaded_model != self.model:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._loaded_model = self.model

        return self._model

    def transcribe(
        self,
        wav: bytes,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> dict:
        model = self._get_model()
        code = (language or "").strip().split("-")[0].split("_")[0].lower()
        audio = wav_to_float32(wav)
        segments, info = model.transcribe(
            audio,
            language=code or None,
            initial_prompt=prompt or None,
            vad_filter=True,
        )

        text = "".join(segment.text for segment in segments).strip()

        return {
            "text": text,
            "language": getattr(info, "language", None) or language,
            "confidence": getattr(info, "avg_logprob", None),
            "metadata": {
                "engine": self.name,
                "model": self.model,
                "duration": getattr(info, "duration", None),
            },
        }

    def warm(self) -> dict:
        self._get_model()

        return {
            "engine": self.name,
            "status": "ready",
            "model": self.model,
        }


class GroqSTTEngine(STTEngine):
    """Hosted STT engine that delegates to CVI's provider boundary.

    This keeps turn detection on the server while avoiding local Whisper
    models on constrained hosts. The research package is imported lazily so
    the standalone local speech image remains independent.
    """

    name = "groq"

    def __init__(self, provider=None):
        self._provider = provider

    @property
    def provider(self):
        if self._provider is None:
            from agent.voice.stt_providers import create_stt_provider

            self._provider = create_stt_provider("groq")

        return self._provider

    def transcribe(
        self,
        wav: bytes,
        *,
        language: str | None = None,
        prompt: str | None = None,
    ) -> dict:
        result = self.provider.transcribe(
            wav,
            filename="segment.wav",
            language=language,
            prompt=prompt,
        )

        metadata = dict(result.metadata or {})
        metadata["engine"] = self.name

        return {
            "text": result.text,
            "language": result.language or language,
            "confidence": result.confidence,
            "metadata": metadata,
        }

    def warm(self) -> dict:
        return {"engine": self.name, "status": "ready"}


_engine_cache: dict[tuple, STTEngine] = {}


def create_stt_engine(name: str | None = None) -> STTEngine:
    provider_name = (name or os.getenv(
        "CVI_SPEECH_STT_ENGINE",
        "faster-whisper",
    )).strip().lower()

    if provider_name not in {"faster-whisper", "groq"}:
        raise ValueError(
            f"Unsupported speech STT engine: {provider_name}"
        )

    key = (
        provider_name,
        os.getenv("CVI_LOCAL_STT_MODEL", "small"),
        os.getenv("CVI_LOCAL_STT_DEVICE", "cpu"),
        os.getenv("CVI_LOCAL_STT_COMPUTE_TYPE", "int8"),
        os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
    )

    if key not in _engine_cache:
        if provider_name == "groq":
            _engine_cache[key] = GroqSTTEngine()
        else:
            _engine_cache[key] = FasterWhisperSTTEngine()

    return _engine_cache[key]
