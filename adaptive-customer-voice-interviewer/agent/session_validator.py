from datetime import date
from typing import Dict, Any

from agent.session import normalize_date
from agent.state import InterviewState


def validate_research_session(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Determine whether the research session is currently
    eligible to run.

    This node is deterministic.

    It does not:
        - generate questions
        - modify the research goal
        - create a meta plan
        - evaluate proposals
        - make research decisions

    It only validates session lifecycle eligibility.
    """

    session = state["research_session"]

    today = date.today()

    status = session["status"]
    start_date = normalize_date(session["start_date"])
    end_date = normalize_date(session["end_date"])

    # --------------------------------------------------------
    # Explicit terminal states
    # --------------------------------------------------------

    if status == "completed":
        return {
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": (
                "research_session_completed"
            ),
        }

    if status == "cancelled":
        return {
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": (
                "research_session_cancelled"
            ),
        }

    if status == "expired":
        return {
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": (
                "research_session_expired"
            ),
        }

    # --------------------------------------------------------
    # Scheduled session
    # --------------------------------------------------------

    if status == "scheduled" and today < start_date:
        return {
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": (
                "research_session_not_started"
            ),
        }

    # --------------------------------------------------------
    # Date boundary
    # --------------------------------------------------------

    if today < start_date:
        return {
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": (
                "research_session_not_started"
            ),
        }

    if end_date is not None and today > end_date:
        return {
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": (
                "research_session_expired"
            ),
        }

    # --------------------------------------------------------
    # Valid session
    # --------------------------------------------------------

    return {
        "interview_complete": False,
        "next_action": None,
    }
