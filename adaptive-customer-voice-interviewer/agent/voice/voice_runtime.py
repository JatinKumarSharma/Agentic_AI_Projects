from abc import ABC, abstractmethod
from typing import Callable, Optional

from .voice_types import VoiceRuntimeResult

class VoiceRuntime(ABC):

    @abstractmethod
    def start_listening(self) -> None:
        """Start capturing consumer audio."""
        raise NotImplementedError

    @abstractmethod
    def stop_listening(self) -> None:
        """Stop capturing consumer audio."""
        raise NotImplementedError

    @abstractmethod
    def speak(self, text: str) -> None:
        """Speak text to the consumer."""
        raise NotImplementedError

    @abstractmethod
    def interrupt_speech(self) -> None:
        """Immediately stop currently playing TTS."""
        raise NotImplementedError

    @abstractmethod
    def listen_for_response(
        self,
        *,
        on_partial_transcript: Optional[Callable[[str], None]] = None,
    ) -> VoiceRuntimeResult:
        """
        Capture one consumer response and return the final
        response event data.
        """
        raise NotImplementedError
    