from typing import Any, Dict

from agent.provenance import (
    new_response_id,
    participant_id_for_session,
    utc_now,
)
from agent.state import InterviewState
from agent.observability import get_logger

logger = get_logger(__name__)



def accept_valid_response(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Store a consumer response only after it has been
    classified as valid for the current interview question.
    """

    response = state.get("latest_response")

    if not response:
        raise ValueError(
            "Cannot accept an empty consumer response."
        )

    responses = list(
        state.get("responses", [])
    )
    response_records = list(
        state.get("response_records", [])
    )

    responses.append(response)

    turn = len(responses)
    response_id = (
        state.get("latest_response_id")
        or new_response_id()
    )
    captured_at = (
        state.get("latest_response_captured_at")
        or utc_now()
    )

    research_session = state.get("research_session") or {}
    participant_id = (
        state.get("latest_participant_id")
        or research_session.get("participant_id")
        or participant_id_for_session(research_session)
    )
    question = (
        state.get("latest_response_question")
        or state.get("current_question")
    )

    response_records.append({
        "response_id": response_id,
        "turn": turn,
        "question": question,
        "text": response,
        "captured_at": captured_at,
        "participant_id": participant_id,
    })

    logger.info("debug_payload", extra={"parts": ["DEBUG accept_valid_response:", {             "latest_response": response,             "response_id": response_id,             "responses_before": state.get("responses", []),             "responses_after": responses,         }]})
    return {
        "responses": responses,
        "response_records": response_records,
        "latest_response_id": response_id,
        "latest_response_captured_at": captured_at,
        "latest_response_turn": turn,
        "latest_response_question": question,
        "latest_participant_id": participant_id,
        "response_retry_count": 0,
        "response_recovery_action": None,
    }
