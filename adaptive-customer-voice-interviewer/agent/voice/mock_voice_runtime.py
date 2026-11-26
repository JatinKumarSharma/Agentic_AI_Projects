from typing import Callable, Optional

from .voice_runtime import VoiceRuntime
from .voice_types import VoiceRuntimeResult
from agent.observability import get_logger

logger = get_logger(__name__)



class MockVoiceRuntime(VoiceRuntime):

    def __init__(
        self,
        responses: list[VoiceRuntimeResult],
    ):
        self.responses = responses
        self.response_index = 0
        self.spoken_messages: list[str] = []

    def start_listening(self) -> None:
        pass

    def stop_listening(self) -> None:
        pass

    def speak(self, text: str) -> None:
        self.spoken_messages.append(text)
        logger.info(f"[TTS] {text}")

    def interrupt_speech(self) -> None:
        logger.info("[TTS] interrupted")

    def listen_for_response(
        self,
        *,
        on_partial_transcript: Optional[
            Callable[[str], None]
        ] = None,
    ) -> VoiceRuntimeResult:

        if self.response_index >= len(self.responses):
            return {
                "transcript": None,
                "speech_detected": False,
                "speech_duration_ms": 0,
                "audio_issue": False,
            }

        response = self.responses[self.response_index]
        self.response_index += 1

        transcript = response["transcript"]

        if (
            transcript
            and on_partial_transcript is not None
        ):
            on_partial_transcript(transcript)

        return response
    