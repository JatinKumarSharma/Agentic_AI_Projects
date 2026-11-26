
import base64

import pytest

from agent.voice.sarvam_stt import (
    create_realtime_audio_input,
    create_realtime_end,
)


def test_audio_input_is_base64_encoded():
    audio = b"\x01\x02\x03\x04"

    result = create_realtime_audio_input(audio)

    assert result.event == "audio_input"
    assert base64.b64decode(result.audio) == audio


def test_empty_audio_is_rejected():
    with pytest.raises(ValueError):
        create_realtime_audio_input(b"")


def test_non_bytes_audio_is_rejected():
    with pytest.raises(TypeError):
        create_realtime_audio_input("not bytes")


def test_realtime_end_event():
    result = create_realtime_end()

    assert result.event == "end"
