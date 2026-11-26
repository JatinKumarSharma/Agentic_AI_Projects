from typing import Dict, Any

from agent.state import InterviewState


# ============================================================
# EXECUTIVE CONTROLLER
# ============================================================

def execute_decision(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Executes the action selected by the Decision Policy.

    The Executive Controller does not make research decisions.
    It only translates an already-made decision into the
    next executable interview state.

    Supported actions:

        ask_question
        stop

    Future actions such as specialist invocation or tool
    execution can be added here without changing the
    Decision Policy's responsibilities.
    """

    next_action = state.get(
        "next_action"
    )

    # ========================================================
    # STOP
    # ========================================================

    if next_action == "stop":
        return {
            "interview_complete": True,
            "next_action": "stop",
        }

    # ========================================================
    # ASK QUESTION
    # ========================================================

    if next_action == "ask_question":

        selected_proposal = state.get(
            "selected_proposal"
        )

        # ----------------------------------------------------
        # Defensive validation
        # ----------------------------------------------------

        if not selected_proposal:

            return {
                "interview_complete": True,
                "next_action": "stop",
                "termination_reason": (
                    "missing_selected_proposal"
                ),
            }

        question = selected_proposal.get(
            "question"
        )

        if not question:

            return {
                "interview_complete": True,
                "next_action": "stop",
                "termination_reason": (
                    "selected_proposal_missing_question"
                ),
            }

        # ----------------------------------------------------
        # Prepare question for the voice interface
        # ----------------------------------------------------

        return {
            "current_question": question,
            "next_action": "ask_question",
        }

    # ========================================================
    # UNKNOWN ACTION
    # ========================================================

    return {
        "interview_complete": True,
        "next_action": "stop",
        "termination_reason": (
            "unknown_next_action"
        ),
    }
