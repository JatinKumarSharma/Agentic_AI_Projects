from typing import Any

from .orchestrator import VoiceOrchestrator


class GraphVoiceBridge:

    def __init__(
        self,
        *,
        graph,
        orchestrator: VoiceOrchestrator,
    ):
        self.graph = graph
        self.orchestrator = orchestrator

    def handle_interrupt(
        self,
        interrupt_value: dict[str, Any],
        *,
        session_id: str,
        interview_id: str,
    ) -> Any:

        event_type = interrupt_value.get("type")

        if event_type == "deliver_question":

            question = interrupt_value["question"]

            return self.orchestrator.deliver_question(
                session_id=session_id,
                interview_id=interview_id,
                question=question,
            )

        if event_type == "await_consumer_response":

            question = interrupt_value["question"]

            interaction_mode = interrupt_value.get(
                "interaction_mode",
                "research_answer",
            )

            return self.orchestrator.collect_response(
                session_id=session_id,
                interview_id=interview_id,
                question=question,
                interaction_mode=interaction_mode,
            )

        if event_type == "response_recovery":

            action = interrupt_value["action"]
            question = interrupt_value.get("question")

            self.orchestrator.speak(
                self._recovery_prompt(
                    action=action,
                    question=question,
                )
            )

            return True

        raise ValueError(
            f"Unsupported voice interrupt type: {event_type}"
        )

    @staticmethod
    def _recovery_prompt(
        *,
        action: str,
        question: str | None,
    ) -> str:

        if action == "repeat_question":
            return f"Sure. {question}"

        if action == "redirect_to_question":
            return (
                f"Thanks. Let's come back to the question. "
                f"{question}"
            )

        if action == "ask_repeat":
            return (
                "Sorry, I didn't quite catch that. "
                "Could you say that again?"
            )

        if action == "check_audibility":
            return "Can you hear me clearly?"

        if action == "check_connection":
            return (
                "It sounds like there may be a connection issue. "
                "Can you hear me?"
            )

        return "Let's continue."
    