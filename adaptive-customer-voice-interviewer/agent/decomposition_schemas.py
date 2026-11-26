from typing import List, Optional, Literal

from pydantic import BaseModel, Field


# ============================================================
# EXTRACTED EVIDENCE
# ============================================================

class ExtractedEvidence(BaseModel):

    """
    A single atomic piece of information extracted from
    a consumer response.
    """

    category: Literal[
        "product_relationship",
        "product_awareness",
        "usage_experience",
        "overall_sentiment",
        "product_perception",
        "feature_sentiment",
        "needs",
        "pain_points",
        "unmet_needs",
        "expectations",
        "aspirations",
        "purchase_intent",
        "buying_constraints",
        "adoption_barriers",
        "competitor_information",
        "consumer_profile"
    ] = Field(
        description=(
            "The research category this evidence belongs to."
        )
    )

    parameter: str = Field(
        description=(
            "The specific information parameter being described."
        )
    )

    value: str = Field(
        description=(
            "The normalized meaning extracted from the "
            "consumer response."
        )
    )

    sentiment: Optional[
        Literal[
            "positive",
            "negative",
            "neutral",
            "mixed"
        ]
    ] = Field(
        default=None,
        description=(
            "Sentiment expressed or strongly implied by the "
            "consumer. Use only: positive, negative, neutral, "
            "mixed, or null when sentiment is not applicable."
        )
    )

    evidence_type: Literal[
        "explicit",
        "inferred"
    ] = Field(
        description=(
            "How the evidence was obtained. "
            "Use explicit when directly stated by the consumer. "
            "Use inferred only for a reasonable interpretation "
            "derived from the consumer's statement."
        )
    )

    confidence: Literal[
        "low",
        "medium",
        "high"
    ] = Field(
        description=(
            "Qualitative confidence in correctly interpreting "
            "the consumer's meaning. "
            "Use only: low, medium, or high. "
            "Do not use numerical confidence scores."
        )
    )


# ============================================================
# ANSWER DECOMPOSITION
# ============================================================

class AnswerDecompositionSchema(BaseModel):

    """
    Structured interpretation of a consumer response.
    """

    summary: str = Field(
        description=(
            "A concise summary of the meaningful information "
            "expressed by the consumer."
        )
    )

    evidence: List[ExtractedEvidence] = Field(
        min_length=1,
        description=(
            "Atomic pieces of research evidence extracted from "
            "the consumer response. "
            "Return at least one evidence item whenever the "
            "consumer response contains meaningful information."
        )
    )
        
    ambiguity_detected: bool = Field(
        default=False,
        description=(
            "Whether the consumer response contains ambiguity "
            "that could materially affect understanding and may "
            "require clarification. "
            "Always return true or false. "
            "Use false when no meaningful ambiguity exists."
        )
    )

    potential_contradictions: List[str] = Field(
        default_factory=list,
        description=(
            "Potential contradictions found within the current "
            "consumer response itself. Do not compare against "
            "previous responses."
        )
    )
    