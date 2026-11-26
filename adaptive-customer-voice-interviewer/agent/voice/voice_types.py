from typing import Literal, Optional, TypedDict

from .voice_profile import DeliveryKind


VoiceInteractionMode = Literal[
    "research_answer",
    "audibility_confirmation",
    "connection_confirmation",
]


VoiceOutputEventType = Literal[
    "immediate_acknowledgement",
    "contextual_engagement",
]


class VoiceRuntimeResult(TypedDict):
    transcript: Optional[str]
    speech_detected: bool
    speech_duration_ms: int
    audio_issue: bool


class VoiceResponseEvent(TypedDict):
    session_id: str
    interview_id: str

    question: str
    interaction_mode: VoiceInteractionMode

    transcript: Optional[str]

    speech_detected: bool
    speech_duration_ms: int

    audio_issue: bool

    timestamp: float
    

class VoiceDeliveryMetadata(TypedDict):
    """Provider-neutral playback instructions attached to an output event."""

    kind: DeliveryKind
    language: str
    provider: str
    voice: Optional[str]
    rate: float
    pitch: float
    volume: float


class VoiceOutputEvent(TypedDict):
    """A message emitted by the research plane for the voice plane."""

    session_id: str
    interview_id: str
    event_type: VoiceOutputEventType
    text: str
    timestamp: float
    delivery: VoiceDeliveryMetadata
