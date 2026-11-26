import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from agent.voice.event_adapter import create_voice_output_event


def test_create_immediate_acknowledgement_event():
    event = create_voice_output_event(
        session_id="session-1",
        interview_id="interview-1",
        event_type="immediate_acknowledgement",
        text="Got it.",
    )

    assert event["session_id"] == "session-1"
    assert event["interview_id"] == "interview-1"
    assert event["event_type"] == "immediate_acknowledgement"
    assert event["text"] == "Got it."
    assert isinstance(event["timestamp"], float)
    assert event["delivery"]["kind"] == "acknowledgement"
    assert event["delivery"]["language"] == "en-IN"
    assert 0 < event["delivery"]["volume"] <= 1


def test_create_contextual_engagement_event(monkeypatch):
    monkeypatch.setenv("CVI_VOICE_TTS_PROVIDER", "browser")

    event = create_voice_output_event(
        session_id="session-1",
        interview_id="interview-1",
        event_type="contextual_engagement",
        text="Ease of use seems particularly important to you.",
    )

    assert event["event_type"] == "contextual_engagement"
    assert event["text"] == (
        "Ease of use seems particularly important to you."
    )
    assert event["delivery"]["kind"] == "reflection"
    assert event["delivery"]["provider"] == "browser"


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", " "),
        ("interview_id", ""),
        ("text", "  "),
    ],
)
def test_reject_empty_required_fields(field, value):
    kwargs = {
        "session_id": "session-1",
        "interview_id": "interview-1",
        "event_type": "immediate_acknowledgement",
        "text": "Got it.",
    }
    kwargs[field] = value

    with pytest.raises(ValueError):
        create_voice_output_event(**kwargs)


def test_reject_unsupported_event_type():
    with pytest.raises(ValueError, match="Unsupported voice output event type"):
        create_voice_output_event(
            session_id="session-1",
            interview_id="interview-1",
            event_type="generic_filler",
            text="Thanks.",
        )
