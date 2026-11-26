"""Deterministic response-outcome ledger and report labels.

The analysis report layer must not infer interview quality from prose. This
module keeps a model-free record of how the engine classified every submitted
answer and derives the report labels from that record.
"""

from typing import Any, Optional

from agent.provenance import (
    participant_id_for_session,
    stable_id,
    utc_now,
)


VALID_RESPONSE_STATUS = "valid"

INVALID_RESPONSE_STATUSES = frozenset({
    "unrelated",
    "non_responsive",
    "unclear",
    "no_response",
    "audio_issue",
})

STATUS_UNFINISHED = "unfinished"
STATUS_COMPLETED = "completed"

PATTERN_RECOVERED = "recovered_after_invalid"
PATTERN_CONTINUOUS = "continuous_valid"
PATTERN_UNRESOLVED = "unresolved_invalid"
PATTERN_UNCLASSIFIED = "unclassified"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def is_valid(status: Any) -> bool:
    return _text(status) == VALID_RESPONSE_STATUS


def is_invalid(status: Any) -> bool:
    return _text(status) in INVALID_RESPONSE_STATUSES


def is_completed(state: dict) -> bool:
    """Treat decision-policy stops as completed interview outcomes.

    The executive controller does not always set ``interview_complete``.
    A stop action or a termination reason is an equally authoritative
    end-of-interview signal and must label the report as completed.
    """

    return bool(
        state.get("interview_complete")
        or _text(state.get("next_action")) == "stop"
        or _text(state.get("termination_reason"))
    )


def normalized_outcomes(state: dict) -> list[dict]:
    """Return well-formed outcome records already present in state."""

    outcomes = state.get("response_outcomes")

    if not isinstance(outcomes, list):
        return []

    return [
        dict(outcome)
        for outcome in outcomes
        if isinstance(outcome, dict)
    ]


def _outcome_id(
    session_id: str,
    sequence: int,
    response_id: str,
    status: str,
) -> str:
    return stable_id(
        "outcome",
        session_id,
        sequence,
        response_id,
        status,
    )


def build_outcome(
    state: dict,
    status: str,
    sequence: int,
    classification_source: str,
) -> dict:
    """Build one outcome record from state that already exists."""

    research_session = state.get("research_session") or {}
    session_id = _text(research_session.get("session_id")) or "unknown"
    response_id = (
        _text(state.get("latest_response_id"))
        or stable_id(
            "resp",
            session_id,
            sequence,
            state.get("latest_response"),
        )
    )
    transcript = state.get("latest_response")
    participant_id = (
        _text(state.get("latest_participant_id"))
        or _text(research_session.get("participant_id"))
        or participant_id_for_session(research_session)
    )
    captured_at = (
        _text(state.get("latest_response_captured_at"))
        or utc_now()
    )
    question = (
        state.get("latest_response_question")
        or state.get("current_question")
    )

    return {
        "outcome_id": _outcome_id(
            session_id,
            sequence,
            response_id,
            status,
        ),
        "sequence": sequence,
        "session_id": session_id,
        "response_id": response_id,
        "status": status,
        "question": question,
        "transcript": transcript,
        "captured_at": captured_at,
        "participant_id": participant_id,
        "classification_source": classification_source,
    }


def append_outcome(
    state: dict,
    status: str,
    classification_source: str,
) -> dict:
    """Append a classified outcome without duplicating a retried write."""

    outcomes = normalized_outcomes(state)
    sequence = len(outcomes) + 1
    outcome = build_outcome(
        state,
        status,
        sequence,
        classification_source,
    )

    if any(
        existing.get("response_id") == outcome["response_id"]
        and existing.get("status") == outcome["status"]
        and existing.get("classification_source") == (
            outcome["classification_source"]
        )
        for existing in outcomes
    ):
        return {"response_outcomes": outcomes}

    return {"response_outcomes": outcomes + [outcome]}


def classify_response_pattern(outcomes: list[dict]) -> str:
    """Derive the answer-sequence label from recorded classifications."""

    statuses = [
        _text(outcome.get("status"))
        for outcome in outcomes
    ]
    statuses = [status for status in statuses if status]

    if not statuses:
        return PATTERN_UNCLASSIFIED

    invalid_indexes = [
        index
        for index, status in enumerate(statuses)
        if is_invalid(status)
    ]

    if not invalid_indexes:
        return PATTERN_CONTINUOUS if all(
            is_valid(status) for status in statuses
        ) else PATTERN_UNCLASSIFIED

    first_invalid = invalid_indexes[0]
    valid_after_invalid = any(
        is_valid(status) for status in statuses[first_invalid + 1:]
    )

    if valid_after_invalid:
        return PATTERN_RECOVERED

    return PATTERN_UNRESOLVED


def derive_report_labels(state: dict) -> dict:
    """Return labels and counts that are safe to aggregate."""

    outcomes = normalized_outcomes(state)
    pattern = classify_response_pattern(outcomes)
    status_label = (
        STATUS_COMPLETED if is_completed(state) else STATUS_UNFINISHED
    )
    tags = list(dict.fromkeys([status_label, pattern]))
    statuses = [
        _text(outcome.get("status"))
        for outcome in outcomes
    ]
    invalid_indexes = [
        index
        for index, status in enumerate(statuses)
        if is_invalid(status)
    ]
    first_invalid = invalid_indexes[0] if invalid_indexes else None

    return {
        "status_label": status_label,
        "response_pattern_label": pattern,
        "tags": tags,
        "valid_response_count": sum(
            is_valid(status) for status in statuses
        ),
        "invalid_response_count": len(invalid_indexes),
        "valid_before_invalid": (
            first_invalid is not None
            and any(is_valid(status) for status in statuses[:first_invalid])
        ),
        "valid_after_invalid": (
            first_invalid is not None
            and any(
                is_valid(status)
                for status in statuses[first_invalid + 1:]
            )
        ),
        "response_outcome_count": len(outcomes),
    }
