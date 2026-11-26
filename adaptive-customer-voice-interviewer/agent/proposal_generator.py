import csv
import io
import re

import uuid
from typing import (
    Dict,
    Any,
    List,
    cast,
    Mapping,
)

from pydantic import BaseModel, Field

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage

from agent.llm import get_llm, invoke_output_limit
from agent.state import (
    InterviewState,
    Proposal,
    InformationGap,
    InformationRequirement,
)
from agent.observability import get_logger

logger = get_logger(__name__)



load_dotenv()


# ============================================================
# STRUCTURED BATCH QUESTION OUTPUT
# ============================================================

class TargetQuestionSet(BaseModel):
    """
    Candidate questions generated for one target in a batch.
    """

    target_index: int = Field(ge=0)
    questions: List[str] = Field(min_length=3, max_length=3)


class BatchQuestionGenerationSchema(BaseModel):
    """
    Structured output for generating questions for multiple
    targets in one LLM call.
    """

    target_question_sets: List[TargetQuestionSet]


# ============================================================
# GAP-SPECIFIC QUESTION INSTRUCTIONS
# ============================================================

GAP_TYPE_INSTRUCTIONS = {

    "missing": """
The information is completely missing.

Generate exploratory questions that naturally introduce
the topic without assuming the consumer has an opinion,
experience, ownership, or prior knowledge.
""",

    "insufficient": """
Some information already exists, but understanding is incomplete.

Generate follow-up questions that deepen understanding
without repeating information already collected.
""",

    "verification_required": """
Information exists but requires clarification or confirmation.

Generate neutral verification questions that confirm or
clarify previous consumer statements without sounding
repetitive or leading.
""",
}


# ============================================================
# CONTEXT COMPRESSION
# ============================================================

def recent_question_window(
    questions: List[str] | None,
    limit: int = 8,
) -> List[str]:
    """Keep only the recent question window to bound prompt growth."""

    if not questions or limit <= 0:
        return []

    cleaned = [
        str(question).strip()
        for question in questions
        if str(question).strip()
    ]
    return cleaned[-limit:]


def build_conversation_context_summary(
    state: InterviewState,
    recent_limit: int = 3,
) -> str:
    """Build a compact, deterministic conversation summary.

    This is prompt-context compression, not persistent memory replacement.
    It uses structured state already maintained by the graph/read model.
    """

    questions = [
        str(q).strip()
        for q in state.get("questions_asked", [])
        if str(q).strip()
    ]
    responses = [
        str(r).strip()
        for r in state.get("responses", [])
        if str(r).strip()
    ]

    unresolved_gaps = state.get("unresolved_gaps", []) or []
    gap_params = []
    for gap in unresolved_gaps:
        param = str(gap.get("parameter", "")).strip()
        if param and param not in gap_params:
            gap_params.append(param)

    lines = [
        f"Turns answered: {len(responses)}",
        f"Questions asked: {len(questions)}",
    ]

    if gap_params:
        lines.append(
            "Unresolved research gaps: "
            + ", ".join(gap_params[:6])
        )

    if responses:
        recent = responses[-max(1, recent_limit):]
        start = len(responses) - len(recent) + 1
        lines.append("Recent responses (compressed window):")
        for idx, text in enumerate(recent, start=start):
            compact = re.sub(r"\s+", " ", text).strip()
            if len(compact) > 180:
                compact = compact[:177].rstrip() + "..."
            lines.append(f"- R{idx}: {compact}")

    return "\n".join(lines)


# ============================================================
# CONSUMER KNOWLEDGE CONTEXT
# ============================================================

def build_consumer_context(
    state: InterviewState
) -> str:
    """
    Builds a concise representation of accumulated
    consumer knowledge for context-aware question generation.

    Only information already obtained from the consumer
    is included.
    """

    context_lines = []

    # --------------------------------------------------------
    # Single KnowledgeItems
    # --------------------------------------------------------

    knowledge_fields = {
        "Product relationship": state.get(
            "product_relationship"
        ),

        "Product awareness": state.get(
            "product_awareness"
        ),

        "Purchase intent": state.get(
            "purchase_intent"
        ),

        "Overall sentiment": state.get(
            "overall_sentiment"
        ),

        "Product perception": state.get(
            "product_perception"
        ),
    }

    for label, item in knowledge_fields.items():

        if not item:
            continue

        values = item.get(
            "values",
            []
        )

        if values:

            extracted_values = [
                str(value.get("value"))
                for value in values
            ]

            context_lines.append(
                f"{label}: "
                + "; ".join(extracted_values)
            )

    # --------------------------------------------------------
    # Dictionary-based KnowledgeItems
    # --------------------------------------------------------

    dictionary_fields = {
        "Feature sentiment": state.get(
            "feature_sentiment",
            {}
        ),

        "Features": state.get(
            "features",
            {}
        ),

        "Needs": state.get(
            "needs",
            {}
        ),

        "Pain points": state.get(
            "pain_points",
            {}
        ),

        "Expectations": state.get(
            "expectations",
            {}
        ),

        "Aspirations": state.get(
            "aspirations",
            {}
        ),

        "Buying constraints": state.get(
            "buying_constraints",
            {}
        ),

        "Adoption barriers": state.get(
            "adoption_barriers",
            {}
        ),

        "Competitor information": state.get(
            "competitor_information",
            {}
        ),
    }

    for label, items in dictionary_fields.items():

        if not items:
            continue

        item_lines = []

        for parameter, knowledge in items.items():

            values = knowledge.get(
                "values",
                []
            )

            extracted_values = [
                str(value.get("value"))
                for value in values
            ]

            if extracted_values:

                item_lines.append(
                    f"{parameter}: "
                    + "; ".join(extracted_values)
                )

        if item_lines:

            context_lines.append(
                f"{label}: "
                + " | ".join(item_lines)
            )

    knowledge_text = (
        "\n".join(context_lines)
        if context_lines
        else "No consumer-specific information has been collected yet."
    )

    conversation_summary = build_conversation_context_summary(state)
    return (
        f"{knowledge_text}\n\n"
        "CONVERSATION SUMMARY (COMPRESSED)\n"
        f"{conversation_summary}"
    )


# ============================================================
# PROMPT BUILDER
# ============================================================

def build_proposal_prompt(
    state: InterviewState,
    targets: List[Mapping[str, Any]],
    consumer_context: str | None = None,
) -> str:
    """
    Builds one batch prompt for generating candidate questions
    across multiple information targets in a single LLM call.

    Each target is kept independent so the generated questions
    remain attributable to exactly one information gap.
    """

    research_context = state.get(
        "research_context",
        {}
    )

    previous_questions = recent_question_window(
        state.get("questions_asked", []),
        limit=8,
    )

    audience = known_customer_context(state)

    if consumer_context is None:
        consumer_context = build_consumer_context(state)

    target_sections = []

    for index, target in enumerate(targets):

        gap_type = target.get(
            "gap_type",
            "missing"
        )

        instructions = GAP_TYPE_INSTRUCTIONS.get(
            gap_type,
            GAP_TYPE_INSTRUCTIONS["missing"]
        )

        target_sections.append(
            f"""
------------------------------------------------------------
TARGET {index}
------------------------------------------------------------

Parameter:
{target.get("parameter")}

Category:
{target.get("category")}

Gap Type:
{gap_type}

Criticality:
{target.get("criticality")}

Importance:
{target.get("importance")}

Goal Alignment:
{target.get("goal_alignment")}

Confidence:
{target.get("confidence")}

Verification Required:
{target.get("verification_required")}

Gap-Specific Instructions:
{instructions}
"""
        )

    return f"""
You are generating candidate questions for an adaptive
consumer research interview.

Generate candidate questions for ALL of the targets below
in ONE response. Each target is independent and must receive
exactly 3 candidate questions.

============================================================
RESEARCH CONTEXT
============================================================

Company:
{research_context.get("company_name")}

Product:
{research_context.get("product_name")}

Product Category:
{research_context.get("product_category")}

Research Goal:
{research_context.get("research_goal")}

Known Customer Segment:
{audience["segment"] or "Not specified"}

Target Audience:
{audience["audience"] or "Not specified"}

Interview Constraints:
{", ".join(str(item) for item in audience["constraints"]) or "None"}


============================================================
CURRENT CONSUMER KNOWLEDGE
============================================================

{consumer_context}


============================================================
IMPORTANT CONSUMER-CONTEXT RULES
============================================================

Treat the information above as established information
obtained from the consumer.

Do not invent consumer information.

Do not contradict known information.

Do not ask hypothetical ownership or usage questions when
the consumer has already explicitly stated that they own
or use the product.

When a customer segment or target audience is already known,
treat that relationship as established consumer context.
Do not ask questions that establish, classify, or verify the
relationship, awareness, ownership, or membership. For example,
never ask "What is your current relationship with the product,
if any?". Move directly to experience, goals, and pain points.

Use existing knowledge to generate follow-up questions
that deepen understanding.


============================================================
TARGETS
============================================================

{"".join(target_sections)}


============================================================
PREVIOUS QUESTIONS (RECENT WINDOW)
============================================================

{previous_questions}


============================================================
QUESTION GENERATION RULES
============================================================

1. Generate exactly 3 candidate questions for EACH target.

2. Keep each question tied to exactly one target.

3. Do not combine multiple unrelated targets in one question.

4. Questions must sound natural in a real conversation.

5. Do not repeat previous questions.

6. Do not ask leading questions.

7. Do not make unsupported assumptions about the consumer.

8. Prefer open-ended questions when appropriate.

9. Avoid asking for information already known.

10. Questions should be suitable for a spoken consumer
    interview.

11. Keep questions concise enough to be answered naturally.

12. Make questions for different targets meaningfully distinct.

13. Do not provide explanations, analysis, or commentary.


============================================================
OUTPUT FORMAT — STRICT CSV
============================================================

Return ONLY valid CSV.

The CSV must contain exactly these three columns:

target_index,question_number,question

Requirements:

1. Generate exactly {len(targets) * 3} data rows.

2. Generate exactly 3 rows for each target.

3. target_index must match the target number shown above,
   starting from 0.

4. question_number must be 1, 2, or 3 for each target.

5. Every question must occupy exactly one CSV record.

6. Enclose every question in double quotes.

7. Escape double quotes inside questions by doubling them.

8. Do not include Markdown code fences.

9. Do not include explanations or commentary.

10. Do not omit any target.

11. Do not generate additional targets.

12. Preserve the target ordering.

The first row must be the CSV header.

The remaining rows must contain the candidate questions.

Return CSV only.
"""


# ============================================================
# RESPONSE PARSER
# ============================================================

_BATCH_HEADER = [
    "target_index",
    "question_number",
    "question",
]

# Matches "0,1,<question>" (indices may optionally be quoted) and captures
# the free-form question text as the final group. Questions may themselves
# contain commas and quotes, so everything after the second comma is kept
# intact rather than handed to a strict CSV reader.
_BATCH_ROW_RE = re.compile(
    r'^\s*"?\s*(\d+)\s*"?\s*,\s*"?\s*(\d+)\s*"?\s*,(.*)$'
)


def _clean_question_text(raw: str) -> str:
    """Normalize one question cell from a local model's CSV output."""
    text = raw.strip()

    # A stray trailing comma (an extra empty column) is common in local
    # output such as: 0,1,"A question?",
    if text.endswith(","):
        text = text[:-1].rstrip()

    # Strip one surrounding layer of quotes, then unescape doubled quotes.
    # Inner, unescaped quotes are left as-is.
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    text = text.replace('""', '"')

    return text.strip()


def _extract_batch_rows(response: str) -> List[List[str]]:
    """Pull the header and data rows out of messy LLM output.

    Local models frequently wrap CSV in Markdown fences, add commentary
    before or after the table, and leave quotes unescaped. A line-oriented
    scan tolerates those cases where ``csv.reader(strict=True)`` does not.
    """
    rows: List[List[str]] = []
    header_seen = False

    for raw_line in response.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("```"):
            continue

        # Accept the header despite irregular spacing, casing, or quoting.
        normalized = ",".join(
            part.strip().strip('"').lower()
            for part in line.split(",")
        )
        if normalized == ",".join(_BATCH_HEADER):
            rows.append(list(_BATCH_HEADER))
            header_seen = True
            continue

        if not header_seen:
            continue

        match = _BATCH_ROW_RE.match(line)
        if not match:
            # Commentary or a post-table note; ignore it.
            continue

        rows.append([
            match.group(1),
            match.group(2),
            _clean_question_text(match.group(3)),
        ])

    return rows


def parse_batch_questions(
    response: str,
    target_count: int,
) -> List[List[str]]:

    if not response or not response.strip():
        raise ValueError("LLM returned empty CSV output.")

    try:
        rows = _extract_batch_rows(response)

        if not rows:
            raise ValueError("No CSV header or rows found in output.")

        expected_header = _BATCH_HEADER
        header, *data_rows = rows

        if header != expected_header:
            raise ValueError(
                f"Invalid CSV header: {header}"
            )

        raw_rows = []
        for row in data_rows:
            if len(row) != 3:
                raise ValueError(f"Invalid CSV row: {row}")
            raw_rows.append((
                int(row[0].strip()),
                int(row[1].strip()),
                row[2].strip(),
            ))

        # Some local models emit 1-based target indices. Accept them only
        # when 0 is absent and every index fits 1..target_count.
        indices = [r[0] for r in raw_rows]
        index_offset = (
            1
            if indices
            and 0 not in indices
            and all(1 <= i <= target_count for i in indices)
            else 0
        )

        questions_by_target = [
            {} for _ in range(target_count)
        ]

        for raw_index, question_number, question in raw_rows:

            target_index = raw_index - index_offset

            # Local models sometimes hallucinate extra targets. Ignore
            # rows outside the requested range as long as the requested
            # targets are complete.
            if not 0 <= target_index < target_count:
                continue

            if question_number not in (1, 2, 3):
                raise ValueError(
                    f"Invalid question number: {question_number}"
                )

            if not question:
                raise ValueError("Empty question")

            if question_number in questions_by_target[target_index]:
                raise ValueError(
                    f"Duplicate question number: "
                    f"target={target_index}, "
                    f"question={question_number}"
                )

            questions_by_target[target_index][
                question_number
            ] = question

        result = []

        for target_index, question_set in enumerate(
            questions_by_target
        ):

            if set(question_set.keys()) != {1, 2, 3}:
                raise ValueError(
                    f"Incomplete question set for target "
                    f"{target_index}"
                )

            result.append([
                question_set[1],
                question_set[2],
                question_set[3],
            ])

        return result

    except (ValueError, IndexError) as exc:
        raise ValueError(
            f"Failed to parse or validate proposal CSV: {exc}"
        ) from exc


# ============================================================
# PROPOSAL CREATION
# ============================================================

def create_proposals(
    questions: List[str],
    gap: Mapping[str, Any],
) -> List[Proposal]:
    """
    Convert generated candidate questions into Proposal objects.

    This function only creates proposal records.
    It does not evaluate, rank, select, or execute them.
    """

    proposals: List[Proposal] = []

    for question in questions:

        proposal: Proposal = {
            "proposal_id": str(uuid.uuid4()),
            "target_gap": str(gap.get("parameter", "")),
            "question": question,
            "expected_information_gain": "low",
            "expected_state_improvement": "low",
            "importance": gap.get("importance", "medium"),
            "goal_alignment": gap.get("goal_alignment", "medium"),
            "criticality": gap.get("criticality", "medium"),
            "risk": "low",
            "cost": "low",
            "consumer_burden": "low",
        }

        proposals.append(proposal)

    return proposals


# ============================================================
# QUESTION GENERATION
# ============================================================

def generate_questions_for_targets(
    state: InterviewState,
    targets: List[Mapping[str, Any]],
) -> List[List[str]]:
    """
    Generate exactly three candidate questions per target
    in one LLM call using CSV output.

    Python validates and converts the CSV into a list
    of question lists aligned with the original targets.
    """

    if not targets:
        return []

    llm = get_llm(purpose="proposal")

    consumer_context = build_consumer_context(state)

    prompt = build_proposal_prompt(
        state=state,
        targets=targets,
        consumer_context=consumer_context,
    )

    try:
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "You are a consumer research "
                        "interview question generator. "
                        "Follow the requested CSV format exactly. "
                        "Return CSV only."
                    )
                ),
                HumanMessage(content=prompt),
            ],
            # Keep each provider request below free-tier output-token limits.
            **invoke_output_limit(900),
        )
    except Exception as error:
        if not is_rate_limit_error(error):
            raise

        logger.info(
            "[PROPOSAL GENERATION] Provider rate limit reached; "
            "using deterministic question fallback.",
        )
        return [
            build_neutral_question_fallbacks(
                str(target.get("parameter", ""))
            )
            for target in targets
        ]

    if not isinstance(response.content, str):
        raise ValueError(
            "LLM returned non-text content instead of CSV."
        )

    try:
        questions_by_target = parse_batch_questions(
            response=response.content,
            target_count=len(targets),
        )
    except ValueError as error:
        # Local small models often violate CSV shape/count contracts.
        # Fall back deterministically so the interview can still start.
        logger.warning(
            "[PROPOSAL GENERATION] Malformed local-model CSV; "
            "using deterministic question fallback.",
            extra={"parts": [str(error)]},
        )
        questions_by_target = [
            build_neutral_question_fallbacks(
                str(target.get("parameter", ""))
            )
            for target in targets
        ]

    audience = known_customer_context(state)

    return [
        sanitize_established_context_questions(
            questions,
            known=audience["known"],
            parameter=str(target.get("parameter", "")),
        )
        for questions, target in zip(questions_by_target, targets)
    ]

# ============================================================
# TARGET PRIORITIZATION AND SELECTION
# ============================================================

LEVEL_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


def get_level_rank(
    value: Any,
    default: str = "medium",
) -> int:
    """
    Converts a qualitative category into an internal ordering.

    These values are used only for deterministic comparisons.
    They are not research scores or model-generated measurements.
    """

    return LEVEL_RANK.get(
        str(value).lower(),
        LEVEL_RANK[default],
    )


def rank_information_targets(
    targets: List[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    """
    Ranks information targets by research priority.

    Priority order:
    1. Criticality
    2. Importance
    3. Goal alignment
    """

    return sorted(
        targets,
        key=lambda target: (
            get_level_rank(
                target.get("criticality")
            ),
            get_level_rank(
                target.get("importance")
            ),
            get_level_rank(
                target.get("goal_alignment")
            ),
        ),
        reverse=True,
    )


UNSPECIFIED_AUDIENCE_VALUES = {
    "",
    "all",
    "any",
    "general",
    "none",
    "unspecified",
}

ESTABLISHED_RELATIONSHIP_PARAMETERS = {
    "Product awareness",
    "Product relationship",
}


def known_customer_context(state: InterviewState) -> dict[str, Any]:
    """Return the researcher-established audience facts for a session."""

    context = state.get("research_context") or {}
    session = state.get("research_session") or {}

    segment = str(
        context.get("customer_segment")
        or session.get("customer_segment")
        or ""
    ).strip()
    audience = str(
        context.get("target_audience")
        or session.get("target_audience")
        or ""
    ).strip()
    constraints = (
        context.get("interview_constraints")
        or session.get("interview_constraints")
        or []
    )

    return {
        "segment": segment,
        "audience": audience,
        "constraints": constraints,
        "known": (
            segment.lower() not in UNSPECIFIED_AUDIENCE_VALUES
            or audience.lower() not in UNSPECIFIED_AUDIENCE_VALUES
        ),
    }


def filter_established_context_targets(
    state: InterviewState,
    targets: List[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    """Remove redundant relationship targets for a known customer segment."""

    if not known_customer_context(state)["known"]:
        return targets

    return [
        target
        for target in targets
        if target.get("parameter")
        not in ESTABLISHED_RELATIONSHIP_PARAMETERS
    ]


ESTABLISHED_CONTEXT_QUESTION_PATTERNS = (
    re.compile(r"\bcurrent relationship\b", re.IGNORECASE),
    re.compile(r"\brelationship (?:with|to)\b", re.IGNORECASE),
    re.compile(r"\b(?:first )?came across\b", re.IGNORECASE),
    re.compile(r"\bhow (?:did|do) you (?:first )?hear\b", re.IGNORECASE),
    re.compile(r"\bwhere (?:did|do) you (?:first )?hear\b", re.IGNORECASE),
    re.compile(r"\b(?:first )?(?:hear|heard) about\b", re.IGNORECASE),
    re.compile(r"\b(?:first )?(?:learn|learned) about\b", re.IGNORECASE),
    re.compile(r"\bfound out about\b", re.IGNORECASE),
    re.compile(r"\baware(?:ness)? of\b", re.IGNORECASE),
    re.compile(
        r"\bwhen did you (?:first )?(?:start|begin) using\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhow long have you (?:been )?using\b",
        re.IGNORECASE,
    ),
)


def is_established_context_question(question: Any) -> bool:
    """Detect questions that re-establish a known customer relationship."""

    text = str(question or "")
    return any(
        pattern.search(text)
        for pattern in ESTABLISHED_CONTEXT_QUESTION_PATTERNS
    )


def build_neutral_question_fallbacks(parameter: str) -> List[str]:
    """Return three model-free questions tied to one research target."""

    subject = str(parameter or "this product").strip().lower()

    return [
        f"Thinking about your experience, which part of {subject} "
        "has been most useful?",
        f"What has been most difficult about {subject}?",
        f"If you could change one thing about {subject}, "
        "what would it be?",
    ]


def sanitize_established_context_questions(
    questions: List[str],
    *,
    known: bool,
    parameter: str,
) -> List[str]:
    """Replace relationship/awareness questions for known segments."""

    if not known:
        return list(questions)

    fallbacks = build_neutral_question_fallbacks(parameter)

    return [
        fallbacks[index % len(fallbacks)]
        if is_established_context_question(question)
        else question
        for index, question in enumerate(questions)
    ]


def is_rate_limit_error(error: Exception) -> bool:
    """Recognize provider rate-limit errors without importing an SDK."""

    if type(error).__name__ == "RateLimitError":
        return True

    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == 429


def select_information_targets(
    targets: List[Mapping[str, Any]],
    max_priority_targets: int = 3,
    selected_target_count: int = 2,
) -> List[Mapping[str, Any]]:
    """
    Selects research targets in two stages.

    Stage 1:
        Retain the three highest-priority targets.

    Stage 2:
        Select two using qualitative value-efficiency criteria.

    Criticality is considered first to protect essential
    research requirements.

    Remaining selection criteria:
        1. Expected goal improvement: high first.
        2. Estimated system cost: low first.
        3. Estimated consumer burden: low first.
        4. Importance and goal alignment.

    Missing estimates default to medium for compatibility
    with older test fixtures.
    """

    if not targets:
        return []

    priority_targets = rank_information_targets(
        targets
    )[:max_priority_targets]

    selected_targets = sorted(
        priority_targets,
        key=lambda target: (
            get_level_rank(
                target.get("criticality")
            ),
            get_level_rank(
                target.get("expected_goal_improvement")
            ),
            -get_level_rank(
                target.get("estimated_cost")
            ),
            -get_level_rank(
                target.get("estimated_consumer_burden")
            ),
            get_level_rank(
                target.get("importance")
            ),
            get_level_rank(
                target.get("goal_alignment")
            ),
        ),
        reverse=True,
    )

    return selected_targets[:selected_target_count]


# ============================================================
# MAIN PROPOSAL GENERATOR
# ============================================================

def generate_proposals(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Generate candidate interview questions.

    Initial turn:
        Uses the Meta Plan directly.

    Subsequent turns:
        Uses unresolved information gaps produced
        by the Gap Analyzer.
    """

    iteration = state.get(
        "iteration_count",
        0,
    )

    if iteration == 0:
        return generate_initial_proposals(
            state
        )

    return generate_adaptive_proposals(
        state
    )


# ============================================================
# INITIAL PROPOSALS
# ============================================================


def generate_initial_proposals(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Generate initial interview questions from the Meta Plan.

    All requirements are considered for prioritization.
    The top three are shortlisted, then two are selected
    using qualitative value-efficiency criteria.

    Gap Analyzer is intentionally not used here because
    no consumer evidence exists yet.
    """

    meta_plan = state.get("meta_plan")

    if not meta_plan:
        return {"proposals": []}

    requirements: List[InformationRequirement] = (
        meta_plan.get("required_information", [])
    )

    targets: List[Mapping[str, Any]] = []

    for requirement in requirements:
        parameter = requirement.get("parameter", "")

        targets.append({
            "parameter": parameter,
            "category": parameter,

            # Initial information is not yet known.
            "gap_type": "missing",

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

            "confidence": "low",

            "verification_required": requirement.get(
                "verification_requirement",
                False,
            ),
        })

    targets = filter_established_context_targets(state, targets)
    targets = select_information_targets(targets)

    if not targets:
        return {"proposals": []}

    question_sets = generate_questions_for_targets(
        state=state,
        targets=targets,
    )

    all_proposals: List[Proposal] = []

    for target, questions in zip(targets, question_sets):
        all_proposals.extend(
            create_proposals(
                questions=questions,
                gap=target,
            )
        )

    return {
        "proposals": all_proposals
    }


# ============================================================
# ADAPTIVE PROPOSALS
# ============================================================


def generate_adaptive_proposals(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Generate candidate questions for unresolved information
    gaps identified by Gap Analyzer.

    All unresolved gaps are considered.
    The top three are shortlisted by research priority,
    then two are selected using qualitative
    value-efficiency criteria.

    This path is used from the second interview turn onward.
    """

    unresolved_gaps: List[InformationGap] = state.get(
        "unresolved_gaps",
        [],
    )

    if not unresolved_gaps:
        return {
            "proposals": []
        }

    targets: List[Mapping[str, Any]] = (
        select_information_targets(
            filter_established_context_targets(
                state,
                list(unresolved_gaps),
            )
        )
    )

    if not targets:
        return {
            "proposals": []
        }

    question_sets = generate_questions_for_targets(
        state=state,
        targets=targets,
    )

    all_proposals: List[Proposal] = []

    for target, questions in zip(targets, question_sets):
        all_proposals.extend(
            create_proposals(
                questions=questions,
                gap=target,
            )
        )

    return {
        "proposals": all_proposals
    }

def test_csv_question_generation(
    state: InterviewState,
) -> List[List[str]]:
    """
    Test CSV-based LLM question generation independently
    using the actual interview state and unresolved gaps.
    """

    unresolved_gaps = state.get("unresolved_gaps", [])

    if not unresolved_gaps:
        raise ValueError("No unresolved gaps available.")

    targets = select_information_targets(
        list(unresolved_gaps)
    )

    logger.info("\n========== CSV GENERATION TEST ==========")

    logger.info("debug_payload", extra={"parts": ["Targets:", len(targets)]})
    question_sets = generate_questions_for_targets(
        state=state,
        targets=targets,
    )

    logger.info("\nGenerated question sets:")

    for index, questions in enumerate(question_sets):
        logger.info(f"\nTarget {index}:")
        for number, question in enumerate(questions, 1):
            logger.info(f"{number}. {question}")

    assert len(question_sets) == len(targets)

    assert all(
        len(questions) == 3
        for questions in question_sets
    )

    logger.info("\nCSV GENERATION TEST PASSED")

    logger.info("debug_payload", extra={"parts": ["Total targets:", len(targets)]})
    logger.info("debug_payload", extra={"parts": ["Total questions:", sum(map(len, question_sets))]})
    logger.info("========================================\n")

    return question_sets