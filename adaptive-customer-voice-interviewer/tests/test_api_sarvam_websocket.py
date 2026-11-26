import asyncio
import base64
import pytest
from fastapi.testclient import TestClient

from contextlib import asynccontextmanager
from types import SimpleNamespace

import api


@pytest.fixture
def client():
    with TestClient(api.app) as test_client:
        yield test_client


def test_websocket_rejects_unknown_session(client):
    session_id = "test-unknown-voice-session"

    api.sessions.pop(session_id, None)
    api.pending_interrupts.pop(session_id, None)

    with client.websocket_connect(
        f"/sessions/{session_id}/voice"
    ) as websocket:
        message = websocket.receive_json()

    assert message["type"] == "error"
    assert message["message"] == "Session not found."


def test_websocket_rejects_session_not_waiting_for_response(
    client,
    monkeypatch,
):
    session_id = "test-not-waiting-voice-session"

    monkeypatch.setitem(api.sessions, session_id, {})
    monkeypatch.setitem(
        api.pending_interrupts,
        session_id,
        {
            "type": "deliver_question",
            "question": "Test question",
        },
    )

    with client.websocket_connect(
        f"/sessions/{session_id}/voice"
    ) as websocket:
        message = websocket.receive_json()

    assert message["type"] == "error"
    assert (
        message["message"]
        == "The interview is not currently waiting for a response."
    )


class FakeSarvamSocket:
    def __init__(self, events):
        self.events = iter(events)
        self.audio_inputs = []

    async def recv(self):
        try:
            return next(self.events)
        except StopIteration:
            # This should not happen in the final-transcript test.
            raise RuntimeError("No more mocked Sarvam events.")

    async def send_realtime_audio_input(self, audio_input):
        self.audio_inputs.append(audio_input)


class FakeSarvamClient:
    def __init__(self, socket):
        self.socket = socket
        self.speech_to_text_realtime_streaming = self

    @asynccontextmanager
    async def connect(self, **kwargs):
        self.connection_config = kwargs
        yield self.socket


def test_websocket_forwards_events_and_submits_final_once(
    client,
    monkeypatch,
):
    session_id = "test-successful-voice-session"

    monkeypatch.setitem(api.sessions, session_id, {})
    monkeypatch.setitem(
        api.pending_interrupts,
        session_id,
        {
            "type": "await_consumer_response",
            "question": "What would you improve?",
            "interaction_mode": "research_answer",
        },
    )

    mocked_events = [
        SimpleNamespace(
            event="transcript.partial",
            text="I would improve",
            utterance_idx=1,
        ),
        SimpleNamespace(
            event="vad.speech_end",
            utterance_idx=1,
            speech_duration_ms=8000,
        ),
        SimpleNamespace(
            event="transcript.final",
            text="I would improve the onboarding process.",
            utterance_idx=1,
        ),
    ]

    fake_socket = FakeSarvamSocket(mocked_events)
    fake_client = FakeSarvamClient(fake_socket)

    monkeypatch.setattr(
        api,
        "create_sarvam_client",
        lambda: fake_client,
    )

    submitted_responses = []

    def fake_schedule_response_submission(
        received_session_id,
        request,
    ):
        submitted_responses.append(
            (received_session_id, request)
        )
        return {
            "status": "processing",
            "session_id": received_session_id,
        }

    monkeypatch.setattr(
        api,
        "schedule_response_submission",
        fake_schedule_response_submission,
    )

    with client.websocket_connect(
        f"/sessions/{session_id}/voice"
    ) as websocket:

        ready = websocket.receive_json()
        partial = websocket.receive_json()
        speech_end = websocket.receive_json()
        acknowledgement = websocket.receive_json()
        final = websocket.receive_json()
        submitted = websocket.receive_json()

    assert ready["type"] == "stt_ready"

    assert partial == {
        "type": "transcript_partial",
        "text": "I would improve",
        "utterance_idx": 1,
    }

    assert speech_end == {
        "type": "vad_speech_end",
        "utterance_idx": 1,
    }

    assert acknowledgement["type"] == "voice_output"
    assert acknowledgement["event"]["event_type"] == (
        "immediate_acknowledgement"
    )
    assert acknowledgement["event"]["text"]

    assert final["type"] == "transcript_final"
    assert final["text"] == (
        "I would improve the onboarding process."
    )

    assert submitted["type"] == "response_submitted"
    assert submitted["status"] == "processing"

    assert len(submitted_responses) == 1

    received_session_id, request = submitted_responses[0]

    assert received_session_id == session_id
    assert request.transcript == (
        "I would improve the onboarding process."
    )
    assert request.audio_issue is False


class AudioAwareFakeSarvamSocket:
    def __init__(self):
        self.events = asyncio.Queue()
        self.audio_inputs = []

    async def send_realtime_audio_input(self, audio_input):
        self.audio_inputs.append(audio_input)

        # Simulate Sarvam returning events after receiving audio.
        await self.events.put(
            SimpleNamespace(
                event="transcript.partial",
                text="Testing audio",
                utterance_idx=1,
            )
        )

        await self.events.put(
            SimpleNamespace(
                event="vad.speech_end",
                utterance_idx=1,
                speech_duration_ms=8000,
            )
        )

        await self.events.put(
            SimpleNamespace(
                event="transcript.final",
                text="Testing audio forwarding.",
                utterance_idx=1,
            )
        )

    async def recv(self):
        return await self.events.get()


class StopAwareFakeSarvamSocket:
    def __init__(self):
        self.events = asyncio.Queue()
        self.audio_inputs = []
        self.realtime_end_messages = []

    async def send_realtime_audio_input(self, audio_input):
        self.audio_inputs.append(audio_input)

    async def send_realtime_end(self, end_message):
        self.realtime_end_messages.append(end_message)

        # Simulate Sarvam returning the final transcript
        # only after receiving the end-of-audio signal.
        await self.events.put(
            SimpleNamespace(
                event="transcript.final",
                text="I would improve customer onboarding.",
                utterance_idx=1,
            )
        )

    async def recv(self):
        return await self.events.get()


def test_websocket_stop_control_ends_audio_and_submits_final(
    client,
    monkeypatch,
):
    session_id = "test-stop-control-session"

    monkeypatch.setitem(api.sessions, session_id, {})
    monkeypatch.setitem(
        api.pending_interrupts,
        session_id,
        {
            "type": "await_consumer_response",
            "question": "What would you improve?",
            "interaction_mode": "research_answer",
        },
    )

    fake_socket = StopAwareFakeSarvamSocket()
    fake_client = FakeSarvamClient(fake_socket)

    monkeypatch.setattr(
        api,
        "create_sarvam_client",
        lambda: fake_client,
    )

    submitted_responses = []

    def fake_schedule_response_submission(
        received_session_id,
        request,
    ):
        submitted_responses.append(
            (received_session_id, request)
        )

        return {
            "status": "processing",
            "session_id": received_session_id,
        }

    monkeypatch.setattr(
        api,
        "schedule_response_submission",
        fake_schedule_response_submission,
    )

    browser_audio = b"\x01\x02\x03\x04"

    received_messages = []

    with client.websocket_connect(
        f"/sessions/{session_id}/voice"
    ) as websocket:

        ready = websocket.receive_json()
        assert ready["type"] == "stt_ready"

        websocket.send_bytes(browser_audio)

        websocket.send_json({
            "type": "stop",
        })

        # Collect messages until the final transcript
        # has been submitted.
        while True:
            message = websocket.receive_json()
            received_messages.append(message)

            if message["type"] == "response_submitted":
                break

    message_types = {
        message["type"]
        for message in received_messages
    }

    assert "transcript_final" in message_types
    assert "response_submitted" in message_types

    assert len(fake_socket.audio_inputs) == 1
    assert len(fake_socket.realtime_end_messages) == 1

    forwarded_audio = base64.b64decode(
        fake_socket.audio_inputs[0].audio
    )
    assert forwarded_audio == browser_audio

    assert len(submitted_responses) == 1

    received_session_id, request = submitted_responses[0]

    assert received_session_id == session_id
    assert request.transcript == (
        "I would improve customer onboarding."
    )


def test_websocket_forwards_binary_audio_to_sarvam(
    client,
    monkeypatch,
):
    session_id = "test-audio-forwarding-session"

    monkeypatch.setitem(api.sessions, session_id, {})
    monkeypatch.setitem(
        api.pending_interrupts,
        session_id,
        {
            "type": "await_consumer_response",
            "question": "Can you hear me?",
            "interaction_mode": "research_answer",
        },
    )

    fake_socket = AudioAwareFakeSarvamSocket()
    fake_client = FakeSarvamClient(fake_socket)

    monkeypatch.setattr(
        api,
        "create_sarvam_client",
        lambda: fake_client,
    )

    submitted_responses = []

    def fake_schedule_response_submission(
        received_session_id,
        request,
    ):
        submitted_responses.append(
            (received_session_id, request)
        )

        return {
            "status": "processing",
            "session_id": received_session_id,
        }

    monkeypatch.setattr(
        api,
        "schedule_response_submission",
        fake_schedule_response_submission,
    )

    browser_audio = b"\x01\x02\x03\x04\x05\x06"

    with client.websocket_connect(
        f"/sessions/{session_id}/voice"
    ) as websocket:

        ready = websocket.receive_json()
        assert ready["type"] == "stt_ready"

        websocket.send_bytes(browser_audio)

        partial = websocket.receive_json()
        speech_end = websocket.receive_json()
        acknowledgement = websocket.receive_json()
        final = websocket.receive_json()
        submitted = websocket.receive_json()

    assert partial["type"] == "transcript_partial"
    assert speech_end["type"] == "vad_speech_end"
    assert acknowledgement["type"] == "voice_output"
    assert acknowledgement["event"]["event_type"] == (
        "immediate_acknowledgement"
    )
    assert final["type"] == "transcript_final"
    assert submitted["type"] == "response_submitted"

    assert len(fake_socket.audio_inputs) == 1

    forwarded_audio = base64.b64decode(
        fake_socket.audio_inputs[0].audio
    )

    assert forwarded_audio == browser_audio

    assert len(submitted_responses) == 1
    assert (
        submitted_responses[0][1].transcript
        == "Testing audio forwarding."
    )
