from typing import Dict, Any, List, Optional

from agent.state import (
    InterviewState,
    InformationGap,
    InformationRequirement,
    KnowledgeItem,
    GapType,
)


# ============================================================
# PARAMETER TO STATE MAPPING
# ============================================================

PARAMETER_MAPPING = {

    "Product awareness": {
        "category": "product_awareness",
        "state_type": "single",
        "state_field": "product_awareness",
    },

    "Product relationship": {
        "category": "product_relationship",
        "state_type": "single",
        "state_field": "product_relationship",
    },

    "Usage experience": {
        "category": "usage_experience",
        "state_type": "multiple",
        "state_field": "features",
    },

    "Overall sentiment": {
        "category": "overall_sentiment",
        "state_type": "single",
        "state_field": "overall_sentiment",
    },

    "Product perception": {
        "category": "product_perception",
        "state_type": "single",
        "state_field": "product_perception",
    },

    "Feature-specific sentiment": {
        "category": "feature_sentiment",
        "state_type": "multiple",
        "state_field": "feature_sentiment",
    },

    "Needs": {
        "category": "needs",
        "state_type": "multiple",
        "state_field": "needs",
    },

    "Pain points": {
        "category": "pain_points",
        "state_type": "multiple",
        "state_field": "pain_points",
    },

    "Unmet needs": {
        "category": "unmet_needs",
        "state_type": "multiple",
        "state_field": "needs",
    },

    "Expectations": {
        "category": "expectations",
        "state_type": "multiple",
        "state_field": "expectations",
    },

    "Aspirations": {
        "category": "aspirations",
        "state_type": "multiple",
        "state_field": "aspirations",
    },

    "Purchase intent": {
        "category": "purchase_intent",
        "state_type": "single",
        "state_field": "purchase_intent",
    },

    "Buying constraints": {
        "category": "buying_constraints",
        "state_type": "multiple",
        "state_field": "buying_constraints",
    },

    "Adoption barriers": {
        "category": "adoption_barriers",
        "state_type": "multiple",
        "state_field": "adoption_barriers",
    },

    "Competitor perception": {
        "category": "competitor_information",
        "state_type": "multiple",
        "state_field": "competitor_information",
    },
}


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
# KNOWLEDGE EXTRACTION
# ============================================================

def get_single_knowledge(
    state: InterviewState,
    field: str
) -> Dict[str, Any]:
    """
    Returns qualitative state information from a single
    KnowledgeItem.
    """

    item = state.get(
        field
    )

    if not item:

        return {
            "sufficiency": None,
            "confidence": None,
            "verification_status": "unverified",
            "applicability_status": "unknown",
        }

    return {
        "sufficiency": item.get(
            "sufficiency"
        ),

        "confidence": item.get(
            "confidence"
        ),

        "verification_status": item.get(
            "verification_status",
            "unverified",
        ),

        "applicability_status": item.get(
            "applicability_status",
            "applicable",
        ),
    }


# ============================================================
# MULTIPLE KNOWLEDGE AGGREGATION
# ============================================================

def get_multiple_knowledge(
    state: InterviewState,
    field: str
) -> Dict[str, Any]:
    """
    Aggregates qualitative information from multiple
    KnowledgeItems.

    The aggregation intentionally avoids numerical averaging.

    The weakest meaningful state is used because incomplete
    understanding of important sub-items may still represent
    an unresolved research gap.
    """

    items = state.get(
        field,
        {}
    )

    if not items:

        return {
            "sufficiency": None,
            "confidence": None,
            "verification_status": "unverified",
            "applicability_status": "unknown",
        }

    knowledge_items = list(
        items.values()
    )

    applicable_items = [
        item
        for item in knowledge_items
        if item.get(
            "applicability_status",
            "applicable",
        ) != "not_applicable"
    ]

    if not applicable_items:

        return {
            "sufficiency": "comprehensive",
            "confidence": "high",
            "verification_status": "verified",
            "applicability_status": "not_applicable",
        }

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


# ============================================================
# SUFFICIENCY COMPARISON
# ============================================================

def meets_sufficiency_requirement(
    current: Optional[str],
    required: str
) -> bool:
    """
    Determines whether the current qualitative sufficiency
    level satisfies the Meta Plan requirement.
    """

    if current is None:
        return False

    current_level = SUFFICIENCY_LEVELS.get(
        current,
        0,
    )

    required_level = SUFFICIENCY_LEVELS.get(
        required,
        0,
    )

    return (
        current_level >= required_level
    )


# ============================================================
# GAP CLASSIFICATION
# ============================================================

def classify_gap(
    requirement: InformationRequirement,
    knowledge: Dict[str, Any]
) -> Optional[GapType]:
    """
    Classifies the type of unresolved information gap.

    Gap types:

        missing
            No meaningful information has been collected.

        insufficient
            Information exists but does not meet the required
            qualitative depth.

        verification_required
            Information is sufficiently explored but requires
            confirmation or clarification.
    """

    # --------------------------------------------------------
    # NOT APPLICABLE
    # --------------------------------------------------------

    if (
        knowledge.get(
            "applicability_status"
        )
        == "not_applicable"
    ):

        return None

    current_sufficiency = knowledge.get(
        "sufficiency"
    )

    required_sufficiency = requirement.get(
        "sufficiency_requirement",
        "adequate",
    )

    verification_required = requirement.get(
        "verification_requirement",
        False,
    )

    verification_status = knowledge.get(
        "verification_status",
        "unverified",
    )

    # --------------------------------------------------------
    # MISSING INFORMATION
    # --------------------------------------------------------

    if current_sufficiency is None:

        return "missing"

    # --------------------------------------------------------
    # INSUFFICIENT INFORMATION
    # --------------------------------------------------------

    if not meets_sufficiency_requirement(
        current=current_sufficiency,
        required=required_sufficiency,
    ):

        return "insufficient"

    # --------------------------------------------------------
    # VERIFICATION REQUIRED
    # --------------------------------------------------------

    if (
        verification_required
        and
        verification_status != "verified"
    ):

        return "verification_required"

    return None


# ============================================================
# GAP CREATION
# ============================================================

def create_information_gap(
    requirement: InformationRequirement,
    knowledge: Dict[str, Any],
    category: str,
    gap_type: GapType,
) -> InformationGap:
    """
    Creates a qualitative InformationGap.

    This function describes the gap.

    It does not rank or prioritize the gap.
    """

    return {

        "parameter": requirement[
            "parameter"
        ],

        "category": category,

        "gap_type": gap_type,

        "importance": requirement.get(
            "importance",
            "medium",
        ),

        "goal_alignment": requirement.get(
            "goal_alignment",
            "medium",
        ),

        "criticality": requirement.get(
            "criticality",
            "medium",
        ),

        "confidence": knowledge.get(
            "confidence",
            "low",
        ),

        "verification_required": requirement.get(
            "verification_requirement",
            False,
        ),

        "expected_goal_improvement": requirement.get(
            "expected_goal_improvement",
            "medium",
        ),

        "estimated_cost": requirement.get(
            "estimated_cost",
            "medium",
        ),

        "estimated_consumer_burden": requirement.get(
            "estimated_consumer_burden",
            "medium",
        ),
    }


# ============================================================
# MAIN GAP ANALYZER
# ============================================================

def analyze_gaps(
    state: InterviewState,
) -> Dict[str, List[InformationGap]]:
    """
    Compares the Meta Plan against the accumulated
    interview knowledge state.

    This node determines WHAT information remains unresolved.
    """

    meta_plan = state.get(
        "meta_plan"
    )

    if not meta_plan:
        return {
            "unresolved_gaps": []
        }

    unresolved_gaps: List[InformationGap] = []

    requirements: List[InformationRequirement] = meta_plan.get(
        "required_information",
        []
    )

    for requirement in requirements:

        parameter = requirement.get(
            "parameter"
        )

        mapping = PARAMETER_MAPPING.get(
            parameter
        )

        if not mapping:
            raise ValueError(
                f"Unsupported research parameter: {parameter}"
            )

        state_type = mapping[
            "state_type"
        ]

        state_field = mapping[
            "state_field"
        ]

        category = mapping[
            "category"
        ]

        # ----------------------------------------------------
        # Retrieve knowledge
        # ----------------------------------------------------

        if state_type == "single":

            knowledge = get_single_knowledge(
                state,
                state_field,
            )

        else:

            knowledge = get_multiple_knowledge(
                state,
                state_field,
            )

        # ----------------------------------------------------
        # Classify gap
        # ----------------------------------------------------

        gap_type = classify_gap(
            requirement,
            knowledge,
        )

        # ----------------------------------------------------
        # Create gap
        # ----------------------------------------------------

        if gap_type is not None:

            gap = create_information_gap(
                requirement=requirement,
                knowledge=knowledge,
                category=category,
                gap_type=gap_type,
            )
            unresolved_gaps.append(
                gap
            )

    return {
        "unresolved_gaps": unresolved_gaps
    }