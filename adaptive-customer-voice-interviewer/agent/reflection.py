from typing import Dict, Any, Optional

from agent.state import InterviewState, StateSnapshot


# ============================================================
# STATE SNAPSHOT
# ============================================================

def create_state_snapshot(
    state: InterviewState
) -> StateSnapshot:
    """
    Creates a lightweight snapshot of the current state quality.

    The snapshot contains only the information required for
    deterministic before/after reflection across interview turns.

    IMPORTANT:
    This snapshot should be created BEFORE asking the selected
    question so that the resulting state can later be compared
    against the state after the consumer's response.
    """

    unresolved_gaps = state.get(
        "unresolved_gaps",
        []
    )

    unresolved_gap_parameters = [
        gap.get("parameter")
        for gap in unresolved_gaps
        if gap.get("parameter")
    ]

    return {
        "iteration": state.get(
            "iteration_count",
            0
        ),

        "information_sufficiency": dict(
            state.get(
                "information_sufficiency",
                {}
            )
        ),

        "confidence": dict(
            state.get(
                "confidence",
                {}
            )
        ),

        "verification_status": dict(
            state.get(
                "verification_status",
                {}
            )
        ),

        "unresolved_gap_parameters":
            unresolved_gap_parameters,
    }


# ============================================================
# PARAMETER TO EVIDENCE CATEGORY MAPPING
# ============================================================

PARAMETER_CATEGORY_MAPPING = {

    "Product awareness": "product_awareness",

    "Product relationship": "product_relationship",

    "Usage experience": "usage_experience",

    "Overall sentiment": "overall_sentiment",

    "Product perception": "product_perception",

    "Feature-specific sentiment": "feature_sentiment",

    "Needs": "needs",

    "Pain points": "pain_points",

    "Unmet needs": "unmet_needs",

    "Expectations": "expectations",

    "Aspirations": "aspirations",

    "Purchase intent": "purchase_intent",

    "Buying constraints": "buying_constraints",

    "Adoption barriers": "adoption_barriers",

    "Competitor perception": "competitor_information",
}


# ============================================================
# QUALITATIVE ORDERING
# ============================================================

CONFIDENCE_ORDER = {
    "low": 1,
    "medium": 2,
    "high": 3,
}


SUFFICIENCY_ORDER = {
    "missing": 1,
    "basic": 2,
    "adequate": 3,
    "comprehensive": 4,
}


VERIFICATION_ORDER = {
    "unverified": 1,
    "partially_verified": 2,
    "verified": 3,
}


# ============================================================
# HELPER: CHECK TARGET EVIDENCE
# ============================================================

def has_relevant_evidence(
    state: InterviewState,
    target_gap: str
) -> bool:
    """
    Checks whether the latest consumer response produced
    meaningful evidence relevant to the target information gap.

    This checks only the latest decomposition.

    It does not determine whether the information is sufficient.
    """

    decomposition = state.get(
        "latest_decomposition"
    ) or {}

    evidence = decomposition.get(
        "evidence",
        []
    )

    target_category = (
        PARAMETER_CATEGORY_MAPPING.get(
            target_gap
        )
    )

    if not target_category:
        return False

    for item in evidence:

        if item.get("category") == target_category:

            value = item.get("value")

            if value and str(value).strip():
                return True

    return False


# ============================================================
# HELPER: CHECK GAP RESOLUTION
# ============================================================

def is_gap_resolved(
    state: InterviewState,
    target_gap: str
) -> bool:
    """
    Determines whether the targeted information gap still
    exists after the latest state update.

    A gap is considered resolved when its parameter is no
    longer present in unresolved_gaps.
    """

    unresolved_gaps = state.get(
        "unresolved_gaps",
        []
    )

    for gap in unresolved_gaps:

        if gap.get("parameter") == target_gap:
            return False

    return True


# ============================================================
# HELPER: DETECT CONTRADICTIONS
# ============================================================

def detect_contradictions(
    state: InterviewState
) -> bool:
    """
    Checks whether contradictions were explicitly detected
    during answer decomposition.

    Reflection does not independently reinterpret the
    consumer's language.
    """

    decomposition = state.get(
        "latest_decomposition"
    ) or {}

    contradictions = decomposition.get(
        "potential_contradictions",
        []
    )

    return bool(contradictions)


# ============================================================
# HELPER: REMAINING UNCERTAINTY
# ============================================================

def determine_remaining_uncertainty(
    state: InterviewState,
    target_gap: str,
    gap_resolved: bool
) -> str:
    """
    Determines the qualitative level of uncertainty remaining
    around the targeted information parameter.
    """

    if gap_resolved:
        return "low"

    unresolved_gaps = state.get(
        "unresolved_gaps",
        []
    )

    for gap in unresolved_gaps:

        if gap.get("parameter") != target_gap:
            continue

        gap_type = gap.get("gap_type")

        if gap_type == "missing":
            return "high"

        if gap_type == "verification_required":
            return "medium"

        if gap_type == "insufficient":
            return "medium"

        if gap_type == "not_applicable":
            return "low"

    return "medium"


# ============================================================
# HELPER: ADDITIONAL QUESTION VALUE
# ============================================================

def determine_additional_question_value(
    state: InterviewState,
    target_gap: str,
    gap_resolved: bool
) -> str:
    """
    Determines whether another question about the same
    information parameter is likely to provide useful
    additional information.

    This is qualitative and deterministic.
    """

    if gap_resolved:
        return "low"

    unresolved_gaps = state.get(
        "unresolved_gaps",
        []
    )

    for gap in unresolved_gaps:

        if gap.get("parameter") != target_gap:
            continue

        criticality = gap.get(
            "criticality",
            "medium"
        )

        gap_type = gap.get(
            "gap_type"
        )

        if criticality == "critical":
            return "high"

        if criticality == "high":

            if gap_type in [
                "missing",
                "verification_required"
            ]:
                return "high"

            return "medium"

        if criticality == "medium":

            if gap_type == "missing":
                return "medium"

            return "low"

        return "low"

    return "low"


# ============================================================
# HELPER: QUALITATIVE VALUE COMPARISON
# ============================================================

def compare_qualitative_levels(
    previous_value: Optional[str],
    current_value: Optional[str],
    ordering: Dict[str, int]
) -> str:
    """
    Compares two qualitative levels.

    Returns:

        increased
        decreased
        unchanged

    Unknown or missing values are treated conservatively
    as unchanged.
    """

    if (
        previous_value not in ordering
        or current_value not in ordering
    ):
        return "unchanged"

    previous_score = ordering[previous_value]
    current_score = ordering[current_value]

    if current_score > previous_score:
        return "increased"

    if current_score < previous_score:
        return "decreased"

    return "unchanged"


# ============================================================
# HELPER: CONFIDENCE CHANGE FROM SNAPSHOT
# ============================================================

def determine_confidence_change(
    state: InterviewState,
    target_gap: str,
    understanding_improved: bool,
    gap_resolved: bool,
    contradictions_detected: bool
) -> str:
    """
    Determines qualitative confidence change for the targeted
    information parameter.

    Primary signal:
        Previous state snapshot -> Current state

    Secondary signals:
        - gap resolution
        - relevant evidence
        - contradictions

    Returns one of the values supported by Reflection:

        decreased
        unchanged
        slightly_increased
        increased
    """

    if contradictions_detected:
        return "decreased"

    previous_snapshot = state.get(
        "previous_state_snapshot"
    )

    if not previous_snapshot:
        # Safe fallback for the first turn or if the snapshot
        # was not captured before the question.
        if gap_resolved:
            return "increased"

        if understanding_improved:
            return "slightly_increased"

        return "unchanged"

    previous_confidence = (
        previous_snapshot
        .get("confidence", {})
        .get(target_gap)
    )

    current_confidence = (
        state.get("confidence", {})
        .get(target_gap)
    )

    confidence_change = compare_qualitative_levels(
        previous_confidence,
        current_confidence,
        CONFIDENCE_ORDER
    )

    if confidence_change == "decreased":
        return "decreased"

    if confidence_change == "increased":

        # A full confidence-level increase represents
        # meaningful improvement.
        if gap_resolved:
            return "increased"

        return "slightly_increased"

    # If the qualitative confidence level did not change,
    # gap resolution still represents a meaningful improvement.
    if gap_resolved:
        return "increased"

    if understanding_improved:
        return "slightly_increased"

    return "unchanged"


# # ============================================================
# # HELPER: SUFFICIENCY CHANGE
# # ============================================================

def determine_sufficiency_change(
    state: InterviewState,
    target_gap: str,
) -> str:
    previous_snapshot = state.get(
        "previous_state_snapshot"
    )

    if not previous_snapshot:
        return "unchanged"

    previous_sufficiency = (
        previous_snapshot
        .get("information_sufficiency", {})
        .get(target_gap)
    )

    current_sufficiency = (
        state
        .get("information_sufficiency", {})
        .get(target_gap)
    )

    return compare_qualitative_levels(
        previous_sufficiency,
        current_sufficiency,
        SUFFICIENCY_ORDER,
    )


# # ============================================================
# # HELPER: VERIFICATION CHANGE
# # ============================================================

def determine_verification_change(
    state: InterviewState,
    target_gap: str,
) -> str:
    previous_snapshot = state.get(
        "previous_state_snapshot"
    )

    if not previous_snapshot:
        return "unchanged"

    previous_status = (
        previous_snapshot
        .get("verification_status", {})
        .get(target_gap)
    )

    current_status = (
        state
        .get("verification_status", {})
        .get(target_gap)
    )

    return compare_qualitative_levels(
        previous_status,
        current_status,
        VERIFICATION_ORDER,
    )


# # ============================================================
# # HELPER: GAP STATUS CHANGE
# # ============================================================

def determine_gap_status_change(
    state: InterviewState,
    target_gap: str,
) -> str:
    previous_snapshot = state.get(
        "previous_state_snapshot"
    )

    current_gaps = state.get(
        "unresolved_gaps",
        [],
    )

    current_gap_parameters = {
        gap.get("parameter")
        for gap in current_gaps
        if gap.get("parameter")
    }

    current_exists = (
        target_gap in current_gap_parameters
    )

    if not previous_snapshot:
        return (
            "unresolved"
            if current_exists
            else "resolved"
        )

    previous_gap_parameters = set(
        previous_snapshot.get(
            "unresolved_gap_parameters",
            [],
        )
    )

    previous_exists = (
        target_gap in previous_gap_parameters
    )

    if previous_exists and not current_exists:
        return "resolved"

    if previous_exists and current_exists:
        return "unchanged"

    if not previous_exists and current_exists:
        return "unresolved"

    return "unchanged"


# ============================================================
# MAIN REFLECTION NODE
# ============================================================

def reflect_on_response(
    state: InterviewState
) -> Dict[str, Any]:
    """
    Deterministically evaluates the effect of the latest
    consumer response on the interview state.

    This component:

    - Does not call an LLM
    - Does not reinterpret consumer language
    - Does not generate questions
    - Does not modify knowledge
    - Does not select proposals

    It compares the state before and after the latest
    consumer response and evaluates the effect on the
    targeted information gap.
    """

    selected_proposal = state.get(
        "selected_proposal"
    )

    # --------------------------------------------------------
    # NO TARGET
    # --------------------------------------------------------

    if not selected_proposal:

        return {
            "reflection": None
        }

    previous_question = selected_proposal.get(
        "question"
    )

    target_gap = selected_proposal.get(
        "target_gap"
    )

    if not target_gap:

        return {
            "reflection": None
        }

    # --------------------------------------------------------
    # UNDERSTANDING IMPROVEMENT
    # --------------------------------------------------------

    understanding_improved = has_relevant_evidence(
        state,
        target_gap
    )

    # --------------------------------------------------------
    # GAP RESOLUTION
    # --------------------------------------------------------

    gap_resolved = is_gap_resolved(
        state,
        target_gap
    )

    # --------------------------------------------------------
    # CONTRADICTIONS
    # --------------------------------------------------------

    contradictions_detected = detect_contradictions(
        state
    )

    # --------------------------------------------------------
    # REMAINING UNCERTAINTY
    # --------------------------------------------------------

    remaining_uncertainty = (
        determine_remaining_uncertainty(
            state,
            target_gap,
            gap_resolved
        )
    )

    # --------------------------------------------------------
    # ADDITIONAL QUESTION VALUE
    # --------------------------------------------------------

    additional_question_value = (
        determine_additional_question_value(
            state,
            target_gap,
            gap_resolved
        )
    )

    # --------------------------------------------------------
    # CONFIDENCE CHANGE
    # --------------------------------------------------------

    confidence_change = determine_confidence_change(
        state,
        target_gap,
        understanding_improved,
        gap_resolved,
        contradictions_detected
    )

    # --------------------------------------------------------
    # STATE DELTA
    # --------------------------------------------------------

    sufficiency_change = determine_sufficiency_change(
        state,
        target_gap
    )

    verification_change = determine_verification_change(
        state,
        target_gap
    )

    gap_status_change = determine_gap_status_change(
        state,
        target_gap
    )

    # --------------------------------------------------------
    # REFLECTION RESULT
    # --------------------------------------------------------

    reflection = {

        "previous_question": previous_question,

        "understanding_improved": (
            understanding_improved
        ),

        "gap_resolved": gap_resolved,

        "contradictions_detected": (
            contradictions_detected
        ),

        "confidence_change": confidence_change,

        "remaining_uncertainty": (
            remaining_uncertainty
        ),

        "additional_question_value": (
            additional_question_value
        ),

        "sufficiency_change": sufficiency_change,
        "verification_change": verification_change,
        "gap_status_change": gap_status_change,
    }

    return {
        "reflection": reflection
    }
