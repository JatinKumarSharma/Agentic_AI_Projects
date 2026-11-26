from typing import Dict, Any

from agent.state import InterviewState
from agent.reflection import create_state_snapshot


# ============================================================
# STATE SNAPSHOT NODE
# ============================================================

def capture_pre_question_snapshot(
    state: InterviewState
) -> Dict[str, Any]:
    """
    Captures the interview state immediately before the
    selected proposal/question is executed.

    The snapshot is intentionally lightweight and contains
    only the state-quality information required by Reflection.

    This node does not:
    - call an LLM
    - modify consumer knowledge
    - analyze the response
    - generate a question
    - select a proposal
    """

    snapshot = create_state_snapshot(state)

    # Preserve historical snapshots while also storing the
    # latest snapshot as the immediate before-question state.
    history = list(
        state.get(
            "state_history",
            []
        )
    )

    history.append(snapshot)

    return {
        "previous_state_snapshot": snapshot,
        "state_history": history,
    }