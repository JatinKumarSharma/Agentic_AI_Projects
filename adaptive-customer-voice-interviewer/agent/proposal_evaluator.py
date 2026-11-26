import csv
import io
import time
from time import perf_counter
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

from agent import state
from agent.llm import get_evaluator_llm
from agent.state import InterviewState, Proposal
from agent.proposal_generator import (
    build_consumer_context,
    known_customer_context,
)
from agent.observability import get_logger

logger = get_logger(__name__)



def build_evaluator_consumer_context(
    state: InterviewState,
) -> str:
    """
    Build consumer context for proposal evaluation.

    Preserves the existing evidence context and adds:
    1. KnowledgeItem-level quality metadata.
    2. EvidenceItem-level metadata for every evidence item.
    """

    consumer_context = build_consumer_context(state)

    knowledge_item_fields = {
        "values",
        "sufficiency",
        "confidence",
        "criticality",
        "verification_status",
        "applicability_status",
    }

    def is_knowledge_item(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and knowledge_item_fields.issubset(value.keys())
        )

    metadata_lines = []

    for state_field, field_value in state.items():

        knowledge_items = []

        # Case 1: A single KnowledgeItem.
        if is_knowledge_item(field_value):
            knowledge_items.append(
                (state_field, field_value)
            )

        # Case 2: Multiple KnowledgeItems indexed by parameter.
        elif isinstance(field_value, dict):
            for parameter, item in field_value.items():
                if is_knowledge_item(item):
                    knowledge_items.append(
                        (f"{state_field}.{parameter}", item)
                    )

        for parameter_path, item in knowledge_items:

            metadata_lines.append(
                f"\nResearch parameter: {parameter_path}"
            )

            metadata_lines.append(
                "KnowledgeItem metadata:"
            )

            metadata_lines.append(
                f"- Sufficiency: {item['sufficiency']}"
            )
            metadata_lines.append(
                f"- Confidence: {item['confidence']}"
            )
            metadata_lines.append(
                f"- Criticality: {item['criticality']}"
            )
            metadata_lines.append(
                f"- Verification status: "
                f"{item['verification_status']}"
            )
            metadata_lines.append(
                f"- Applicability status: "
                f"{item['applicability_status']}"
            )

            metadata_lines.append("Evidence-level metadata:")

            evidence_items = item.get("values", [])

            if not evidence_items:
                metadata_lines.append(
                    "- No evidence items recorded."
                )
                continue

            for index, evidence in enumerate(
                evidence_items, start=1
            ):
                metadata_lines.append(
                    f"- Evidence item {index}:"
                )
                metadata_lines.append(
                    f"  - Value: {evidence.get('value')}"
                )
                metadata_lines.append(
                    f"  - Source: {evidence.get('source')}"
                )
                metadata_lines.append(
                    f"  - Confidence: "
                    f"{evidence.get('confidence')}"
                )
                metadata_lines.append(
                    f"  - Evidence type: "
                    f"{evidence.get('evidence_type')}"
                )
                metadata_lines.append(
                    f"  - Sentiment: "
                    f"{evidence.get('sentiment')}"
                )

    if not metadata_lines:
        return consumer_context

    return (
        consumer_context
        + "\n\nKNOWLEDGE QUALITY METADATA\n"
        + "\n".join(metadata_lines)
    )

# ============================================================
# LLM INITIALIZATION
# ============================================================

llm = get_evaluator_llm()


def _is_rate_limit_error(error: Exception) -> bool:
    """Recognize provider rate-limit errors without importing an SDK."""

    if type(error).__name__ == "RateLimitError":
        return True

    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == 429


def invoke_with_rate_limit_retry(
    model,
    prompt,
    max_attempts=2,
    initial_delay=0.5,
    max_delay=1.0,
):
    """
    Invoke the LLM with bounded retries for provider rate limits.

    Uses Retry-After when supplied and fits inside ``max_delay``;
    otherwise it falls back to capped exponential backoff. Provider
    daily-quota headers can request waits of tens of minutes, which
    must never block a live interview, so every delay is capped. The
    defaults deliberately keep the first-question path near its prior
    6-8s range instead of spending 15s+ sleeping on a rate limit.
    """

    for attempt in range(max_attempts):
        try:
            return model.invoke(prompt)

        except Exception as error:
            if not _is_rate_limit_error(error):
                raise

            if attempt == max_attempts - 1:
                raise

            retry_after = None

            response = getattr(error, "response", None)

            if response is not None:
                retry_after = response.headers.get("retry-after")

            fallback_delay = min(
                initial_delay * (2 ** attempt),
                max_delay,
            )

            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = fallback_delay

            # A daily-quota Retry-After can be tens of minutes. Sleeping
            # for that duration inside a live session request hangs the
            # participant, so cap it and fail fast after the last attempt.
            delay = min(delay, max_delay)
            if delay < 0:
                delay = fallback_delay

            logger.info(
                f"[RATE LIMIT] Attempt {attempt + 1}/"
                f"{max_attempts} failed. Retrying in "
                f"{delay:.2f}s.",
            )

            time.sleep(delay)

# ============================================================
# PROMPT BUILDER
# ============================================================



def build_evaluation_prompt(
    proposals: List[Proposal],
    state: InterviewState,
    consumer_context: str | None = None,
) -> str:
    """
    Builds one prompt to independently evaluate multiple
    candidate interview questions.

    Proposals are identified using positional indices.
    The LLM must never generate or reproduce proposal IDs.
    """

    research_context = state.get("research_context", {})
    audience = known_customer_context(state)

    if consumer_context is None:
        consumer_context = build_evaluator_consumer_context(state)

    proposal_sections = []

    for index, proposal in enumerate(proposals):

        proposal_sections.append(
            f"""
------------------------------------------------------------
PROPOSAL INDEX: {index}
------------------------------------------------------------

Target Information Gap:
{proposal.get("target_gap")}

Importance:
{proposal.get("importance")}

Goal Alignment:
{proposal.get("goal_alignment")}

Criticality:
{proposal.get("criticality")}

Candidate Question:
{proposal.get("question")}
"""
        )

    return f"""
You are evaluating candidate questions generated by an
adaptive consumer research interview system.

Evaluate ALL provided proposals in ONE response.

IMPORTANT:
Each proposal must be evaluated independently.
Do not compare proposals or select the best proposal.
Do not allow one proposal's question to influence the
evaluation of another proposal.

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

When the audience is already known, do not reward questions that
establish or classify the customer relationship or awareness.

============================================================
CURRENT CONSUMER KNOWLEDGE
============================================================

{consumer_context}

============================================================
PROPOSALS TO EVALUATE
============================================================

{"".join(proposal_sections)}

============================================================
EVALUATION CRITERIA
============================================================

Evaluate every proposal using ONLY these values:

- low
- medium
- high

1. EXPECTED INFORMATION GAIN

How much useful NEW information is likely to be obtained
if the consumer answers this question?

low:
Unlikely to provide meaningful new information.

medium:
Likely to provide some useful information.

high:
Likely to provide substantial useful information.

2. EXPECTED STATE IMPROVEMENT

How much is this question expected to improve the
current knowledge state and reduce the target gap?

Consider:
- missing information
- insufficient information
- verification needs
- confidence improvement

low:
Unlikely to meaningfully improve the state.

medium:
Likely to partially improve the state.

high:
Likely to substantially improve the state.

3. RISK

Evaluate the risk associated with asking this question.

Consider:
- leading language
- unsupported assumptions
- repetition
- ambiguity
- contradiction with known consumer information
- overly sensitive questioning

low:
Minimal risk.

medium:
Some potential risk.

high:
Significant risk.

4. COST

Evaluate the conversational cost of asking this question.

Consider:
- complexity
- length
- cognitive effort
- explanation required

low:
Easy and simple to answer.

medium:
Requires moderate thought or explanation.

high:
Requires significant effort or explanation.

5. CONSUMER BURDEN

How demanding is this question for the consumer?

Consider:
- recall effort
- emotional effort
- response complexity
- time required

low:
Minimal effort required.

medium:
Moderate effort required.

high:
Significant effort required.

KNOWLEDGE QUALITY INTERPRETATION

Use the knowledge-quality metadata together with the actual evidence
when evaluating each proposal.

1. SUFFICIENCY
- missing: No usable information is currently available.
- basic: Some information exists, but important details may remain
  unexplored.
- adequate: The information is sufficient for the current research
  requirement, although additional detail may be useful.
- comprehensive: The current research requirement is substantially
  covered. Avoid treating additional detail as automatically necessary.

2. CONFIDENCE
- low: The stored knowledge has limited reliability.
- medium: The stored knowledge has moderate reliability.
- high: The stored knowledge has strong reliability.

Lower confidence can increase the potential value of clarification
or verification, but does not automatically imply greater new
information gain.

3. VERIFICATION STATUS
- unverified: The stored information has not been independently
  confirmed or explicitly validated.
- partially_verified: Some aspects have been confirmed, while
  others remain uncertain.
- verified: The information has been sufficiently confirmed.

A question that verifies existing information may improve state
quality without producing substantial new information.

4. APPLICABILITY STATUS
- applicable: The knowledge is relevant to the current research
  requirement.
- not_applicable: The requirement does not apply to this consumer
  or research context. Do not recommend questions merely to fill
  an inapplicable requirement.
- unknown: Applicability has not yet been established. Do not
  automatically interpret unknown as not_applicable.

SCORING DISTINCTION

Evaluate expected_information_gain and expected_state_improvement
independently.

Do not assume that:
- High information gain necessarily means high state improvement.
- Low information gain necessarily means low state improvement.
- Comprehensive knowledge must always be verified.
- High confidence necessarily means verified.
- Unverified knowledge necessarily requires another question.

Base each evaluation on the proposal's target gap, existing evidence,
knowledge-quality metadata, and the likely response to the question.

METADATA AND EVIDENCE CONSISTENCY

Knowledge-quality metadata is an assessment of the stored evidence,
not a replacement for the evidence itself.

Always inspect the actual consumer evidence.

If metadata and evidence appear inconsistent:
- Do not ignore the actual evidence.
- Do not assume that a metadata label guarantees information quality.
- Evaluate the proposal using the available evidence and its limitations.
- Avoid inventing missing information.

If applicability is unknown, determine whether the proposal
could establish applicability itself.

Do not treat unknown requirements as irrelevant.
Do not treat not_applicable requirements as unresolved gaps.

============================================================
IMPORTANT RULES
============================================================

- Return exactly one evaluation for every proposal.
- Preserve the exact proposal index provided.
- Never omit or duplicate a proposal index.
- Evaluate each proposal independently.
- Evaluate relative to its specific target information gap.
- Do not select or rank proposals.
- Do not rewrite candidate questions.
- Do not invent consumer information.
- Use existing consumer knowledge as context.
- Do not use numerical scores.
- Use only low, medium, or high for each criterion.
- Do not provide explanations or commentary.

============================================================
OUTPUT FORMAT — STRICT CSV
============================================================

Return ONLY valid CSV.

The CSV must contain exactly these six columns:

proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden

Requirements:

1. Generate exactly {len(proposals)} data rows.
2. Generate exactly one row per proposal.
3. proposal_index must match the index shown above,
   starting from 0.
4. Every evaluation field must contain low, medium, or high.
5. Do not include proposal IDs.
6. Do not include additional columns.
7. Do not include Markdown code fences.
8. Do not include explanations or commentary.
9. Do not omit or add proposals.
10. Preserve the original proposal ordering.

The first row must be the CSV header.
The remaining rows must contain the evaluations.

Return CSV only.
"""


_EVAL_HEADER = [
    "proposal_index",
    "expected_information_gain",
    "expected_state_improvement",
    "risk",
    "cost",
    "consumer_burden",
]
_EVAL_LEVELS = {"low", "medium", "high"}


def _extract_eval_rows(response: str) -> List[List[str]]:
    """Tolerant line-oriented extraction for local-model evaluation CSV."""
    rows: List[List[str]] = []
    header_seen = False

    for raw_line in response.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue

        normalized = ",".join(
            part.strip().strip('"').lower()
            for part in line.split(",")
        )
        if normalized == ",".join(_EVAL_HEADER):
            rows.append(list(_EVAL_HEADER))
            header_seen = True
            continue

        if not header_seen:
            continue

        # Try strict CSV first for well-formed quoted rows, then fall back
        # to a simple split for unquoted local-model output.
        try:
            parsed = next(csv.reader(io.StringIO(line)))
        except csv.Error:
            parsed = [part.strip() for part in line.split(",")]

        if len(parsed) == len(_EVAL_HEADER):
            rows.append([part.strip().strip('"') for part in parsed])

    return rows


def parse_evaluation_csv(
    response: str,
    proposal_count: int,
    require_complete: bool = True,
) -> Dict[int, Dict[str, str]]:
    """
    Parse and validate CSV evaluation output.

    Proposal IDs are deliberately excluded.
    Python maps indices to original proposal IDs later.
    """

    if not response or not response.strip():
        raise ValueError("LLM returned empty evaluation CSV.")

    if proposal_count < 1:
        raise ValueError("Proposal count must be at least 1.")

    # Remove a possible UTF-8 BOM without altering CSV content.
    response = response.lstrip("\ufeff").strip()

    try:
        rows = _extract_eval_rows(response)
        if not rows:
            raise ValueError("No CSV header or rows found in output.")

        header, *data_rows = rows
        if header != _EVAL_HEADER:
            raise ValueError(f"Invalid CSV header: {header}")

        evaluations = {}

        for row in data_rows:

            if len(row) != len(_EVAL_HEADER):
                raise ValueError(f"Invalid CSV row: {row}")

            index_text = row[0].strip()
            if not index_text.isdigit():
                raise ValueError(f"Invalid proposal index: {index_text}")

            proposal_index = int(index_text)
            if not 0 <= proposal_index < proposal_count:
                # Local models may hallucinate extra rows; ignore them
                # when the requested proposals can still be recovered.
                continue

            if proposal_index in evaluations:
                raise ValueError(
                    f"Duplicate evaluation for proposal "
                    f"index: {proposal_index}"
                )

            evaluation = {}
            for field_name, raw_value in zip(_EVAL_HEADER[1:], row[1:]):
                value = raw_value.strip().lower()
                if value not in _EVAL_LEVELS:
                    raise ValueError(
                        f"Invalid value '{raw_value.strip()}' for "
                        f"'{field_name}' at proposal "
                        f"index {proposal_index}."
                    )
                evaluation[field_name] = value

            evaluations[proposal_index] = evaluation

        expected_indices = set(range(proposal_count))
        missing_indices = expected_indices - set(evaluations.keys())

        if require_complete and missing_indices:
            raise ValueError(
                f"Missing evaluations for proposal indices: "
                f"{sorted(missing_indices)}"
            )

        return evaluations

    except (csv.Error, StopIteration, ValueError) as exc:
        raise ValueError(
            f"Failed to parse or validate evaluation CSV: {exc}"
        ) from exc


# ============================================================
# MULTIPLE PROPOSAL EVALUATION
# ============================================================

def evaluate_proposal_group(
    target_gap: str,
    proposals: List[Proposal],
    state: InterviewState,
    consumer_context: str,
) -> Dict[str, Dict[str, str]]:
    """
    Evaluate one target-gap group and recover any missing evaluations.

    Returns evaluations keyed by the original proposal UUID.
    """

    group_started = perf_counter()

    logger.info(
        f"[PROPOSAL EVALUATION] Gap: {target_gap} | "
        f"Proposals: {len(proposals)}",
    )

    # --------------------------------------------------------
    # PRIMARY EVALUATION
    # --------------------------------------------------------

    prompt = build_evaluation_prompt(
        proposals=proposals,
        state=state,
        consumer_context=consumer_context,
    )

    llm_started = perf_counter()
    response = invoke_with_rate_limit_retry(llm, prompt)

    logger.info(
        f"[EVALUATOR TIMING] Gap '{target_gap}' primary: "
        f"{perf_counter() - llm_started:.2f}s",
    )

    if not isinstance(response.content, str):
        raise ValueError(
            f"LLM returned non-text content for target gap: {target_gap}"
        )

    if not response.content.strip():
        logger.info(
            f"[PROPOSAL EVALUATION] Gap '{target_gap}': "
            "primary response was empty; recovering all proposals.",
        )
        primary_evaluations = {}
    else:
        primary_evaluations = parse_evaluation_csv(
            response=response.content,
            proposal_count=len(proposals),
            require_complete=False,
        )

    # Map group-local indices to original proposal UUIDs.
    evaluations_by_id = {
        proposals[index]["proposal_id"]: evaluation
        for index, evaluation in primary_evaluations.items()
    }

    # --------------------------------------------------------
    # RECOVERY EVALUATION
    # --------------------------------------------------------

    missing_proposals = [
        proposal
        for proposal in proposals
        if proposal["proposal_id"] not in evaluations_by_id
    ]

    if missing_proposals:
        logger.info(
            f"[PROPOSAL EVALUATION] Gap '{target_gap}': "
            f"recovering {len(missing_proposals)} missing evaluations.",
        )

        retry_prompt = build_evaluation_prompt(
            proposals=missing_proposals,
            state=state,
            consumer_context=consumer_context,
        )

        retry_started = perf_counter()
        retry_response = invoke_with_rate_limit_retry(
            llm,
            retry_prompt,
        )

        logger.info(
            f"[EVALUATOR TIMING] Gap '{target_gap}' recovery: "
            f"{perf_counter() - retry_started:.2f}s",
        )

        if not isinstance(retry_response.content, str):
            raise ValueError(
                f"Recovery LLM returned non-text content for gap: {target_gap}"
            )

        retry_evaluations = parse_evaluation_csv(
            response=retry_response.content,
            proposal_count=len(missing_proposals),
            require_complete=True,
        )

        for retry_index, evaluation in retry_evaluations.items():
            proposal_id = missing_proposals[retry_index]["proposal_id"]

            if proposal_id in evaluations_by_id:
                raise ValueError(
                    f"Duplicate evaluation during recovery: {proposal_id}"
                )

            evaluations_by_id[proposal_id] = evaluation

    # --------------------------------------------------------
    # GROUP COMPLETENESS VALIDATION
    # --------------------------------------------------------

    expected_ids = {
        proposal["proposal_id"]
        for proposal in proposals
    }

    returned_ids = set(evaluations_by_id)

    missing_ids = expected_ids - returned_ids
    unexpected_ids = returned_ids - expected_ids

    if missing_ids:
        raise ValueError(
            f"Evaluation incomplete for target gap '{target_gap}'. "
            f"Missing IDs: {missing_ids}"
        )

    if unexpected_ids:
        raise ValueError(
            f"Unexpected proposal IDs for target gap '{target_gap}': "
            f"{unexpected_ids}"
        )

    logger.info(
        f"[EVALUATOR TIMING] Gap '{target_gap}' total: "
        f"{perf_counter() - group_started:.2f}s",
    )

    return evaluations_by_id


def build_rate_limit_fallback_evaluations(
    proposals: List[Proposal],
) -> Dict[str, Dict[str, str]]:
    """Return deterministic evaluations when the provider is rate limited."""

    evaluation = {
        "expected_information_gain": "medium",
        "expected_state_improvement": "medium",
        "risk": "medium",
        "cost": "medium",
        "consumer_burden": "medium",
    }

    return {
        proposal["proposal_id"]: dict(evaluation)
        for proposal in proposals
    }


def evaluate_proposals(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Evaluate proposals independently within target-gap groups.

    Primary evaluation:
        One LLM call per target-gap group, with up to three groups
        evaluated concurrently.

    Recovery:
        An additional LLM call for each group with missing evaluations.

    Proposal IDs are managed exclusively by Python.
    """

    evaluator_started = perf_counter()

    proposals: List[Proposal] = state.get("proposals", [])

    if not proposals:
        return {"evaluated_proposals": []}

    proposal_ids = [
        proposal["proposal_id"]
        for proposal in proposals
    ]

    if len(proposal_ids) != len(set(proposal_ids)):
        raise ValueError(
            "Input proposals contain duplicate proposal IDs."
        )

    consumer_context = build_evaluator_consumer_context(state)

    # --------------------------------------------------------
    # GROUP PROPOSALS BY TARGET GAP
    # --------------------------------------------------------

    proposals_by_gap: Dict[str, List[Proposal]] = {}

    for proposal in proposals:
        target_gap = str(proposal.get("target_gap", ""))

        proposals_by_gap.setdefault(target_gap, []).append(proposal)

    groups = list(proposals_by_gap.items())

    logger.info(
        f"[PROPOSAL EVALUATION] Proposals: {len(proposals)} | "
        f"Target gaps: {len(groups)} | "
        f"Max workers: {min(3, len(groups))}",
    )

    # --------------------------------------------------------
    # PARALLEL GROUP EVALUATION
    # --------------------------------------------------------

    evaluations_by_id: Dict[str, Dict[str, str]] = {}

    with ThreadPoolExecutor(
        max_workers=min(3, len(groups))
    ) as executor:

        futures = {
            target_gap: executor.submit(
                evaluate_proposal_group,
                target_gap,
                group_proposals,
                state,
                consumer_context,
            )
            for target_gap, group_proposals in groups
        }

        # Collect results. Each future runs independently.
        for target_gap, group_proposals in groups:
            future = futures[target_gap]

            try:
                group_evaluations = future.result()
            except Exception as error:
                if _is_rate_limit_error(error):
                    logger.info(
                        f"[PROPOSAL EVALUATION] Gap '{target_gap}': "
                        "provider rate limit exhausted; using deterministic "
                        "fallback evaluations.",
                    )
                else:
                    # Local small models may return malformed CSV or
                    # unexpected shapes; degrade gracefully so the
                    # interview can still start.
                    logger.warning(
                        f"[PROPOSAL EVALUATION] Gap '{target_gap}': "
                        "evaluation failed; using deterministic fallback "
                        "evaluations.",
                        extra={"parts": [f"{type(error).__name__}: {error}"]},
                    )
                group_evaluations = build_rate_limit_fallback_evaluations(
                    group_proposals
                )

            duplicate_ids = (
                set(evaluations_by_id)
                & set(group_evaluations)
            )

            if duplicate_ids:
                raise ValueError(
                    "Duplicate evaluations across target groups: "
                    f"{duplicate_ids}"
                )

            evaluations_by_id.update(group_evaluations)

    # --------------------------------------------------------
    # GLOBAL COMPLETENESS VALIDATION
    # --------------------------------------------------------

    expected_ids = set(proposal_ids)
    returned_ids = set(evaluations_by_id)

    missing_ids = expected_ids - returned_ids
    unexpected_ids = returned_ids - expected_ids

    if missing_ids:
        raise ValueError(
            "Proposal evaluation remained incomplete after recovery. "
            f"Missing IDs: {missing_ids}"
        )

    if unexpected_ids:
        raise ValueError(
            f"Unexpected proposal IDs: {unexpected_ids}"
        )

    # --------------------------------------------------------
    # ENRICH ORIGINAL PROPOSALS
    # --------------------------------------------------------

    evaluated_proposals: List[Proposal] = []

    for proposal in proposals:

        proposal_id = proposal["proposal_id"]

        evaluation = evaluations_by_id[proposal_id]

        evaluated_proposal: Proposal = {
            **proposal,
            "expected_information_gain":
                evaluation["expected_information_gain"],
            "expected_state_improvement":
                evaluation["expected_state_improvement"],
            "risk":
                evaluation["risk"],
            "cost":
                evaluation["cost"],
            "consumer_burden":
                evaluation["consumer_burden"],
        }

        evaluated_proposals.append(evaluated_proposal)

    logger.info(
        f"[EVALUATOR TIMING] Total evaluator: "
        f"{perf_counter() - evaluator_started:.2f}s",
    )

    return {
        "evaluated_proposals": evaluated_proposals
    }

