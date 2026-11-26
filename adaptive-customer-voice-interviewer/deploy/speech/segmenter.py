"""Turn segmentation for a stream of VAD-scored PCM16 frames.

This module is pure Python. It never imports a model SDK and never touches the
network, so every boundary decision can be tested with synthetic frames.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SegmentationConfig:
    sample_rate: int = 16000
    frame_samples: int = 512
    min_speech_ms: float = 250
    end_silence_ms: float = 1800
    max_segment_ms: float = 30000
    min_segment_ms: float = 800
    cooldown_ms: float = 300
    tail_silence_ms: float = 200

    @property
    def frame_ms(self) -> float:
        return self.frame_samples / self.sample_rate * 1000

    @classmethod
    def from_env(cls) -> "SegmentationConfig":
        import os

        def number(name: str, default: float) -> float:
            raw = os.getenv(name, "").strip()

            if not raw:
                return default

            try:
                return float(raw)
            except ValueError:
                return default

        return cls(
            sample_rate=int(number("CVI_SPEECH_SAMPLE_RATE", 16000)),
            frame_samples=int(number("CVI_SPEECH_VAD_FRAME_SAMPLES", 512)),
            min_speech_ms=number("CVI_SPEECH_MIN_SPEECH_MS", 250),
            end_silence_ms=number("CVI_SPEECH_END_SILENCE_MS", 1800),
            max_segment_ms=number("CVI_SPEECH_MAX_SEGMENT_MS", 30000),
            min_segment_ms=number("CVI_SPEECH_MIN_SEGMENT_MS", 800),
            cooldown_ms=number("CVI_SPEECH_COOLDOWN_MS", 300),
            tail_silence_ms=number("CVI_SPEECH_TAIL_SILENCE_MS", 200),
        )


@dataclass(frozen=True)
class SegmentEvent:
    """One observable turn boundary.

    ``audio`` contains PCM16 bytes only for ``speech_end``. The caller wraps it
    in a WAV container before handing it to STT.
    """

    type: str
    audio: bytes = b""
    speech_ms: float = 0
    total_ms: float = 0
    reason: str | None = None


class SpeechSegmenter:
    """Turn detector driven by externally supplied speech decisions."""

    def __init__(self, config: SegmentationConfig | None = None):
        self.config = config or SegmentationConfig()
        self._cooldown_frames = 0
        self._reset_candidate()
        self._reset_segment()

    def _frames_for(self, milliseconds: float) -> int:
        return max(1, int(round(milliseconds / self.config.frame_ms)))

    def _reset_candidate(self) -> None:
        self._candidate: list[bytes] = []
        self._candidate_frames = 0
        self._candidate_speech_frames = 0

    def _reset_segment(self) -> None:
        self._segment: list[bytes] = []
        self._segment_frames = 0
        self._speech_frames = 0
        self._silence_frames = 0
        self._in_speech = False

    def _finish(self, reason: str) -> SegmentEvent:
        tail_frames = self._frames_for(self.config.tail_silence_ms)
        keep = max(
            0,
            len(self._segment) - self._silence_frames + tail_frames,
        )
        audio = b"".join(self._segment[:keep])
        speech_ms = self._speech_frames * self.config.frame_ms
        total_ms = self._segment_frames * self.config.frame_ms
        self._reset_segment()

        return SegmentEvent(
            type="speech_end",
            audio=audio,
            speech_ms=speech_ms,
            total_ms=total_ms,
            reason=reason,
        )

    def feed(self, speech: bool, pcm: bytes) -> list[SegmentEvent]:
        """Consume one frame and return any boundary events it produced."""

        expected = self.config.frame_samples * 2

        if len(pcm) != expected:
            raise ValueError(
                f"Expected {expected} PCM16 bytes, received {len(pcm)}."
            )

        events: list[SegmentEvent] = []

        # A short cooldown stops loud question audio or room noise from
        # immediately re-arming a turn after one has ended.
        if self._cooldown_frames > 0:
            self._cooldown_frames -= 1
            return events

        if not self._in_speech:
            if not speech:
                self._reset_candidate()
                return events

            self._candidate.append(pcm)
            self._candidate_frames += 1
            self._candidate_speech_frames += 1

            if (
                self._candidate_speech_frames
                >= self._frames_for(self.config.min_speech_ms)
            ):
                self._in_speech = True
                self._segment = list(self._candidate)
                self._segment_frames = self._candidate_frames
                self._speech_frames = self._candidate_speech_frames
                self._silence_frames = 0
                self._reset_candidate()
                events.append(SegmentEvent(type="speech_start"))

            return events

        self._segment.append(pcm)
        self._segment_frames += 1

        if speech:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        silence_limit = self._frames_for(self.config.end_silence_ms)

        if self._silence_frames >= silence_limit:
            if (
                self._speech_frames * self.config.frame_ms
                >= self.config.min_segment_ms
            ):
                events.append(self._finish("silence"))
            else:
                events.append(
                    SegmentEvent(
                        type="speech_discarded",
                        speech_ms=self._speech_frames * self.config.frame_ms,
                        reason="too_short",
                    )
                )
                self._reset_segment()

            self._cooldown_frames = self._frames_for(
                self.config.cooldown_ms
            )
            return events

        if (
            self._segment_frames
            >= self._frames_for(self.config.max_segment_ms)
        ):
            events.append(self._finish("max_duration"))
            self._cooldown_frames = self._frames_for(
                self.config.cooldown_ms
            )

        return events

    def flush(self) -> SegmentEvent | None:
        """Finish an in-progress turn when the participant presses stop."""

        if not self._in_speech:
            return None

        return self._finish("manual_stop")
