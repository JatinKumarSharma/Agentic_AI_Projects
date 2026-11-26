from typing import Dict, Any, Optional, List

from agent.state import (
    InterviewState,
    Proposal,
    InformationGap,
    InformationRequirement,
)
from agent.gap_analyzer import PARAMETER_MAPPING


# ============================================================
# QUALITATIVE ORDERING
# ============================================================

SUFFICIENCY_LEVELS = {
    "missing": 0,
    "basic": 1,
    "adequate": 2,
    "comprehensive": 3,
}

CONFIDENCE_LEVELS = {
    "low": 1,
    "medium": 2,
    "high": 3,
}


# ============================================================
# ITERATION CONTROL
# ============================================================

def maximum_iterations_reached(
    state: InterviewState,
) -> bool:
    """
    Determine whether the maximum allowed number of
    interview iterations has been reached.
    """

    meta_plan = state.get("meta_plan")

    if not meta_plan:
        return False

    termination_criteria = meta_plan.get(
        "termination_criteria",
        {},
    )

    maximum_iterations = termination_criteria.get(
        "maximum_iterations"
    )

    if maximum_iterations is None:
        return False

    return (
        state.get("iteration_count", 0)
        >= maximum_iterations
    )


def minimum_iterations_reached(
    state: InterviewState,
) -> bool:
    """
    Determine whether the minimum required number of
    interview iterations has been completed.
    """

    meta_plan = state.get("meta_plan")

    if not meta_plan:
        return False

    termination_criteria = meta_plan.get(
        "termination_criteria",
        {},
    )

    minimum_iterations = termination_criteria.get(
        "minimum_iterations"
    )

    if minimum_iterations is None:
        return False

    return (
        state.get("iteration_count", 0)
        >= minimum_iterations
    )


# ============================================================
# CRITICAL INFORMATION
# ============================================================

def get_critical_requirements(
    state: InterviewState,
) -> List[InformationRequirement]:
    """
    Return information requirements classified as critical
    by the Meta Plan.
    """

    meta_plan = state.get("meta_plan")

    if not meta_plan:
        return []

    return [
        requirement
        for requirement in meta_plan.get(
            "required_information",
            [],
        )
        if requirement.get("criticality") == "critical"
    ]


def get_requirement_knowledge(
    state: InterviewState,
    requirement: InformationRequirement,
) -> Dict[str, Any]:
    """
    Resolve the KnowledgeItem corresponding to a Meta Plan
    requirement using the canonical parameter-to-state mapping.

    Decision Policy and Gap Analysis therefore reason over
    the same underlying consumer knowledge.
    """

    parameter = requirement.get("parameter")

    if not parameter:
        return {
            "sufficiency": None,
            "confidence": None,
            "verification_status": "unverified",
            "applicability_status": "unknown",
        }

    mapping = PARAMETER_MAPPING.get(parameter)

    if not mapping:
        return {
            "sufficiency": None,
            "confidence": None,
            "verification_status": "unverified",
            "applicability_status": "unknown",
        }

    state_field = mapping.get("state_field")
    state_type = mapping.get("state_type")

    if not state_field:
        return {
            "sufficiency": None,
            "confidence": None,
            "verification_status": "unverified",
            "applicability_status": "unknown",
        }

    # --------------------------------------------------------
    # SINGLE KNOWLEDGE ITEM
    # --------------------------------------------------------

    if state_type == "single":

        knowledge_item = state.get(
            state_field
        )

        if not knowledge_item:
            return {
                "sufficiency": None,
                "confidence": None,
                "verification_status": "unverified",
                "applicability_status": "unknown",
            }

        return {
            "sufficiency": knowledge_item.get(
                "sufficiency"
            ),
            "confidence": knowledge_item.get(
                "confidence"
            ),
            "verification_status": knowledge_item.get(
                "verification_status",
                "unverified",
            ),
            "applicability_status": knowledge_item.get(
                "applicability_status",
                "applicable",
            ),
        }

    # --------------------------------------------------------
    # MULTIPLE KNOWLEDGE ITEMS
    # --------------------------------------------------------

    items = state.get(
        state_field,
        {},
    )

    if not items:
        return {
            "sufficiency": None,
            "confidence": None,
            "verification_status": "unverified",
            "applicability_status": "unknown",
        }

    applicable_items = [
        item
        for item in items.values()
        if item.get(
            "applicability_status",
            "applicable",
        ) != "not_applicable"
    ]

    # All discovered items are not applicable.
    if not applicable_items:
        return {
            "sufficiency": "comprehensive",
            "confidence": "high",
            "verification_status": "verified",
            "applicability_status": "not_applicable",
        }

    # Use the weakest meaningful state.
    sufficiency_values = [
        item.get(
            "sufficiency",
            "basic",
        )
        for item in applicable_items
    ]

    confidence_values = [
        item.get(
            "confidence",
            "low",
        )
        for item in applicable_items
    ]

    lowest_sufficiency = min(
        sufficiency_values,
        key=lambda value: SUFFICIENCY_LEVELS.get(
            value,
            0,
        ),
    )

    lowest_confidence = min(
        confidence_values,
        key=lambda value: CONFIDENCE_LEVELS.get(
            value,
            0,
        ),
    )

    verification_status = (
        "verified"
        if all(
            item.get(
                "verification_status"
            ) == "verified"
            for item in applicable_items
        )
        else "unverified"
    )

    return {
        "sufficiency": lowest_sufficiency,
        "confidence": lowest_confidence,
        "verification_status": verification_status,
        "applicability_status": "applicable",
    }


def critical_information_satisfied(
    state: InterviewState,
) -> bool:
    """
    Determine whether all critical information has reached
    the global sufficiency threshold defined by the Meta Plan.

    KnowledgeItems are the source of truth.

    Individual sufficiency requirements remain the
    responsibility of Gap Analysis.
    """

    meta_plan = state.get("meta_plan")

    if not meta_plan:
        return False

    critical_requirements = get_critical_requirements(
        state
    )

    if not critical_requirements:
        return True

    termination_criteria = meta_plan.get(
        "termination_criteria",
        {},
    )

    required_status = termination_criteria.get(
        "critical_information_status",
        "adequate",
    )

    required_level = SUFFICIENCY_LEVELS.get(
        required_status,
        0,
    )

    for requirement in critical_requirements:

        knowledge = get_requirement_knowledge(
            state,
            requirement,
        )

        # Not-applicable critical information does not
        # prevent termination.
        if (
            knowledge.get("applicability_status")
            == "not_applicable"
        ):
            continue

        current_status = (
            knowledge.get("sufficiency")
            or "missing"
        )

        current_level = SUFFICIENCY_LEVELS.get(
            current_status,
            0,
        )

        if current_level < required_level:
            return False

    return True


# ============================================================
# CRITICAL UNRESOLVED GAPS
# ============================================================

def get_critical_gaps(
    state: InterviewState,
) -> List[InformationGap]:
    """
    Return unresolved information gaps classified as critical.
    """

    return [
        gap
        for gap in state.get(
            "unresolved_gaps",
            [],
        )
        if gap.get("criticality") == "critical"
    ]


# ============================================================
# GAP MATCHING
# ============================================================

def proposal_targets_gap(
    proposal: Proposal,
    gap: InformationGap,
) -> bool:
    """
    Determine whether a proposal directly targets
    an information gap.
    """

    return (
        proposal.get("target_gap")
        == gap.get("parameter")
    )


# ============================================================
# PROPOSAL VIABILITY
# ============================================================

def proposal_is_viable(
    proposal: Proposal,
) -> bool:
    """
    Determine whether a proposal passes the minimum
    decision-policy viability guardrail.

    This is NOT another evaluation or ranking step.
    """

    low_information_gain = (
        proposal.get(
            "expected_information_gain"
        )
        == "low"
    )

    low_state_improvement = (
        proposal.get(
            "expected_state_improvement"
        )
        == "low"
    )

    high_risk = (
        proposal.get("risk")
        == "high"
    )

    high_cost = (
        proposal.get("cost")
        == "high"
    )

    high_burden = (
        proposal.get("consumer_burden")
        == "high"
    )

    low_value = (
        low_information_gain
        and low_state_improvement
    )

    excessive_burden = (
        high_risk
        and high_cost
        and high_burden
    )

    if low_value and excessive_burden:
        return False

    return True


# ============================================================
# CRITICAL GAP PROPOSAL
# ============================================================

def find_ranked_critical_gap_proposal(
    state: InterviewState,
) -> Optional[Proposal]:
    """
    Find the highest-ranked viable proposal that addresses
    an unresolved critical information gap.

    The proposals are already ranked by proposal_ranker.py.

    No additional ranking is performed here.
    """

    critical_gaps = get_critical_gaps(
        state
    )

    if not critical_gaps:
        return None

    ranked_proposals = state.get(
        "evaluated_proposals",
        [],
    )

    for proposal in ranked_proposals:

        if not proposal_is_viable(
            proposal
        ):
            continue

        for gap in critical_gaps:

            if proposal_targets_gap(
                proposal,
                gap,
            ):
                return proposal

    return None


# ============================================================
# BEST RANKED PROPOSAL
# ============================================================

def get_best_viable_proposal(
    state: InterviewState,
) -> Optional[Proposal]:
    """
    Return the first viable proposal from the already-ranked
    proposal list.

    No additional ranking is performed.
    """

    ranked_proposals = state.get(
        "evaluated_proposals",
        [],
    )

    for proposal in ranked_proposals:

        if proposal_is_viable(
            proposal
        ):
            return proposal

    return None


# ============================================================
# MAIN DECISION POLICY
# ============================================================

def decide_next_action(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Determine the next strategic interview action.

    Decision Policy responsibilities:
        - enforce iteration boundaries
        - evaluate termination conditions
        - protect unresolved critical information
        - select one ranked proposal for execution
        - determine whether the interview continues

    Decision Policy does NOT:
        - generate proposals
        - evaluate proposals
        - rank proposals
        - generate questions
        - execute proposals
    """

    # --------------------------------------------------------
    # 1. HARD MAXIMUM ITERATION STOP
    # --------------------------------------------------------

    if maximum_iterations_reached(state):

        return {
            "selected_proposal": None,
            "next_action": "stop",
            "termination_reason": (
                "maximum_iterations_reached"
            ),
        }

    # --------------------------------------------------------
    # 2. NORMAL TERMINATION
    # --------------------------------------------------------

    if (
        minimum_iterations_reached(state)
        and critical_information_satisfied(state)
    ):

        return {
            "selected_proposal": None,
            "next_action": "stop",
            "termination_reason": (
                "critical_information_satisfied"
            ),
        }

    # --------------------------------------------------------
    # 3. PROTECT CRITICAL UNRESOLVED INFORMATION
    # --------------------------------------------------------

    critical_proposal = (
        find_ranked_critical_gap_proposal(
            state
        )
    )

    if critical_proposal is not None:

        return {
            "selected_proposal": critical_proposal,
            "next_action": "ask_question",
        }

    # --------------------------------------------------------
    # 4. USE HIGHEST-RANKED VIABLE PROPOSAL
    # --------------------------------------------------------

    best_proposal = (
        get_best_viable_proposal(
            state
        )
    )

    if best_proposal is not None:

        return {
            "selected_proposal": best_proposal,
            "next_action": "ask_question",
        }

    # --------------------------------------------------------
    # 5. NO USEFUL PROPOSAL REMAINS
    # --------------------------------------------------------

    if minimum_iterations_reached(state):

        return {
            "selected_proposal": None,
            "next_action": "stop",
            "termination_reason": (
                "no_meaningful_proposals"
            ),
        }

    # --------------------------------------------------------
    # 6. FALLBACK
    # --------------------------------------------------------

    return {
        "selected_proposal": None,
        "next_action": "stop",
        "termination_reason": (
            "no_viable_action"
        ),
    }