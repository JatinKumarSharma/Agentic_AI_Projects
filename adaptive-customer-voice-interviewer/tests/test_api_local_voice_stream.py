import json
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

import api


class FakeUpstreamSocket:
    def __init__(self, events):
        self.events = iter(events)
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        return json.dumps(next(self.events))


@asynccontextmanager
async def fake_connect(url):
    yield API_TEST_SOCKET


API_TEST_SOCKET = None


def test_local_stream_url_derives_websocket_url(monkeypatch):
    monkeypatch.delenv("CVI_SPEECH_STREAM_URL", raising=False)
    monkeypatch.setenv("CVI_SPEECH_URL", "http://speech:9000")
    assert api.local_stream_url() == "ws://speech:9000/stream"

    monkeypatch.setenv("CVI_SPEECH_URL", "https://speech.example")
    assert api.local_stream_url() == "wss://speech.example/stream"

    monkeypatch.setenv(
        "CVI_SPEECH_STREAM_URL",
        "ws://speech:9000/custom",
    )
    assert api.local_stream_url() == "ws://speech:9000/custom"


def test_local_voice_bridge_forwards_turns_and_submits_once(
    monkeypatch,
):
    global API_TEST_SOCKET

    session_id = "test-local-stream-session"
    monkeypatch.setitem(api.sessions, session_id, {})
    monkeypatch.setitem(
        api.pending_interrupts,
        session_id,
        {
            "type": "await_consumer_response",
            "question": "What would you improve?",
            "interaction_mode": "research_answer",
            "turn": 2,
        },
    )

    API_TEST_SOCKET = FakeUpstreamSocket([
        {
            "type": "stream_ready",
            "vad": "silero",
        },
        {
            "type": "speech_start",
            "utterance_idx": 1,
        },
        {
            "type": "speech_end",
            "utterance_idx": 1,
            "speech_ms": 4000,
        },
        {
            "type": "audio_segment",
            "utterance_idx": 1,
            "audio_base64": "AAAA",
        },
        {
            "type": "transcript_final",
            "utterance_idx": 1,
            "text": "  setup was smooth  ",
            "speech_ms": 4000,
        },
    ])

    monkeypatch.setattr(
        api,
        "connect_local_speech_stream",
        fake_connect,
    )

    submissions = []

    def fake_schedule(session_id, request):
        submissions.append((session_id, request))

        return {
            "status": "processing",
            "session_id": session_id,
        }

    monkeypatch.setattr(
        api,
        "schedule_response_submission",
        fake_schedule,
    )

    with TestClient(api.app) as client:
        with client.websocket_connect(
            f"/sessions/{session_id}/voice/local"
        ) as websocket:
            ready = websocket.receive_json()
            started = websocket.receive_json()
            ended = websocket.receive_json()
            final = websocket.receive_json()
            submitted = websocket.receive_json()

    assert ready == {
        "type": "stt_ready",
        "provider": "local",
        "vad": "silero",
    }
    assert started == {
        "type": "speech_start",
        "utterance_idx": 1,
    }
    assert ended == {
        "type": "vad_speech_end",
        "utterance_idx": 1,
    }
    assert final["type"] == "transcript_final"
    assert final["text"] == "setup was smooth"
    assert submitted == {
        "type": "response_submitted",
        "status": "processing",
    }

    assert len(submissions) == 1
    _, request = submissions[0]
    assert request.transcript == "setup was smooth"
    assert request.speech_duration_ms == 4000
