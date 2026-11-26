import json
import threading

from fastapi.testclient import TestClient

from deploy.speech import app as speech_app
from deploy.speech import stream as speech_stream
from deploy.speech.segmenter import SegmentationConfig, SpeechSegmenter


def frame(value=1000):
    return value.to_bytes(2, "little") * 512


class FakeVAD:
    name = "fake"
    sample_rate = 16000
    frame_samples = 512

    def is_speech(self, frame_bytes):
        return any(frame_bytes)

    def warm(self):
        return {"provider": "fake", "status": "ready"}


class FakeSTT:
    name = "fake"

    def __init__(self):
        self.calls = []

    def transcribe(self, wav, *, language=None, prompt=None):
        self.calls.append((wav, language, prompt))

        return {
            "text": "setup was smooth",
            "language": language,
            "confidence": 0.9,
            "metadata": {"engine": "fake"},
        }

    def warm(self):
        return {"engine": "fake", "status": "ready"}


def components(monkeypatch, stt=None):
    engine = stt or FakeSTT()

    monkeypatch.setattr(
        speech_stream,
        "create_stream_components",
        lambda: (
            FakeVAD(),
            SpeechSegmenter(
                SegmentationConfig(
                    sample_rate=16000,
                    frame_samples=512,
                    min_speech_ms=64,
                    end_silence_ms=96,
                    max_segment_ms=32000,
                    min_segment_ms=64,
                    cooldown_ms=32,
                    tail_silence_ms=32,
                )
            ),
            engine,
        ),
    )

    return engine


def test_stream_emits_turn_boundaries_then_transcript(monkeypatch):
    engine = components(monkeypatch)

    with TestClient(speech_app.app) as client:
        with client.websocket_connect("/stream") as ws:
            ready = ws.receive_json()

            assert ready["type"] == "stream_ready"
            assert ready["vad"] == "fake"

            ws.send_bytes(frame() * 3 + frame(0) * 3)
            messages = [ws.receive_json() for _ in range(4)]
            ws.send_text(json.dumps({"type": "stop"}))
            stopped = ws.receive_json()

    types = [message["type"] for message in messages]

    assert types == [
        "speech_start",
        "speech_end",
        "audio_segment",
        "transcript_final",
    ]
    assert messages[1]["speech_ms"] == 96
    assert messages[2]["utterance_idx"] == messages[3]["utterance_idx"]
    assert messages[3]["text"] == "setup was smooth"
    assert stopped["type"] == "stream_stopped"
    assert len(engine.calls) == 1
    assert engine.calls[0][0].startswith(b"RIFF")


def test_stream_discards_a_short_noise_burst(monkeypatch):
    components(monkeypatch)

    with TestClient(speech_app.app) as client:
        with client.websocket_connect("/stream") as ws:
            ws.receive_json()
            ws.send_bytes(frame() + frame(0) * 3)
            ws.send_text(json.dumps({"type": "stop"}))

            types = []

            while True:
                message = ws.receive_json()
                types.append(message["type"])

                if message["type"] == "stream_stopped":
                    break

    assert "speech_start" not in types
    assert "transcript_final" not in types


def test_stream_reports_invalid_control_without_closing(monkeypatch):
    components(monkeypatch)

    with TestClient(speech_app.app) as client:
        with client.websocket_connect("/stream") as ws:
            ws.receive_json()
            ws.send_text("not-json")
            warning = ws.receive_json()
            ws.send_text(json.dumps({"type": "unknown"}))
            second_warning = ws.receive_json()

    assert warning["type"] == "warning"
    assert second_warning["type"] == "warning"


def test_speech_end_is_emitted_before_transcription_finishes(monkeypatch):
    release = threading.Event()

    class BlockingSTT(FakeSTT):
        def transcribe(self, wav, *, language=None, prompt=None):
            release.wait(timeout=5)

            return super().transcribe(
                wav,
                language=language,
                prompt=prompt,
            )

    components(monkeypatch, BlockingSTT())

    with TestClient(speech_app.app) as client:
        with client.websocket_connect("/stream") as ws:
            ws.receive_json()
            ws.send_bytes(frame() * 3 + frame(0) * 3)

            types = []

            while True:
                message = ws.receive_json()
                types.append(message["type"])

                if message["type"] == "speech_end":
                    release.set()

                if message["type"] == "transcript_final":
                    break

    assert types.index("speech_end") < types.index("audio_segment")
    assert "speech_end" in types
    assert release.is_set()


def test_warmup_includes_vad_and_streaming_stt(monkeypatch):
    engine = FakeSTT()
    monkeypatch.setattr(
        speech_stream,
        "create_stream_components",
        lambda: (FakeVAD(), SpeechSegmenter(), engine),
    )
    monkeypatch.setattr(
        speech_app,
        "warm_vad",
        lambda: {"provider": "fake", "status": "ready"},
    )
    monkeypatch.setattr(
        speech_app,
        "warm_transcriber",
        lambda: {"engine": "fake", "status": "ready"},
    )
    monkeypatch.setattr(
        speech_app,
        "warm_voice",
        lambda: {"status": "ready", "model": "fake"},
    )

    with TestClient(speech_app.app) as client:
        response = client.post("/admin/warmup")

    assert response.status_code == 200
    assert response.json()["vad"]["provider"] == "fake"
    assert response.json()["stt"]["engine"] == "fake"
