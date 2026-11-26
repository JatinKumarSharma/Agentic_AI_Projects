
import base64
import os

from sarvamai import AsyncSarvamAI
from sarvamai.types import RealtimeAudioInput, RealtimeEnd


STT_LANGUAGE_CODE = "hi-IN"
STT_MODEL = "saaras:v3-realtime"
STT_STREAM_TYPE = "fast"
STT_MODE = "codemix"
STT_ENDPOINTING = "vad"
STT_ENCODING = "linear16"
STT_SAMPLE_RATE = "16000"


def create_sarvam_client() -> AsyncSarvamAI:
    """Create an authenticated Sarvam client."""

    api_key = os.getenv("SARVAM_API_KEY")

    if not api_key:
        raise RuntimeError("SARVAM_API_KEY is not configured.")

    return AsyncSarvamAI(
        api_subscription_key=api_key
    )


def create_realtime_audio_input(
    pcm16_audio: bytes,
) -> RealtimeAudioInput:
    """Convert raw PCM16 audio bytes into Sarvam's expected input."""

    if not isinstance(pcm16_audio, bytes):
        raise TypeError("Audio input must be bytes.")

    if not pcm16_audio:
        raise ValueError("Audio input cannot be empty.")

    encoded_audio = base64.b64encode(
        pcm16_audio
    ).decode("utf-8")

    return RealtimeAudioInput(
        audio=encoded_audio
    )


def create_realtime_end() -> RealtimeEnd:
    """Create the end-of-stream message."""

    return RealtimeEnd()
