from typing import Dict, Any

from agent.state import InterviewState, Proposal


# ============================================================
# QUALITATIVE LEVEL WEIGHTS
# ============================================================

LEVEL_WEIGHT = {
    "low": 1,
    "medium": 2,
    "high": 3,
}

CRITICALITY_WEIGHT = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


# ============================================================
# EXPECTED PROGRESS
# ============================================================

def calculate_expected_progress(
    proposal: Proposal,
) -> int:
    """
    Estimate the expected research progress from a proposal.

    Expected progress consists of:
        - expected information gain
        - expected state improvement
        - goal alignment

    Importance and criticality are intentionally excluded.
    They represent research priority, not progress itself.

    Used only as an internal operational value for ranking.
    """

    information_gain = LEVEL_WEIGHT.get(
        str(proposal.get("expected_information_gain", "low")).lower(),
        1,
    )

    state_improvement = LEVEL_WEIGHT.get(
        str(proposal.get("expected_state_improvement", "low")).lower(),
        1,
    )

    goal_alignment = LEVEL_WEIGHT.get(
        str(proposal.get("goal_alignment", "medium")).lower(),
        2,
    )

    return (
        information_gain
        + state_improvement
        + goal_alignment
    )


# ============================================================
# EXECUTION BURDEN
# ============================================================

def calculate_execution_burden(
    proposal: Proposal,
) -> int:
    """
    Estimate the operational burden of executing a proposal.

    Burden consists of:
        - cost
        - risk
        - consumer burden
    """

    cost = LEVEL_WEIGHT.get(
        str(proposal.get("cost", "low")).lower(),
        1,
    )

    risk = LEVEL_WEIGHT.get(
        str(proposal.get("risk", "low")).lower(),
        1,
    )

    consumer_burden = LEVEL_WEIGHT.get(
        str(proposal.get("consumer_burden", "low")).lower(),
        1,
    )

    return (
        cost
        + risk
        + consumer_burden
    )


# ============================================================
# PROGRESS EFFICIENCY
# ============================================================

def calculate_progress_efficiency(
    proposal: Proposal,
) -> float:
    """
    Estimate expected research progress per unit
    of execution burden.
    """

    progress = calculate_expected_progress(
        proposal
    )

    burden = calculate_execution_burden(
        proposal
    )

    return progress / max(burden, 1)


# ============================================================
# RANKING KEY
# ============================================================

def proposal_ranking_key(
    proposal: Proposal,
):
    """
    Create the deterministic ordering key.

    Priority:
        1. Expected progress per unit burden
        2. Total expected progress
        3. Criticality
        4. Importance

    Criticality and importance are used only as
    tie-breaking priority signals.

    The ranker does not select a proposal.
    Final selection remains the responsibility
    of the Decision Policy.
    """

    efficiency = calculate_progress_efficiency(
        proposal
    )

    progress = calculate_expected_progress(
        proposal
    )

    criticality = CRITICALITY_WEIGHT.get(
        str(proposal.get("criticality", "medium")).lower(),
        2,
    )

    importance = LEVEL_WEIGHT.get(
        str(proposal.get("importance", "medium")).lower(),
        2,
    )

    return (
        efficiency,
        progress,
        criticality,
        importance,
    )


# ============================================================
# MAIN RANKER
# ============================================================

def rank_proposals(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Rank evaluated proposals without selecting one.

    The ranker establishes deterministic proposal priority.

    It does NOT:
        - generate proposals
        - evaluate proposals
        - select a proposal
        - decide whether to continue
        - execute a proposal
    """

    proposals = state.get(
        "evaluated_proposals",
        [],
    )

    if not proposals:
        return {
            "evaluated_proposals": []
        }

    ranked_proposals = sorted(
        proposals,
        key=proposal_ranking_key,
        reverse=True,
    )

    return {
        "evaluated_proposals": ranked_proposals
    }