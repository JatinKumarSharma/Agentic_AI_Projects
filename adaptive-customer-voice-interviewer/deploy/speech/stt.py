"""Local STT engine boundary for the speech service.

The API keeps its own provider chain for batch transcription. The streaming
service uses this narrow boundary so faster-whisper can be replaced without
touching turn detection or the interview engine.
"""

import gc
import io
import logging
import os
import resource
import wave
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


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


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _rss_mb() -> float | None:
    """Best-effort process RSS for low-memory diagnostics."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass

    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports ru_maxrss in KiB; macOS reports bytes.
        if rss > 10_000_000:
            return round(rss / (1024 * 1024), 1)
        return round(rss / 1024, 1)
    except Exception:
        return None


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
        self.cpu_threads = _env_int("CVI_LOCAL_STT_CPU_THREADS", 1, minimum=1)
        self.num_workers = _env_int("CVI_LOCAL_STT_NUM_WORKERS", 1, minimum=1)
        self.beam_size = _env_int("CVI_LOCAL_STT_BEAM_SIZE", 1, minimum=1)
        self.best_of = _env_int("CVI_LOCAL_STT_BEST_OF", 1, minimum=1)
        self.vad_filter = _env_bool("CVI_LOCAL_STT_VAD_FILTER", False)
        self.without_timestamps = _env_bool(
            "CVI_LOCAL_STT_WITHOUT_TIMESTAMPS", True
        )
        self.condition_on_previous_text = _env_bool(
            "CVI_LOCAL_STT_CONDITION_ON_PREVIOUS_TEXT", False
        )
        self.max_audio_seconds = float(
            os.getenv("CVI_LOCAL_STT_MAX_AUDIO_SECONDS", "12").strip() or "12"
        )
        self._model = None
        self._loaded_model = None

    def _get_model(self):
        if self._model is None or self._loaded_model != self.model:
            from faster_whisper import WhisperModel

            before = _rss_mb()
            self._model = WhisperModel(
                self.model,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.cpu_threads,
                num_workers=self.num_workers,
            )
            self._loaded_model = self.model
            logger.info(
                "faster_whisper_model_loaded model=%s device=%s compute_type=%s cpu_threads=%s num_workers=%s rss_mb=%s rss_before_mb=%s",
                self.model,
                self.device,
                self.compute_type,
                self.cpu_threads,
                self.num_workers,
                _rss_mb(),
                before,
            )

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

        max_samples = int(self.max_audio_seconds * 16000)
        if max_samples > 0 and len(audio) > max_samples:
            audio = audio[:max_samples]

        before = _rss_mb()
        try:
            segments, info = model.transcribe(
                audio,
                language=code or None,
                initial_prompt=prompt or None,
                vad_filter=self.vad_filter,
                beam_size=self.beam_size,
                best_of=self.best_of,
                temperature=0.0,
                condition_on_previous_text=self.condition_on_previous_text,
                without_timestamps=self.without_timestamps,
            )
            text = "".join(segment.text for segment in segments).strip()
        finally:
            # Drop large numpy/transcription intermediates promptly on 512MB hosts.
            try:
                del audio
            except Exception:
                pass
            gc.collect()

        after = _rss_mb()
        logger.info(
            "faster_whisper_transcribed model=%s duration_s=%s text_chars=%s rss_before_mb=%s rss_after_mb=%s",
            self.model,
            getattr(info, "duration", None),
            len(text),
            before,
            after,
        )

        return {
            "text": text,
            "language": getattr(info, "language", None) or language,
            "confidence": getattr(info, "avg_logprob", None),
            "metadata": {
                "engine": self.name,
                "model": self.model,
                "duration": getattr(info, "duration", None),
                "language_probability": getattr(info, "language_probability", None),
                "beam_size": self.beam_size,
                "cpu_threads": self.cpu_threads,
                "num_workers": self.num_workers,
                "vad_filter": self.vad_filter,
                "without_timestamps": self.without_timestamps,
                "rss_mb": after,
            },
        }

    def warm(self) -> dict:
        self._get_model()

        return {
            "engine": self.name,
            "status": "ready",
            "model": self.model,
            "cpu_threads": self.cpu_threads,
            "num_workers": self.num_workers,
            "beam_size": self.beam_size,
            "vad_filter": self.vad_filter,
            "rss_mb": _rss_mb(),
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
        os.getenv("CVI_LOCAL_STT_CPU_THREADS", "1"),
        os.getenv("CVI_LOCAL_STT_NUM_WORKERS", "1"),
        os.getenv("CVI_LOCAL_STT_BEAM_SIZE", "1"),
        os.getenv("CVI_LOCAL_STT_VAD_FILTER", "false"),
        os.getenv("CVI_LOCAL_STT_WITHOUT_TIMESTAMPS", "true"),
        os.getenv("CVI_LOCAL_STT_CONDITION_ON_PREVIOUS_TEXT", "false"),
        os.getenv("CVI_LOCAL_STT_MAX_AUDIO_SECONDS", "12"),
        os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
    )

    if key not in _engine_cache:
        if provider_name == "groq":
            _engine_cache[key] = GroqSTTEngine()
        else:
            _engine_cache[key] = FasterWhisperSTTEngine()

    return _engine_cache[key]
