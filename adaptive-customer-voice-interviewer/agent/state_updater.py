from copy import deepcopy
from typing import Dict, Any, List, Mapping

from agent.provenance import PROVENANCE_FIELDS

from agent.state import (
    InterviewState,
    InformationRequirement,
    KnowledgeItem,
    EvidenceItem,
    ImportanceLevel,
    ConfidenceLevel,
    SufficiencyLevel,
    VerificationStatus,
    PriorityLevel,
)


# ============================================================
# QUALITATIVE LEVELS
# ============================================================

CONFIDENCE_LEVELS = [
    "low",
    "medium",
    "high"
]

SUFFICIENCY_LEVELS = [
    "missing",
    "basic",
    "adequate",
    "comprehensive"
]


# ============================================================
# EVIDENCE QUALITY
# ============================================================

def determine_evidence_confidence(
    evidence: Dict[str, Any]
) -> ConfidenceLevel:
    """
    Determines the qualitative confidence level of evidence.

    Explicit statements directly provided by the consumer are
    generally stronger than inferred interpretations.
    """

    evidence_type = evidence.get(
        "evidence_type",
        "explicit"
    )

    confidence = evidence.get(
        "confidence",
        "medium"
    )

    # Preserve qualitative confidence if already supplied
    if confidence in CONFIDENCE_LEVELS:

        if evidence_type == "explicit":
            return confidence

        # Inferred evidence should not automatically receive
        # high confidence.
        if evidence_type == "inferred":

            if confidence == "high":
                return "medium"

            return confidence

    # Safe fallback for unexpected values
    if evidence_type == "explicit":
        return "high"

    return "medium"


# ============================================================
# NOVELTY DETECTION
# ============================================================

def is_novel_evidence(
    knowledge_item: KnowledgeItem,
    evidence: Dict[str, Any]
) -> bool:
    """
    Checks whether new evidence adds meaningfully different
    information.

    Current MVP implementation uses normalized exact matching.

    This can later be replaced with semantic similarity.
    """

    new_value = str(
        evidence.get("value", "")
    ).strip().lower()

    existing_values = {
        str(item.get("value", ""))
        .strip()
        .lower()
        for item in knowledge_item.get(
            "values",
            []
        )
    }

    return new_value not in existing_values


# ============================================================
# CONFIDENCE UPDATE
# ============================================================

def update_confidence(
    knowledge_item: KnowledgeItem,
) -> ConfidenceLevel:
    """
    Determines accumulated confidence based on the quality
    and consistency of available evidence.

    Confidence is qualitative rather than mathematically averaged.
    """

    values = knowledge_item.get(
        "values",
        []
    )

    if not values:
        return "low"

    confidence_levels = [
        item.get("confidence", "low")
        for item in values
    ]

    evidence_types = [
        item.get("evidence_type", "explicit")
        for item in values
    ]

    # Multiple explicit pieces of evidence provide stronger confidence
    explicit_count = evidence_types.count(
        "explicit"
    )

    high_confidence_count = confidence_levels.count(
        "high"
    )

    medium_confidence_count = confidence_levels.count(
        "medium"
    )

    if explicit_count >= 2:

        if (
            high_confidence_count >= 1
            or medium_confidence_count >= 2
        ):
            return "high"

    if (
        high_confidence_count >= 1
        or medium_confidence_count >= 2
    ):
        return "medium"

    return "low"


# ============================================================
# SUFFICIENCY
# ============================================================

def determine_sufficiency(
    knowledge_item: KnowledgeItem,
) -> SufficiencyLevel:
    """
    Determines how sufficiently a parameter has been explored.

    Sufficiency represents depth and coverage of understanding,
    not a mathematical accumulation score.
    """

    values = knowledge_item.get(
        "values",
        []
    )

    if not values:
        return "missing"

    evidence_count = len(values)

    verification_status = knowledge_item.get(
        "verification_status",
        "unverified"
    )

    # One meaningful piece of evidence provides basic understanding
    if evidence_count == 1:
        return "basic"

    # Multiple pieces of evidence provide broader understanding
    if evidence_count >= 2:

        if verification_status == "verified":

            if evidence_count >= 3:
                return "comprehensive"

            return "adequate"

        return "adequate"

    return "basic"


# ============================================================
# VERIFICATION
# ============================================================

def update_verification_status(
    knowledge_item: KnowledgeItem,
) -> VerificationStatus:
    """
    Determines whether accumulated evidence has been sufficiently
    clarified or reinforced.

    Verification does not mean external fact-checking.

    The consumer is the sole information source.

    Verification means the understanding has been reinforced,
    clarified, or made sufficiently consistent through the
    conversation.
    """

    values = knowledge_item.get(
        "values",
        []
    )

    if not values:
        return "unverified"

    if len(values) == 1:
        return "unverified"

    explicit_evidence = [
        item
        for item in values
        if item.get("evidence_type") == "explicit"
    ]

    if len(explicit_evidence) < 2:
        return "partially_verified"

    confidence_levels = [
        item.get("confidence", "low")
        for item in explicit_evidence
    ]

    # Multiple explicit statements with medium/high confidence
    # are considered sufficient reinforcement.
    if all(
        confidence in ["medium", "high"]
        for confidence in confidence_levels
    ):
        return "verified"

    return "partially_verified"


# ============================================================
# KNOWLEDGE ITEM CREATION
# ============================================================

def build_evidence_record(
    evidence: Dict[str, Any],
) -> EvidenceItem:
    """Build an evidence item, preserving any accepted provenance."""

    record: EvidenceItem = {
        "value": evidence["value"],
        "source": "consumer_response",
        "confidence": determine_evidence_confidence(
            evidence
        ),
        "sentiment": evidence.get("sentiment"),
        "evidence_type": evidence.get(
            "evidence_type",
            "explicit",
        ),
    }

    for field in PROVENANCE_FIELDS:
        value = evidence.get(field)

        if value is not None:
            record[field] = value

    return record


def create_knowledge_item(
    evidence: Dict[str, Any],
    criticality: ImportanceLevel = "medium"
) -> KnowledgeItem:
    """
    Creates a new KnowledgeItem from extracted evidence.
    """

    evidence_confidence = (
        determine_evidence_confidence(
            evidence
        )
    )

    evidence_record: EvidenceItem = (
        build_evidence_record(evidence)
    )

    knowledge_item: KnowledgeItem = {
        "values": [evidence_record],
        "sufficiency": "basic",
        "confidence": evidence_confidence,
        "criticality": criticality,
        "verification_status": "unverified",
        "applicability_status": "applicable",
    }

    return knowledge_item


# ============================================================
# APPEND EVIDENCE
# ============================================================

def append_evidence(
    knowledge_item: KnowledgeItem,
    evidence: Dict[str, Any]
) -> KnowledgeItem:
    """
    Appends evidence while preserving previous knowledge and
    updates qualitative sufficiency, confidence, and verification.
    """

    novel = is_novel_evidence(
        knowledge_item,
        evidence
    )

    new_evidence: EvidenceItem = (
        build_evidence_record(evidence)
    )

    knowledge_item["values"].append(
        new_evidence
    )

    # Repeated evidence primarily reinforces existing knowledge.
    # Novel evidence expands coverage.
    knowledge_item["verification_status"] = (
        update_verification_status(
            knowledge_item
        )
    )

    knowledge_item["confidence"] = (
        update_confidence(
            knowledge_item
        )
    )

    if novel:

        knowledge_item["sufficiency"] = (
            determine_sufficiency(
                knowledge_item
            )
        )

    else:
        # Repeated evidence does not necessarily expand coverage,
        # but may strengthen verification and confidence.
        current_sufficiency = knowledge_item.get(
            "sufficiency",
            "basic"
        )

        knowledge_item["sufficiency"] = (
            current_sufficiency
        )

    return knowledge_item


# ============================================================
# META PLAN LOOKUP
# ============================================================

def get_requirement_criticality(
    state: InterviewState,
    category: str
) -> ImportanceLevel:
    """
    Finds criticality from the Meta Plan.
    """

    meta_plan = state.get(
        "meta_plan"
    )

    if not meta_plan:
        return "medium"

    normalized_category = (
        category.replace(
            "_",
            " "
        ).lower()
    )

    requirements: List[InformationRequirement] = meta_plan.get(
        "required_information",
        []
    )

    for requirement in requirements:

        parameter = requirement.get(
            "parameter",
            ""
        ).replace(
            "_",
            " "
        ).lower()

        if parameter == normalized_category:

            return requirement.get(
                "criticality",
                "medium"
            )

    return "medium"


# ============================================================
# TOP LEVEL KNOWLEDGE UPDATE
# ============================================================

def update_single_knowledge_item(
    state: InterviewState,
    field: str,
    evidence: Dict[str, Any]
) -> None:
    """
    Updates a top-level KnowledgeItem.
    """

    criticality = get_requirement_criticality(
        state,
        evidence["category"]
    )

    existing = state.get(field)

    if existing is None:

        state[field] = create_knowledge_item(
            evidence,
            criticality
        )

    else:

        state[field] = append_evidence(
            existing,
            evidence
        )


# ============================================================
# DICTIONARY KNOWLEDGE UPDATE
# ============================================================

def update_dictionary_knowledge(
    state: InterviewState,
    field: str,
    evidence: Dict[str, Any]
) -> None:
    """
    Updates dictionary-based KnowledgeItems.

    Example:

        feature_sentiment["camera"]

        pain_points["battery"]
    """

    parameter = evidence["parameter"]

    criticality = get_requirement_criticality(
        state,
        evidence["category"]
    )

    if field not in state or state[field] is None:

        state[field] = {}

    existing = state[field].get(
        parameter
    )

    if existing is None:

        state[field][parameter] = (
            create_knowledge_item(
                evidence,
                criticality
            )
        )

    else:

        state[field][parameter] = (
            append_evidence(
                existing,
                evidence
            )
        )



# ============================================================
# AGGREGATE STATE-QUALITY SYNCHRONIZATION
# ============================================================

def get_aggregate_knowledge(
    state: InterviewState,
    field: str,
    state_type: str,
) -> Dict[str, Any]:
    """
    Derives aggregate qualitative state from the KnowledgeItem(s)
    associated with a research parameter.

    KnowledgeItems remain the source of truth.
    This function only creates the aggregate projection used by
    snapshots and reflection.
    """

    if state_type == "single":

        item = state.get(field)

        if not item:
            return {
                "sufficiency": "missing",
                "confidence": "low",
                "verification_status": "unverified",
            }

        return {
            "sufficiency": item.get(
                "sufficiency",
                "missing",
            ),
            "confidence": item.get(
                "confidence",
                "low",
            ),
            "verification_status": item.get(
                "verification_status",
                "unverified",
            ),
        }

    items = state.get(field, {})

    if not items:
        return {
            "sufficiency": "missing",
            "confidence": "low",
            "verification_status": "unverified",
        }

    knowledge_items = list(items.values())

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
        }

    sufficiency_values = [
        item.get(
            "sufficiency",
            "missing",
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

    sufficiency_order = {
        "missing": 0,
        "basic": 1,
        "adequate": 2,
        "comprehensive": 3,
    }

    confidence_order = {
        "low": 1,
        "medium": 2,
        "high": 3,
    }

    lowest_sufficiency = min(
        sufficiency_values,
        key=lambda value: sufficiency_order.get(
            value,
            0,
        ),
    )

    lowest_confidence = min(
        confidence_values,
        key=lambda value: confidence_order.get(
            value,
            1,
        ),
    )

    verification_status = (
        "verified"
        if all(
            item.get(
                "verification_status",
                "unverified",
            ) == "verified"
            for item in applicable_items
        )
        else "unverified"
    )

    return {
        "sufficiency": lowest_sufficiency,
        "confidence": lowest_confidence,
        "verification_status": verification_status,
    }


def get_requirement_goal_alignment(
    requirement: InformationRequirement,
) -> PriorityLevel:
    """
    Returns the research-goal alignment defined by the Meta Plan.

    Goal alignment is a property of the research requirement,
    not something inferred from consumer evidence.
    """

    return requirement.get(
        "goal_alignment",
        "medium",
    )


def sync_state_quality_indexes(
    state: InterviewState,
) -> None:
    """
    Synchronizes the aggregate state-quality dictionaries
    from the authoritative KnowledgeItems and Meta Plan.

    These dictionaries are projections used by snapshot,
    reflection, and downstream diagnostics.

    They are not independent sources of truth.
    """

    meta_plan = state.get("meta_plan")

    if not meta_plan:
        return

    information_sufficiency: Dict[str, SufficiencyLevel] = {}
    confidence: Dict[str, ConfidenceLevel] = {}
    criticality: Dict[str, ImportanceLevel] = {}
    verification_status: Dict[str, VerificationStatus] = {}
    research_goal_alignment: Dict[str, PriorityLevel] = {}

    top_level_mapping = {
        "product_relationship": (
            "product_relationship",
            "single",
        ),
        "product_awareness": (
            "product_awareness",
            "single",
        ),
        "purchase_intent": (
            "purchase_intent",
            "single",
        ),
        "overall_sentiment": (
            "overall_sentiment",
            "single",
        ),
        "product_perception": (
            "product_perception",
            "single",
        ),
    }

    dictionary_mapping = {
        "usage_experience": (
            "features",
            "multiple",
        ),
        "feature_sentiment": (
            "feature_sentiment",
            "multiple",
        ),
        "consumer_profile": (
            "consumer_profile",
            "multiple",
        ),
        "needs": (
            "needs",
            "multiple",
        ),
        "unmet_needs": (
            "needs",
            "multiple",
        ),
        "pain_points": (
            "pain_points",
            "multiple",
        ),
        "use_cases": (
            "use_cases",
            "multiple",
        ),
        "aspirations": (
            "aspirations",
            "multiple",
        ),
        "expectations": (
            "expectations",
            "multiple",
        ),
        "consumer_intent": (
            "consumer_intent",
            "multiple",
        ),
        "competitor_information": (
            "competitor_information",
            "multiple",
        ),
        "budget_constraints": (
            "budget_constraints",
            "multiple",
        ),
        "buying_constraints": (
            "buying_constraints",
            "multiple",
        ),
        "adoption_barriers": (
            "adoption_barriers",
            "multiple",
        ),
    }

    for requirement in meta_plan.get(
        "required_information",
        [],
    ):
        parameter = requirement.get("parameter")

        if not parameter:
            continue

        mapping = top_level_mapping.get(parameter)

        if mapping is None:
            mapping = dictionary_mapping.get(parameter)

        if mapping is None:
            continue

        field, state_type = mapping

        knowledge = get_aggregate_knowledge(
            state=state,
            field=field,
            state_type=state_type,
        )

        information_sufficiency[parameter] = (
            knowledge["sufficiency"]
        )

        confidence[parameter] = (
            knowledge["confidence"]
        )

        verification_status[parameter] = (
            knowledge["verification_status"]
        )

        criticality[parameter] = (
            requirement.get(
                "criticality",
                "medium",
            )
        )

        research_goal_alignment[parameter] = (
            get_requirement_goal_alignment(
                requirement
            )
        )

    state["information_sufficiency"] = (
        information_sufficiency
    )

    state["confidence"] = confidence
    state["criticality"] = criticality
    state["verification_status"] = (
        verification_status
    )
    state["research_goal_alignment"] = (
        research_goal_alignment
    )


# ============================================================
# MAIN STATE UPDATE NODE
# ============================================================

def update_state(
    state: InterviewState
) -> Mapping[str, Any]:
    """
    Deterministically updates persistent interview knowledge using
    the latest decomposed consumer response.

    Responsibilities:

    - Preserve evidence
    - Detect repeated evidence
    - Expand knowledge coverage
    - Update qualitative confidence
    - Update verification status
    - Update information sufficiency

    This node does not use an LLM.
    """

    decomposition = state.get(
        "latest_decomposition"
    )

    if not decomposition:
        return {}

    updated_state = deepcopy(state)

    evidence_items = decomposition.get(
        "evidence",
        []
    )

    top_level_mapping = {

        "product_relationship":
            "product_relationship",

        "product_awareness":
            "product_awareness",

        "purchase_intent":
            "purchase_intent",

        "overall_sentiment":
            "overall_sentiment",

        "product_perception":
            "product_perception",
    }

    dictionary_mapping = {

        "usage_experience":
            "features",

        "feature_sentiment":
            "feature_sentiment",

        "consumer_profile":
            "consumer_profile",

        "needs":
            "needs",

        "unmet_needs":
            "needs",

        "pain_points":
            "pain_points",

        "use_cases":
            "use_cases",

        "aspirations":
            "aspirations",

        "expectations":
            "expectations",

        "consumer_intent":
            "consumer_intent",

        "competitor_information":
            "competitor_information",

        "budget_constraints":
            "budget_constraints",

        "buying_constraints":
            "buying_constraints",

        "adoption_barriers":
            "adoption_barriers",
    }

    for evidence in evidence_items:

        category = evidence.get(
            "category"
        )

        if category in top_level_mapping:

            update_single_knowledge_item(
                updated_state,
                top_level_mapping[category],
                evidence
            )

        elif category in dictionary_mapping:

            update_dictionary_knowledge(
                updated_state,
                dictionary_mapping[category],
                evidence
            )


# ------------------------------------------------------------
# Synchronize aggregate state-quality indexes
# ------------------------------------------------------------

    sync_state_quality_indexes(
        updated_state
    )


    return updated_state