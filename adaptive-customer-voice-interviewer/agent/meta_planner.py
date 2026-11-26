from dotenv import load_dotenv

from agent.llm import get_structured_llm
from agent.proposal_generator import (
    is_rate_limit_error,
    known_customer_context,
)

from typing import cast

from agent.state import InterviewState
from agent.schemas import MetaPlanSchema
from agent.observability import get_logger

logger = get_logger(__name__)



load_dotenv()


# ============================================================
# META PLANNER
# ============================================================

def build_rate_limit_meta_plan(state: InterviewState) -> dict:
    """Return a deterministic plan when the provider is rate limited."""

    context = state.get("research_context") or {}

    def requirement(parameter: str) -> dict:
        return {
            "parameter": parameter,
            "importance": "high",
            "goal_alignment": "high",
            "sufficiency_requirement": "adequate",
            "criticality": "high",
            "verification_requirement": True,
            "confidence_requirement": "medium",
            "expected_goal_improvement": "high",
            "estimated_cost": "low",
            "estimated_consumer_burden": "low",
        }

    return {
        "research_goal": context.get(
            "research_goal",
            "Understand the customer experience.",
        ),
        "required_information": [
            requirement(parameter)
            for parameter in (
                "Usage experience",
                "Pain points",
                "Needs",
            )
        ],
        "termination_criteria": {
            "minimum_iterations": 4,
            "maximum_iterations": 8,
            "critical_information_status": "adequate",
            "diminishing_information_gain": "low",
        },
    }


def create_meta_plan(state: InterviewState) -> dict:
    """
    Creates a high-level adaptive research plan based on the
    product research context.

    The plan defines WHAT information should be explored,
    not a fixed sequence of interview questions.

    Information priorities and requirements are represented
    qualitatively rather than through artificial numerical scores.
    """

    research_context = state["research_context"]
    audience = known_customer_context(state)

    structured_llm = get_structured_llm(MetaPlanSchema)

    prompt = f"""
You are designing a meta-plan for an adaptive customer research interview.

The goal is NOT to create a fixed questionnaire.

The goal is to determine WHAT information dimensions should be explored
during a dynamic conversation with a consumer.

RESEARCH CONTEXT

Company: {research_context["company_name"]}

Product: {research_context["product_name"]}

Product Category: {research_context["product_category"]}

Research Goal:
{research_context["research_goal"]}

Known Customer Segment:
{audience["segment"] or "Not specified"}

Target Audience:
{audience["audience"] or "Not specified"}

Interview Constraints:
{", ".join(str(item) for item in audience["constraints"]) or "None"}


RESEARCH PRINCIPLE

The consumer is the sole source of research information.

The consumer may:

- Currently use the product
- Have used the product previously
- Know about the product but never used it
- Be interested in buying it
- Be curious about it
- Have little or no awareness of it

The interview must adapt according to the consumer's relationship
with the product.

Do NOT assume every information dimension will be relevant
for every consumer.

When a customer segment or target audience is already known,
treat the relationship and awareness as established. Omit the
"Product relationship" and "Product awareness" dimensions
because asking them would repeat information the researcher
already provided.


INFORMATION DIMENSIONS

Identify important information dimensions that may need to be explored,
such as:

- Product relationship
- Product awareness
- Usage experience
- Overall sentiment
- Product perception
- Feature-specific sentiment
- Needs
- Pain points
- Unmet needs
- Expectations
- Aspirations
- Purchase intent
- Buying constraints
- Adoption barriers
- Competitor perception

You may include other dimensions if they are directly relevant
to the research goal.

Include at most EIGHT information dimensions. Prioritize the
dimensions most relevant to the research goal and omit lower-value
ones. This keeps the structured response within provider output
limits.


FOR EACH INFORMATION REQUIREMENT

Define:

1. parameter

The information dimension to be explored.


2. importance

How important is this information to achieving the research goal?

Use only:

- low
- medium
- high


3. goal_alignment

How strongly does understanding this parameter contribute
to the stated research goal?

Use only:

- low
- medium
- high


4. sufficiency_requirement

What level of understanding should generally be achieved
before this parameter can be considered sufficiently explored?

Use only:

- basic
- adequate
- comprehensive


5. criticality

How essential is this information to the interview?

Use only:

- low
- medium
- high
- critical


6. verification_requirement

Specify whether additional clarification or confirmation may be
needed when the consumer provides ambiguous, incomplete, or
potentially contradictory information.

Return:

- true
- false


7. confidence_requirement

What level of confidence should generally be achieved before
considering the information reliable enough for the research?

Use only:

- low
- medium
- high


8. expected_goal_improvement

How much would successfully understanding this parameter
improve the achievement of the stated research goal?

Consider the actual research objective, not general usefulness.

Use only:

- low
- medium
- high


9. estimated_cost

How much system effort is expected to investigate this parameter?

Consider the likely reasoning, question generation, proposal
evaluation, and processing effort.

Use only:

- low
- medium
- high

A higher cost means more system effort.


10. estimated_consumer_burden

How much effort or inconvenience is the consumer likely to
experience when providing this information?

Consider:

- Difficulty recalling the information
- Sensitivity or personal nature of the topic
- Cognitive effort required
- Time and explanation required

Use only:

- low
- medium
- high

A higher burden means a more demanding experience for the consumer.

PARAMETER VOCABULARY

Every required_information.parameter MUST be exactly one
of the following values:

- Product awareness
- Product relationship
- Usage experience
- Overall sentiment
- Product perception
- Feature-specific sentiment
- Needs
- Pain points
- Unmet needs
- Expectations
- Aspirations
- Purchase intent
- Buying constraints
- Adoption barriers
- Competitor perception

Do not create alternative names, synonyms, paraphrases,
or new parameter names.

For example:

Correct:
"Product awareness"

Incorrect:
"Customer awareness of the product"
"Awareness of the product"
"Consumer familiarity"


TERMINATION CRITERIA

Define reasonable qualitative and operational termination criteria.

The criteria may consider:

- minimum_iterations
- maximum_iterations
- critical information sufficiently explored
- diminishing information gain
- repeated or redundant responses
- consumer disengagement
- no meaningful unresolved gaps

IMPORTANT RULES

- Do not use decimal scores or numerical confidence values.
- Do not assign artificial mathematical precision.
- Use qualitative categories wherever semantic judgment is required.
- Numerical values are allowed only for genuine operational controls,
  such as minimum_iterations and maximum_iterations.
- The meta-plan defines information priorities, not a sequence
  of questions.
- The interview system must remain adaptive.

Return only the structured output required by the schema.
"""

    try:
        result = cast(
            MetaPlanSchema,
            structured_llm.invoke(prompt)
        )
    except Exception as error:
        if is_rate_limit_error(error):
            logger.info(
                "[META PLANNER] Provider rate limit reached; "
                "using deterministic meta-plan fallback.",
            )
        else:
            # Local small models often fail schema/validation on complex
            # structured outputs. Fall back so the interview can start.
            logger.warning(
                "[META PLANNER] Structured meta-plan failed; using "
                "deterministic fallback.",
                extra={"parts": [f"{type(error).__name__}: {error}"]},
            )
        return {"meta_plan": build_rate_limit_meta_plan(state)}

    return {
        "meta_plan": result.model_dump()
    }
