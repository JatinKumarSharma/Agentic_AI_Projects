"""Provider-neutral voice activity detection boundary.

All Silero/torch imports happen inside ``_load_silero_model``, which returns a
callable taking ``(pcm16_bytes, sample_rate)``. Tests replace that one boundary
with a fake and never need a model download, microphone, or GPU.
"""

import os
import struct
from abc import ABC, abstractmethod


class VADProvider(ABC):
    name = "base"
    sample_rate = 16000
    frame_samples = 512

    @abstractmethod
    def is_speech(self, frame: bytes) -> bool:
        """Return whether one PCM16 frame contains speech."""

    def warm(self) -> dict:
        """Optionally load models ahead of the first participant."""

        return {"provider": self.name, "status": "ready"}


class EnergyVADProvider(VADProvider):
    """Dependency-free fallback with an adaptive noise floor.

    It is not a speech classifier, but it keeps offline operation working when
    a model file cannot be downloaded. It is also the deterministic reference
    implementation for the provider boundary.
    """

    name = "energy"

    def __init__(
        self,
        *,
        threshold: float = 300.0,
        multiplier: float = 1.8,
        alpha: float = 0.05,
    ):
        self.threshold = float(threshold)
        self.multiplier = float(multiplier)
        self.alpha = float(alpha)
        self._noise_floor = 0.0
        self._initialized = False

    @staticmethod
    def _rms(frame: bytes) -> float:
        if len(frame) % 2:
            raise ValueError("PCM16 frames must contain an even byte count.")

        if not frame:
            return 0.0

        samples = struct.unpack(f"<{len(frame) // 2}h", frame)
        total = 0.0

        for sample in samples:
            total += sample * sample

        return (total / len(samples)) ** 0.5

    def is_speech(self, frame: bytes) -> bool:
        rms = self._rms(frame)

        if not self._initialized:
            self._noise_floor = rms
            self._initialized = True

        effective = max(
            self.threshold,
            self._noise_floor * self.multiplier,
        )

        if rms > effective:
            return True

        self._noise_floor += self.alpha * (rms - self._noise_floor)
        return False


def _load_silero_model():
    """Load Silero VAD lazily and return a scored-frame callable."""

    import torch
    from silero_vad import load_silero_vad

    # One frame at a time; extra threads add latency without helping accuracy.
    torch.set_num_threads(1)
    model = load_silero_vad()

    def score_frame(frame: bytes, sample_rate: int) -> float:
        if len(frame) % 2:
            raise ValueError("PCM16 frames must contain an even byte count.")

        samples = struct.unpack(f"<{len(frame) // 2}h", frame)
        tensor = torch.tensor(samples, dtype=torch.float32).div(32768.0)
        score = model(tensor, sample_rate)

        return float(score.item() if hasattr(score, "item") else score)

    return score_frame


class SileroVADProvider(VADProvider):
    """Local neural VAD. The model is loaded on first use, not at import."""

    name = "silero"
    frame_samples = 512

    def __init__(self, *, threshold: float = 0.5):
        self.threshold = float(threshold)
        self._model = None

    def _get_model(self):
        if self._model is None:
            self._model = _load_silero_model()

        return self._model

    def is_speech(self, frame: bytes) -> bool:
        score_frame = self._get_model()
        return float(score_frame(frame, self.sample_rate)) >= self.threshold

    def warm(self) -> dict:
        self._get_model()

        return {
            "provider": self.name,
            "status": "ready",
            "frame_samples": self.frame_samples,
        }


PROVIDERS = {
    "silero": SileroVADProvider,
    "energy": EnergyVADProvider,
}


def create_vad_provider(name: str | None = None) -> VADProvider:
    """Create the configured provider without importing model SDKs eagerly."""

    provider_name = (
        name or os.getenv("CVI_SPEECH_VAD_PROVIDER", "silero")
    ).strip().lower()
    provider_class = PROVIDERS.get(provider_name)

    if provider_class is None:
        raise ValueError(
            f"Unsupported VAD provider: {provider_name}. "
            f"Expected one of: {', '.join(sorted(PROVIDERS))}."
        )

    if provider_class is SileroVADProvider:
        raw_threshold = os.getenv("CVI_SPEECH_VAD_THRESHOLD", "").strip()

        try:
            threshold = float(raw_threshold) if raw_threshold else 0.5
        except ValueError:
            threshold = 0.5

        return SileroVADProvider(threshold=threshold)

    return provider_class()
