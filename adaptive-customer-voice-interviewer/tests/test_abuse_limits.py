import asyncio
import base64
import json
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api
from agent import limits as abuse_limits


SESSION_ID = "abuse-limits-session"


def setup_function():
    api.sessions[SESSION_ID] = {"session_id": SESSION_ID, "status": "active"}


def teardown_function():
    api.sessions.pop(SESSION_ID, None)


def test_response_transcript_is_bounded():
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/responses",
        json={"transcript": "x" * 20_000},
    )

    assert response.status_code == 422


def test_timing_metadata_is_bounded():
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/timings",
        json={
            "turn": 1,
            "span_name": "probe",
            "started_ms": 1.0,
            "ended_ms": 2.0,
            "metadata": {
                f"key_{index}": "value" for index in range(60)
            },
        },
    )

    assert response.status_code == 422


def test_transcription_inputs_are_bounded():
    client = TestClient(api.app)
    audio = base64.b64encode(b"fake-audio").decode("ascii")

    unsafe_filename = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json={
            "audio_base64": audio,
            "filename": "../escape.wav",
        },
    )

    assert unsafe_filename.status_code == 422

    long_prompt = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json={
            "audio_base64": audio,
            "prompt": "x" * (abuse_limits.MAX_STT_PROMPT_CHARS + 1),
        },
    )

    assert long_prompt.status_code == 422


def test_request_body_length_is_rejected_before_parsing(monkeypatch):
    client = TestClient(api.app)

    monkeypatch.setattr(abuse_limits, "MAX_HTTP_BODY_BYTES", 64)

    response = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json={"audio_base64": "x" * 1_000},
    )

    assert response.status_code == 413


def test_oversized_audio_upload_is_rejected():
    client = TestClient(api.app)
    oversized = base64.b64encode(
        b"x" * (abuse_limits.MAX_AUDIO_BYTES + 1)
    ).decode("ascii")

    response = client.post(
        f"/sessions/{SESSION_ID}/voice/transcribe",
        json={"audio_base64": oversized},
    )

    assert response.status_code == 422


def test_synthesis_inputs_are_bounded():
    client = TestClient(api.app)

    long_text = client.post(
        f"/sessions/{SESSION_ID}/voice/synthesize",
        json={"text": "x" * (abuse_limits.MAX_TTS_TEXT_CHARS + 1)},
    )

    assert long_text.status_code == 422

    large_delivery = client.post(
        f"/sessions/{SESSION_ID}/voice/synthesize",
        json={
            "text": "Hello?",
            "delivery": {
                f"key_{index}": "value" for index in range(40)
            },
        },
    )

    assert large_delivery.status_code == 422


def test_voice_event_ack_ids_are_bounded():
    client = TestClient(api.app)

    response = client.post(
        f"/sessions/{SESSION_ID}/voice-events/ack",
        json={
            "event_ids": [
                "x" * (abuse_limits.MAX_EVENT_ID_CHARS + 1)
            ]
        },
    )

    assert response.status_code == 422


def test_request_rate_limit_blocks_excess_traffic(monkeypatch):
    client = TestClient(api.app)

    api.rate_limiter.reset()
    monkeypatch.setattr(api.rate_limiter, "enabled", True)
    monkeypatch.setattr(api.rate_limiter, "session_limit", 2)
    monkeypatch.setattr(api.rate_limiter, "ip_limit", 1000)

    statuses = [
        client.get(f"/sessions/{SESSION_ID}/status").status_code
        for _ in range(3)
    ]

    assert statuses == [200, 200, 429]


def test_voice_stream_budget_tracks_frame_and_duration(monkeypatch):
    monkeypatch.setattr(abuse_limits, "WS_MAX_FRAME_BYTES", 16)
    monkeypatch.setattr(abuse_limits, "WS_MAX_TOTAL_BYTES", 40)

    budget = abuse_limits.VoiceStreamBudget()

    assert budget.check_frame(17) == "frame"
    assert budget.check_frame(16) is None
    assert budget.check_frame(16) is None
    assert budget.check_frame(16) == "total"

    budget.started_at -= abuse_limits.WS_MAX_DURATION_SECONDS + 1
    assert budget.check_duration() is True


def test_voice_websocket_rejects_oversized_frames(monkeypatch):
    api.pending_interrupts[SESSION_ID] = {
        "type": "await_consumer_response",
        "question": "What would you improve?",
        "turn": 1,
    }

    monkeypatch.setattr(abuse_limits, "WS_MAX_FRAME_BYTES", 8)

    class FakeUpstream:
        def __init__(self):
            self.ready = False

        async def send(self, payload):
            return None

        async def recv(self):
            if not self.ready:
                self.ready = True
                return json.dumps(
                    {"type": "stream_ready", "vad": "silero"}
                )
            await asyncio.Event().wait()

    @asynccontextmanager
    async def fake_connect(url):
        yield FakeUpstream()

    monkeypatch.setattr(
        api,
        "connect_local_speech_stream",
        fake_connect,
    )

    errors = []

    with TestClient(api.app) as client:
        with client.websocket_connect(
            f"/sessions/{SESSION_ID}/voice/local"
        ) as websocket:
            websocket.send_bytes(b"x" * 32)

            for _ in range(4):
                try:
                    message = websocket.receive_json()
                except WebSocketDisconnect:
                    break

                if message.get("type") == "error":
                    errors.append(message)

    assert errors
    assert "limit" in errors[0]
    assert errors[0]["limit"] == "frame"

    api.pending_interrupts.pop(SESSION_ID, None)


def test_voice_websocket_rate_limit_closes_excess_connections(monkeypatch):
    api.pending_interrupts[SESSION_ID] = {
        "type": "await_consumer_response",
        "question": "What would you improve?",
        "turn": 1,
    }

    api.rate_limiter.reset()
    monkeypatch.setattr(api.rate_limiter, "enabled", True)
    monkeypatch.setattr(api.rate_limiter, "session_limit", 100)
    monkeypatch.setattr(api.rate_limiter, "ip_limit", 1000)
    monkeypatch.setattr(api.rate_limiter, "websocket_limit", 1)

    class FakeUpstream:
        async def send(self, payload):
            return None

        async def recv(self):
            await asyncio.Event().wait()

    @asynccontextmanager
    async def fake_connect(url):
        yield FakeUpstream()

    monkeypatch.setattr(
        api,
        "connect_local_speech_stream",
        fake_connect,
    )

    errors = []

    with TestClient(api.app) as client:
        with client.websocket_connect(
            f"/sessions/{SESSION_ID}/voice/local"
        ):
            with client.websocket_connect(
                f"/sessions/{SESSION_ID}/voice/local"
            ) as rejected:
                try:
                    message = rejected.receive_json()
                except WebSocketDisconnect:
                    message = {}

                if message.get("type") == "error":
                    errors.append(message)

    assert errors
    assert "slow down" in errors[0]["message"]

    api.pending_interrupts.pop(SESSION_ID, None)


def test_voice_websocket_limits_concurrent_streams(monkeypatch):
    api.pending_interrupts[SESSION_ID] = {
        "type": "await_consumer_response",
        "question": "What would you improve?",
        "turn": 1,
    }
    monkeypatch.setattr(abuse_limits, "WS_MAX_CONCURRENT_PER_SESSION", 1)

    class FakeUpstream:
        def __init__(self):
            self.ready = False

        async def send(self, payload):
            return None

        async def recv(self):
            if not self.ready:
                self.ready = True
                return json.dumps(
                    {"type": "stream_ready", "vad": "silero"}
                )
            await asyncio.Event().wait()

    @asynccontextmanager
    async def fake_connect(url):
        yield FakeUpstream()

    monkeypatch.setattr(
        api,
        "connect_local_speech_stream",
        fake_connect,
    )

    errors = []

    with TestClient(api.app) as client:
        with client.websocket_connect(
            f"/sessions/{SESSION_ID}/voice/local"
        ):
            with client.websocket_connect(
                f"/sessions/{SESSION_ID}/voice/local"
            ) as rejected:
                try:
                    message = rejected.receive_json()
                except WebSocketDisconnect:
                    message = {}

                if message.get("type") == "error":
                    errors.append(message)

        # Closing the first connection must release its slot.
        with client.websocket_connect(
            f"/sessions/{SESSION_ID}/voice/local"
        ):
            pass

    assert errors
    assert "active voice connection" in errors[0]["message"]
    assert api.active_voice_streams.get(SESSION_ID, 0) == 0

    api.pending_interrupts.pop(SESSION_ID, None)
