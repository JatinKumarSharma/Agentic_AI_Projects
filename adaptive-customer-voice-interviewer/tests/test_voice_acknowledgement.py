import api
from agent.voice.acknowledgement_policy import ACKNOWLEDGEMENTS


SESSION_ID = "ack-session"


def test_speech_end_acknowledgement_uses_voice_output_contract():
    message = api.create_speech_end_acknowledgement_message(SESSION_ID)

    assert message["type"] == "voice_output"

    event = message["event"]
    assert event["session_id"] == SESSION_ID
    assert event["interview_id"] == SESSION_ID
    assert event["event_type"] == "immediate_acknowledgement"
    assert event["text"] in ACKNOWLEDGEMENTS
    assert isinstance(event["timestamp"], float)


def test_vad_speech_end_emits_status_then_immediate_acknowledgement():
    messages = api.create_vad_speech_end_messages(
        SESSION_ID, 3, speech_ms=7000, pause_ms=800
    )

    assert len(messages) == 2

    assert messages[0] == {
        "type": "vad_speech_end",
        "utterance_idx": 3,
    }

    assert messages[1]["type"] == "voice_output"
    assert messages[1]["event"]["event_type"] == (
        "immediate_acknowledgement"
    )
    assert messages[1]["event"]["session_id"] == SESSION_ID


def test_short_speech_does_not_emit_acknowledgement():
    messages = api.create_vad_speech_end_messages(
        SESSION_ID, 3, speech_ms=500, pause_ms=800
    )

    assert messages == [
        {"type": "vad_speech_end", "utterance_idx": 3}
    ]


def test_acknowledgements_are_one_to_three_words_and_not_robotic():
    assert ACKNOWLEDGEMENTS

    for text in ACKNOWLEDGEMENTS:
        assert 1 <= len(text.split()) <= 3
        assert "mm hmm" not in text.lower()
