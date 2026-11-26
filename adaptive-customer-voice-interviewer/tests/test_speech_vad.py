import pytest

from deploy.speech import segmenter as speech_segmenter
from deploy.speech import vad as speech_vad


FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2


def frame(value=1000):
    return value.to_bytes(2, "little", signed=False) * FRAME_SAMPLES


def config(**overrides):
    values = dict(
        sample_rate=16000,
        frame_samples=FRAME_SAMPLES,
        min_speech_ms=64,
        end_silence_ms=96,
        max_segment_ms=320,
        min_segment_ms=64,
        cooldown_ms=32,
        tail_silence_ms=32,
    )
    values.update(overrides)

    return speech_segmenter.SegmentationConfig(**values)


def test_short_noise_burst_never_confirms_speech():
    detector = speech_segmenter.SpeechSegmenter(config())

    events = []

    for _ in range(1):
        events.extend(detector.feed(True, frame()))

    for _ in range(4):
        events.extend(detector.feed(False, frame(0)))

    assert events == []


def test_confirmed_speech_and_silence_produces_one_segment():
    detector = speech_segmenter.SpeechSegmenter(config())
    events = []

    for _ in range(3):
        events.extend(detector.feed(True, frame()))

    assert [event.type for event in events] == ["speech_start"]

    for _ in range(3):
        events.extend(detector.feed(False, frame(0)))

    ends = [event for event in events if event.type == "speech_end"]

    assert len(ends) == 1
    assert ends[0].speech_ms == 96
    assert ends[0].reason == "silence"
    # Three speech frames plus one retained tail-silence frame.
    assert len(ends[0].audio) == FRAME_BYTES * 4


def test_thinking_pause_does_not_fragment_one_answer():
    detector = speech_segmenter.SpeechSegmenter(config())
    events = []

    for _ in range(3):
        events.extend(detector.feed(True, frame()))
    for _ in range(2):
        events.extend(detector.feed(False, frame(0)))
    for _ in range(3):
        events.extend(detector.feed(True, frame()))
    for _ in range(3):
        events.extend(detector.feed(False, frame(0)))

    assert [event.type for event in events] == ["speech_start", "speech_end"]
    assert events[1].speech_ms == 192


def test_maximum_segment_duration_forces_an_end():
    detector = speech_segmenter.SpeechSegmenter(
        config(max_segment_ms=160, end_silence_ms=10000)
    )
    events = []

    for _ in range(6):
        events.extend(detector.feed(True, frame()))

    ends = [event for event in events if event.type == "speech_end"]

    assert len(ends) == 1
    assert ends[0].reason == "max_duration"


def test_cooldown_prevents_immediate_retrigger_after_segment_end():
    detector = speech_segmenter.SpeechSegmenter(config())

    for _ in range(3):
        detector.feed(True, frame())
    for _ in range(3):
        detector.feed(False, frame(0))

    assert detector.feed(True, frame()) == []


def test_energy_provider_adapts_to_noise_floor_without_model():
    provider = speech_vad.EnergyVADProvider(threshold=300, multiplier=2.0)

    for _ in range(20):
        assert provider.is_speech(frame(100)) is False

    assert provider.is_speech(frame(1200)) is True


def test_vad_factory_selects_provider_and_rejects_unknown(monkeypatch):
    monkeypatch.setenv("CVI_SPEECH_VAD_PROVIDER", "energy")

    provider = speech_vad.create_vad_provider()

    assert isinstance(provider, speech_vad.EnergyVADProvider)
    assert provider.frame_samples == 512

    monkeypatch.setenv("CVI_SPEECH_VAD_PROVIDER", "nope")

    with pytest.raises(ValueError):
        speech_vad.create_vad_provider()


def test_silero_provider_is_lazy_and_threshold_is_mockable(monkeypatch):
    calls = []

    def fake_model(tensor, sample_rate):
        calls.append(sample_rate)
        return 0.8

    monkeypatch.setattr(
        speech_vad,
        "_load_silero_model",
        lambda: fake_model,
    )

    provider = speech_vad.SileroVADProvider(threshold=0.5)

    assert calls == []
    assert provider.is_speech(frame()) is True
    assert calls == [16000]
