from .event_adapter import create_voice_response_event
from .voice_runtime import VoiceRuntime
from .voice_types import (
    VoiceInteractionMode,
    VoiceResponseEvent,
    VoiceRuntimeResult,
)


class VoiceOrchestrator:

    def __init__(self, runtime: VoiceRuntime):
        self.runtime = runtime

    def speak(self, text: str) -> None:
        self.runtime.speak(text)

    def deliver_question(
        self,
        *,
        session_id: str,
        interview_id: str,
        question: str,
    ) -> bool:

        self.speak(question)

        return True

    def collect_response(
        self,
        *,
        session_id: str,
        interview_id: str,
        question: str,
        interaction_mode: VoiceInteractionMode,
    ) -> VoiceResponseEvent:

        self.runtime.start_listening()

        try:
            voice_result: VoiceRuntimeResult = (
                self.runtime.listen_for_response()
            )
        finally:
            self.runtime.stop_listening()

        return create_voice_response_event(
            session_id=session_id,
            interview_id=interview_id,
            question=question,
            interaction_mode=interaction_mode,
            voice_result=voice_result,
        )
    