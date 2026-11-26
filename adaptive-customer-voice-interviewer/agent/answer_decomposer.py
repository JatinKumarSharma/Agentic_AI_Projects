from dotenv import load_dotenv

from typing import cast

from agent.llm import (
    _llm_provider,
    get_structured_llm,
    invoke_with_rate_limit_retry,
)

from agent.state import InterviewState

from agent.provenance import attach_response_provenance

from agent.decomposition_schemas import AnswerDecompositionSchema

from langgraph.config import get_stream_writer
from agent.observability import get_logger

logger = get_logger(__name__)


load_dotenv()


# ============================================================
# ANSWER DECOMPOSITION
# ============================================================

def decompose_answer(state: InterviewState) -> dict:
    """
    Decomposes the latest consumer response into structured,
    atomic pieces of research evidence.

    This node does not update the knowledge state directly.

    Its responsibility is interpretation only:

    - Extract atomic evidence
    - Identify evidence category
    - Preserve consumer meaning
    - Distinguish explicit vs inferred evidence
    - Identify sentiment
    - Assess qualitative interpretation confidence
    - Detect ambiguity
    - Detect contradictions
    """

    latest_response = state.get(
        "latest_response"
    )

    if not latest_response:

        return {
            "latest_decomposition": None
        }

    research_context = state[
        "research_context"
    ]

    structured_llm = get_structured_llm(
        AnswerDecompositionSchema
    )

    fallback_structured_llm = None
    if _llm_provider() == "groq":
        fallback_structured_llm = get_structured_llm(
            AnswerDecompositionSchema,
            method="json_schema",
        )    prompt = f"""

You are an expert customer research interview analysis system.

Your task is to decompose a consumer's response into small,
atomic pieces of research evidence.

Your role is to INTERPRET what the consumer has communicated.

Do not evaluate whether the information is sufficient.
Do not decide what question should be asked next.
Do not update the interview state.

You only extract and structure evidence.

============================================================
RESEARCH CONTEXT
============================================================

Company:
{research_context["company_name"]}

Product:
{research_context["product_name"]}

Product Category:
{research_context["product_category"]}

Research Goal:
{research_context["research_goal"]}


============================================================
CONSUMER RESPONSE
============================================================

"{latest_response}"


============================================================
EVIDENCE EXTRACTION
============================================================

Extract only information that was:

1. Directly expressed by the consumer, or

2. Reasonably implied by the consumer's statement.

Do NOT invent:

- preferences
- experiences
- motivations
- needs
- pain points
- intentions
- constraints
- facts

that were not communicated by the consumer.


Possible evidence categories include:

- product_relationship
- product_awareness
- usage_experience
- overall_sentiment
- product_perception
- feature_sentiment
- needs
- pain_points
- unmet_needs
- expectations
- aspirations
- purchase_intent
- buying_constraints
- adoption_barriers
- competitor_information
- consumer_profile


============================================================
ATOMIC EVIDENCE RULES
============================================================

1. Break compound responses into separate atomic evidence items.

For example:

"I have used the iPhone for two years. I love the camera
but the battery frustrates me."

Should produce separate evidence for:

- product relationship
- usage duration
- positive camera sentiment
- negative battery experience

Do not merge unrelated information into one evidence item.


2. Preserve the consumer's meaning.

Do not unnecessarily rewrite or exaggerate statements.


3. Every meaningful factual, experiential, preference, sentiment,
or usage statement in the consumer response must be represented
by at least one evidence item.

Never return an empty evidence list when the consumer response
contains meaningful information.

For example:

Consumer response:
"I have used the product for two years. I love how easy it is to use."

Return two evidence items:

1. usage_experience
   parameter: usage_duration
   value: two years
   evidence_type: explicit
   confidence: high

2. feature_sentiment
   parameter: ease_of_use
   value: easy to use
   sentiment: positive
   evidence_type: explicit
   confidence: high

Do not create a separate overall_sentiment item when that would
duplicate sentiment already attached to a more specific evidence item.


============================================================
SENTIMENT RULES
============================================================

Assign sentiment only when sentiment is actually expressed
or strongly implied.

Allowed sentiment values:

- positive
- negative
- neutral
- mixed
- None

Do not force sentiment where none is present.


============================================================
INTERPRETATION CONFIDENCE
============================================================

Confidence represents confidence in correctly interpreting
the consumer's meaning.

Confidence does NOT represent whether the consumer's
statement is objectively true.

Use ONLY qualitative confidence levels:

- high
- medium
- low


Use HIGH confidence when:

- The consumer clearly and directly expresses the information.
- The meaning is specific and unambiguous.
- Little interpretation is required.

Example:

"I really like the iPhone camera."

→ confidence: high


Use MEDIUM confidence when:

- The meaning is reasonably clear but contextual.
- Some interpretation is required.
- The statement could have more than one nuance.

Example:

"I usually need to charge it before evening."

→ possible battery concern
→ confidence: medium


Use LOW confidence when:

- The evidence is weakly implied.
- Interpretation is uncertain.
- Important context is missing.

Do NOT use numerical confidence values.


============================================================
AMBIGUITY
============================================================

Set ambiguity_detected to True only when clarification
could materially improve understanding.

Do not mark normal conversational brevity as ambiguity.

Examples of meaningful ambiguity:

- "It's not that great anymore."
  → unclear what aspect is being discussed.

- "Sometimes I like it, sometimes I don't."
  → unclear what conditions cause the difference.


============================================================
CONTRADICTIONS
============================================================

Identify contradictions only when the CURRENT consumer
response contains genuinely conflicting information.

Do not invent contradictions.

Do not compare against previous responses here.

Cross-response contradiction analysis belongs to the
state and reflection stages.

============================================================
OUTPUT REQUIREMENTS
============================================================

Your output MUST contain ALL four top-level fields:

1. summary
2. evidence
3. ambiguity_detected
4. potential_contradictions

Rules:

- ambiguity_detected MUST always be a boolean:
  true or false.

- If no meaningful ambiguity exists, return false.

- potential_contradictions MUST always be a list.
  Return an empty list when no contradictions exist.

- Never omit any top-level field.
- Never return null for required fields.
- Return only the structured output.

============================================================
IMPORTANT CONSTRAINTS
============================================================

- The consumer is the sole source of research information.
- Do not use external product knowledge.
- Do not assume product features.
- Do not assume consumer motivations.
- Do not infer demographic information unnecessarily.
- Do not evaluate information sufficiency.
- Do not prioritize evidence.
- Do not generate interview questions.
- Do not assign numerical scores.
- Return only the structured output required by the schema.

"""

    try:
        result = cast(
            AnswerDecompositionSchema,
            invoke_with_rate_limit_retry(structured_llm, prompt)
        )

    except Exception as error:
        if fallback_structured_llm is None:
            raise

        status_code = getattr(error, "status_code", None)
        response = getattr(error, "response", None)
        response_status = getattr(response, "status_code", None)

        is_tool_use_failure = (
            status_code == 400
            or response_status == 400
            or "tool_use_failed" in str(error)
        )

        if not is_tool_use_failure:
            raise

        logger.warning(
            "answer_decomposer_structured_fallback",
            extra={
                "fallback_method": "json_schema",
                "error_type": type(error).__name__,
            },
        )

        result = cast(
            AnswerDecompositionSchema,
            invoke_with_rate_limit_retry(
                fallback_structured_llm,
                prompt
            )
        )

    logger.info("\n===== DECOMPOSER DEBUG =====")
    logger.info("debug_payload", extra={"parts": ["LATEST RESPONSE:", latest_response]})
    logger.info("debug_payload", extra={"parts": ["DECOMPOSER RESULT:", result.model_dump()]})
    logger.info("debug_payload", extra={"parts": ["EVIDENCE COUNT:", len(result.evidence)]})
    logger.info("============================\n")

    decomposition = attach_response_provenance(
        result.model_dump(),
        state,
    )

    # ---------------------------------------------------------
    # PUBLISH DECOMPOSITION-READY EVENT
    # ---------------------------------------------------------

    research_session = state.get("research_session")

    if research_session:
        session_id = research_session["session_id"]

        writer = get_stream_writer()

        writer({
            "event": "contextual_engagement_input",
            "session_id": session_id,
            "interview_id": session_id,
            "decomposition": decomposition,
        })

    return {
        "latest_decomposition": decomposition
    }
