from typing import List, Literal

from pydantic import BaseModel, Field


ResearchParameter = Literal[
    "Product awareness",
    "Product relationship",
    "Usage experience",
    "Overall sentiment",
    "Product perception",
    "Feature-specific sentiment",
    "Needs",
    "Pain points",
    "Unmet needs",
    "Expectations",
    "Aspirations",
    "Purchase intent",
    "Buying constraints",
    "Adoption barriers",
    "Competitor perception",
]


# ============================================================
# INFORMATION REQUIREMENT
# ============================================================

class InformationRequirementSchema(BaseModel):
    """
    Defines a piece of information the interview should attempt
    to collect in order to satisfy the research objective.
    """

    parameter: ResearchParameter = Field(
        description=(
            "The information parameter to investigate, "
            "for example product awareness, product relationship, "
            "usage experience, pain points, unmet needs, "
            "aspirations, or buying constraints."
        )
    )

    importance: Literal[
        "low",
        "medium",
        "high"
    ] = Field(
        description=(
            "Relative importance of this parameter to the "
            "overall research objective."
        )
    )

    goal_alignment: Literal[
        "low",
        "medium",
        "high"
    ] = Field(
        description=(
            "How strongly understanding this parameter contributes "
            "to achieving the research goal."
        )
    )

    sufficiency_requirement: Literal[
        "basic",
        "adequate",
        "comprehensive"
    ] = Field(
        description=(
            "The general depth of understanding required before "
            "this parameter can be considered sufficiently explored."
        )
    )

    criticality: Literal[
        "low",
        "medium",
        "high",
        "critical"
    ] = Field(
        description=(
            "How essential this parameter is to the research. "
            "Use: critical, high, medium, or low."
        )
    )

    verification_requirement: bool = Field(
        description=(
            "Whether ambiguous, incomplete, weak, or potentially "
            "contradictory consumer statements should be clarified "
            "through follow-up questions."
        )
    )

    confidence_requirement: Literal[
        "low",
        "medium",
        "high"
    ] = Field(
        description=(
            "The general confidence level required before the "
            "interpreted consumer information is considered "
            "reliable enough for the research."
        )
    )

    expected_goal_improvement: Literal[
        "low",
        "medium",
        "high"
    ] = Field(
        description=(
            "Expected improvement toward achieving the research "
            "goal if this information parameter is sufficiently resolved."
        )
    )

    estimated_cost: Literal[
        "low",
        "medium",
        "high"
    ] = Field(
        description=(
            "Estimated system effort required to investigate this "
            "information parameter. Consider reasoning, generation, "
            "evaluation, and processing effort."
        )
    )

    estimated_consumer_burden: Literal[
        "low",
        "medium",
        "high"
    ] = Field(
        description=(
            "Estimated effort or inconvenience imposed on the "
            "consumer when collecting this information."
        )
    )

# ============================================================
# TERMINATION CRITERIA
# ============================================================

class TerminationCriteriaSchema(BaseModel):
    """
    TERMINATION CRITERIA

    You MUST generate the termination_criteria object containing
    ALL FOUR required fields.

    1. minimum_iterations

    Return an integer greater than or equal to 1.

    This represents the minimum number of interview iterations
    that should occur before termination can be considered.

    2. maximum_iterations

    Return an integer greater than or equal to 1.

    This represents the maximum number of interview iterations
    allowed.

    Ensure maximum_iterations is greater than or equal to
    minimum_iterations.

    3. critical_information_status

    Return exactly one of:

    - basic
    - adequate
    - comprehensive

    This represents the required overall understanding of critical
    information before the interview can normally conclude.

    Choose the level according to the research goal and the
    importance of the information being investigated.

    4. diminishing_information_gain

    Return exactly one of:

    - low
    - medium
    - high

    This represents the expected information-gain level below
    which continuing the interview may no longer provide
    meaningful value.

    Interpretation:

    - low: Further questions are unlikely to provide meaningful
    new information.
    - medium: Further questions may provide some additional
    information, but the expected value is limited.
    - high: Further questions are still expected to provide
    substantial new information.

    IMPORTANT:

    - Return all four fields.
    - Do not omit termination_criteria.
    - Do not create additional termination fields.
    - Do not use qualitative values for iteration counts.
    - Keep numerical iteration limits reasonable for the research goal.
    """

    minimum_iterations: int = Field(
        ge=1,
        description=(
            "Minimum number of interview iterations before "
            "termination can be considered."
        )
    )

    maximum_iterations: int = Field(
        ge=1,
        description=(
            "Maximum number of interview iterations allowed."
        )
    )

    critical_information_status: Literal[
        "basic",
        "adequate",
        "comprehensive"
    ] = Field(
        description=(
            "Required overall level of understanding for critical "
            "information before the interview can normally conclude."
        )
    )

    diminishing_information_gain: Literal[
        "low",
        "medium",
        "high"
    ] = Field(
        description=(
            "Expected information gain level indicating whether "
            "continuing the interview is likely to remain valuable. "
            "Low information gain may support termination."
        )
    )


# ============================================================
# META PLAN
# ============================================================

class MetaPlanSchema(BaseModel):
    """
    High-level adaptive research plan generated before
    or at the beginning of the interview.
    """

    research_goal: str = Field(
        description="The primary research objective."
    )

    required_information: List[
        InformationRequirementSchema
    ] = Field(
        description=(
            "The key information dimensions the interview should "
            "attempt to explore adaptively."
        )
    )

    termination_criteria: TerminationCriteriaSchema = Field(
        description=(
            "Conditions and operational limits that help determine "
            "when the interview should conclude."
        )
    )
    