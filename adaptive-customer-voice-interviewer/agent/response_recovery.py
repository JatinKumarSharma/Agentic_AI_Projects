from typing import Any, Dict

from agent.state import InterviewState
from agent.observability import get_logger

logger = get_logger(__name__)



MAX_RESPONSE_RETRIES = 2


def recover_from_response(
    state: InterviewState,
) -> Dict[str, Any]:

    status = state.get("response_status")

    retry_count = state.get(
        "response_retry_count",
        0,
    )

    # --------------------------------------------------------
    # Valid response
    # --------------------------------------------------------

    if status == "valid":
        return {
            "response_recovery_action": "continue_research",
        }

    # --------------------------------------------------------
    # Maximum retries
    # --------------------------------------------------------

    if retry_count >= MAX_RESPONSE_RETRIES:
        return {
            "response_recovery_action": "stop",
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": (
                "maximum_response_retries_exceeded"
            ),
        }

    logger.info("debug_payload", extra={"parts": ["DEBUG recover_from_response:", {             "status": status,             "retry_count": retry_count,             "max_retries": MAX_RESPONSE_RETRIES,         }]})
    # --------------------------------------------------------
    # Determine recovery action
    # --------------------------------------------------------

    if status == "no_response":
        action = (
            "repeat_question"
            if retry_count == 0
            else "check_audibility"
        )

    elif status == "audio_issue":

        action = "check_connection"

    elif status == "unrelated":

        action = "redirect_to_question"

    elif status == "non_responsive":

        action = "redirect_to_question"

    elif status == "unclear":

        action = "ask_repeat"

    else:

        action = "ask_repeat"

    return {
        "response_recovery_action": action,
        "response_retry_count": retry_count + 1,
    }
