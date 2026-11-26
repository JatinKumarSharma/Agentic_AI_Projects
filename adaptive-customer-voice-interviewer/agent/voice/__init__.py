"""Provider-neutral voice contracts and adapters."""

from .event_adapter import create_voice_output_event, create_voice_response_event
from .stt_providers import (
    STTProvider,
    STTProviderError,
    STTResult,
    create_stt_provider,
    pcm16_chunks_to_wav,
    transcribe_with_fallback,
)
from .tts_providers import (
    TTSProvider,
    TTSProviderError,
    TTSResult,
    create_tts_provider,
    synthesize_with_fallback,
)
from .voice_profile import Prosody, VoiceProfile, get_voice_profile
from .voice_types import VoiceOutputEvent, VoiceRuntimeResult

__all__ = [
    "STTProvider",
    "STTProviderError",
    "STTResult",
    "TTSProvider",
    "TTSProviderError",
    "TTSResult",
    "Prosody",
    "VoiceOutputEvent",
    "VoiceProfile",
    "VoiceRuntimeResult",
    "create_stt_provider",
    "create_voice_output_event",
    "create_voice_response_event",
    "create_tts_provider",
    "get_voice_profile",
    "pcm16_chunks_to_wav",
    "synthesize_with_fallback",
    "transcribe_with_fallback",
]
