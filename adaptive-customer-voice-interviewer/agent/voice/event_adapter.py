import time

from .voice_profile import (
    DeliveryKind,
    VoiceProfile,
    get_voice_profile,
)
from .voice_types import (
    VoiceInteractionMode,
    VoiceResponseEvent,
    VoiceRuntimeResult,
    VoiceOutputEvent,
    VoiceOutputEventType,
)


DELIVERY_KIND_BY_EVENT_TYPE = {
    "immediate_acknowledgement": "acknowledgement",
    "contextual_engagement": "reflection",
}


def create_voice_response_event(
    *,
    session_id: str,
    interview_id: str,
    question: str,
    interaction_mode: VoiceInteractionMode,
    voice_result: VoiceRuntimeResult,
) -> VoiceResponseEvent:

    return {
        "session_id": session_id,
        "interview_id": interview_id,
        "question": question,
        "interaction_mode": interaction_mode,
        "transcript": voice_result["transcript"],
        "speech_detected": voice_result["speech_detected"],
        "speech_duration_ms": voice_result["speech_duration_ms"],
        "audio_issue": voice_result["audio_issue"],
        "timestamp": time.time(),
    }



def create_voice_output_event(
    *,
    session_id: str,
    interview_id: str,
    event_type: VoiceOutputEventType,
    text: str,
    delivery_kind: DeliveryKind | None = None,
    profile: VoiceProfile | None = None,
) -> VoiceOutputEvent:
    """Create a typed outbound message for the active voice plane.

    This adapter only defines the event contract. Event delivery and
    playback are handled by the voice transport/runtime layer.
    """

    if not session_id.strip():
        raise ValueError("session_id must not be empty")

    if not interview_id.strip():
        raise ValueError("interview_id must not be empty")

    if not text.strip():
        raise ValueError("text must not be empty")

    if event_type not in DELIVERY_KIND_BY_EVENT_TYPE:
        raise ValueError(f"Unsupported voice output event type: {event_type}")

    active_profile = profile or get_voice_profile()
    kind = delivery_kind or DELIVERY_KIND_BY_EVENT_TYPE[event_type]
    prosody = active_profile.prosody_for(kind).to_dict()

    return {
        "session_id": session_id,
        "interview_id": interview_id,
        "event_type": event_type,
        "text": text.strip(),
        "timestamp": time.time(),
        "delivery": {
            "kind": kind,
            "language": active_profile.language,
            "provider": active_profile.tts_provider,
            "voice": active_profile.voice,
            **prosody,
        },
    }
