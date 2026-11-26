from datetime import date

from agent.voice.voice_types import VoiceInteractionMode
from typing import (
    TypedDict,
    Optional,
    List,
    Dict,
    Any,
    Literal,
    NotRequired,
)


# ============================================================
# QUALITATIVE TYPE DEFINITIONS
# ============================================================

ResponseStatus = Literal[
    "valid",
    "unrelated",
    "non_responsive",
    "unclear",
    "no_response",
    "audio_issue",
]

ResponseRecoveryAction = Literal[
    "continue_research",
    "repeat_question",
    "redirect_to_question",
    "ask_repeat",
    "check_audibility",
    "check_connection",
    "stop",
]

ImportanceLevel = Literal[
    "critical",
    "high",
    "medium",
    "low"
]

PriorityLevel = Literal[
    "high",
    "medium",
    "low"
]

ConfidenceLevel = Literal[
    "low",
    "medium",
    "high"
]

SufficiencyLevel = Literal[
    "missing",
    "basic",
    "adequate",
    "comprehensive"
]

VerificationStatus = Literal[
    "unverified",
    "partially_verified",
    "verified"
]

ApplicabilityStatus = Literal[
    "unknown",
    "applicable",
    "not_applicable"
]

EvidenceType = Literal[
    "explicit",
    "inferred"
]

SentimentType = Literal[
    "positive",
    "negative",
    "neutral",
    "mixed"
]

GapType = Literal[
    "missing",
    "insufficient",
    "verification_required",
    "not_applicable"
]

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
# RESPONSE PROVENANCE
# ============================================================

class ResponseRecord(TypedDict):
    """One accepted consumer answer with auditable provenance."""

    response_id: str
    turn: int
    question: Optional[str]
    text: str
    captured_at: str
    participant_id: str


class ResponseOutcomeRecord(TypedDict):
    """One classified submission, including invalid attempts."""

    outcome_id: str
    sequence: int
    session_id: str
    response_id: str
    status: ResponseStatus
    question: Optional[str]
    transcript: Optional[str]
    captured_at: str
    participant_id: str
    classification_source: str


# ============================================================
# RESEARCH SESSION
# ============================================================

class ResearchSession(TypedDict):
    """
    Defines a specific research campaign/session.

    A company can run multiple research sessions over time,
    potentially for different products, research goals,
    audiences, and time periods.
    """

    session_id: str

    company_id: str
    company_name: str

    product_id: str
    product_name: str
    product_category: str

    research_goal: str

    start_date: date
    end_date: Optional[date]

    target_audience: Optional[str]

    # Normalized reporting dimensions. Older sessions may omit these.
    customer_segment: NotRequired[Optional[str]]
    participant_id: NotRequired[Optional[str]]

    interview_constraints: Optional[List[str]]

    # Participant link lifetime. Older sessions may omit these.
    link_expiry: NotRequired[Optional[str]]
    link_expires_at: NotRequired[Optional[str]]
    interview_started_at: NotRequired[Optional[str]]

    # Explicit interview lifecycle, separate from campaign status. Older
    # sessions may omit these and are normalized by agent.session helpers.
    interview_status: NotRequired[
        Literal[
            "created",
            "starting",
            "in_progress",
            "completed",
            "failed",
        ]
    ]
    interview_completed_at: NotRequired[Optional[str]]
    interview_failed_at: NotRequired[Optional[str]]
    interview_error_type: NotRequired[Optional[str]]
    interview_termination_reason: NotRequired[Optional[str]]

    # Participant invitation capability. Only the hash is stored durably.
    participant_token_hash: NotRequired[Optional[str]]
    participant_token_issued_at: NotRequired[Optional[str]]

    status: Literal[
        "scheduled",
        "active",
        "completed",
        "expired",
        "cancelled",
    ]


# ============================================================
# RESEARCH CONTEXT
# ============================================================

class ResearchContext(TypedDict):
    """
    Defines what product is being researched and why.
    """

    company_name: str

    product_name: str

    product_category: str

    research_goal: str

    customer_segment: NotRequired[Optional[str]]

    target_audience: NotRequired[Optional[str]]

    interview_constraints: NotRequired[Optional[List[str]]]


# ============================================================
# META PLAN
# ============================================================

class InformationRequirement(TypedDict):
    """
    Defines information the interview should attempt to collect.

    These are qualitative planning requirements rather than
    numerical thresholds.
    """

    parameter: str

    importance: ImportanceLevel

    goal_alignment: PriorityLevel

    sufficiency_requirement: SufficiencyLevel

    criticality: ImportanceLevel

    verification_requirement: bool

    confidence_requirement: ConfidenceLevel

    expected_goal_improvement: PriorityLevel

    estimated_cost: PriorityLevel

    estimated_consumer_burden: PriorityLevel


class MetaPlan(TypedDict):
    """
    High-level information requirements for the interview.
    """

    research_goal: str

    required_information: List[InformationRequirement]

    termination_criteria: Dict[str, Any]


# ============================================================
# EVIDENCE
# ============================================================

class EvidenceItem(TypedDict):
    """
    A structured piece of evidence extracted from a
    consumer response.
    """

    value: Any

    source: str

    confidence: ConfidenceLevel

    sentiment: Optional[SentimentType]

    evidence_type: EvidenceType

    # Provenance is present on evidence produced by the live graph.
    evidence_id: NotRequired[Optional[str]]
    response_id: NotRequired[Optional[str]]
    turn: NotRequired[Optional[int]]
    captured_at: NotRequired[Optional[str]]
    participant_id: NotRequired[Optional[str]]
    source_quote: NotRequired[Optional[str]]


class KnowledgeItem(TypedDict):
    """
    Accumulated knowledge about a particular research parameter.

    Knowledge quality is represented qualitatively rather than
    through numerical accumulation.
    """

    values: List[EvidenceItem]

    sufficiency: SufficiencyLevel

    confidence: ConfidenceLevel

    criticality: ImportanceLevel

    verification_status: VerificationStatus

    applicability_status: ApplicabilityStatus


# ============================================================
# INFORMATION GAPS
# ============================================================

class InformationGap(TypedDict):
    """
    Represents information that is missing, insufficient,
    or requires clarification / verification.
    """

    parameter: str

    category: str

    gap_type: GapType

    importance: ImportanceLevel

    goal_alignment: PriorityLevel

    criticality: ImportanceLevel

    confidence: ConfidenceLevel

    verification_required: bool

    expected_goal_improvement: PriorityLevel

    estimated_cost: PriorityLevel

    estimated_consumer_burden: PriorityLevel


# ============================================================
# QUESTION PROPOSALS
# ============================================================

class Proposal(TypedDict):
    """
    Represents a candidate next interview question.

    All proposal evaluation dimensions are qualitative.
    """

    proposal_id: str

    target_gap: str

    question: str

    expected_information_gain: PriorityLevel

    expected_state_improvement: PriorityLevel

    importance: ImportanceLevel

    goal_alignment: PriorityLevel

    criticality: ImportanceLevel

    risk: PriorityLevel

    cost: PriorityLevel

    consumer_burden: PriorityLevel


# ============================================================
# STATE SNAPSHOT
# ============================================================

class StateSnapshot(TypedDict):
    """
    Lightweight snapshot of the interview state used by the
    reflection component to compare state changes across turns.
    """

    iteration: int

    information_sufficiency: Dict[str, str]

    confidence: Dict[str, str]

    verification_status: Dict[str, str]

    unresolved_gap_parameters: List[str]


# ============================================================
# REFLECTION
# ============================================================

class Reflection(TypedDict):

    previous_question: Optional[str]

    understanding_improved: bool

    gap_resolved: bool

    contradictions_detected: bool

    confidence_change: str
    # decreased | unchanged | slightly_increased | increased

    remaining_uncertainty: str
    # low | medium | high

    additional_question_value: str
    # low | medium | high

# ============================================================
# MAIN LANGGRAPH SYSTEM STATE
# ============================================================

class InterviewState(TypedDict):

    # --------------------------------------------------------
    # RESEARCH SESSION
    # --------------------------------------------------------

    research_session: ResearchSession


    # --------------------------------------------------------
    # RESEARCH CONTEXT
    # --------------------------------------------------------

    research_context: ResearchContext


    # --------------------------------------------------------
    # META PLAN
    # --------------------------------------------------------

    meta_plan: Optional[MetaPlan]


    # --------------------------------------------------------
    # CONSUMER-PRODUCT CONTEXT
    # --------------------------------------------------------

    product_relationship: Optional[KnowledgeItem]

    product_awareness: Optional[KnowledgeItem]

    purchase_intent: Optional[KnowledgeItem]


    # --------------------------------------------------------
    # PRODUCT SENTIMENT / PERCEPTION
    # --------------------------------------------------------

    overall_sentiment: Optional[KnowledgeItem]

    product_perception: Optional[KnowledgeItem]

    feature_sentiment: Dict[str, KnowledgeItem]


    # --------------------------------------------------------
    # CONSUMER KNOWLEDGE
    # --------------------------------------------------------

    consumer_profile: Dict[str, KnowledgeItem]

    product_or_service: List[EvidenceItem]

    features: Dict[str, KnowledgeItem]

    needs: Dict[str, KnowledgeItem]

    pain_points: Dict[str, KnowledgeItem]

    use_cases: Dict[str, KnowledgeItem]

    aspirations: Dict[str, KnowledgeItem]

    expectations: Dict[str, KnowledgeItem]

    consumer_intent: Dict[str, KnowledgeItem]

    competitor_information: Dict[str, KnowledgeItem]

    budget_constraints: Dict[str, KnowledgeItem]

    buying_constraints: Dict[str, KnowledgeItem]

    adoption_barriers: Dict[str, KnowledgeItem]


    # --------------------------------------------------------
    # CONVERSATION MEMORY
    # --------------------------------------------------------

    questions_asked: List[str]

    responses: List[str]

    response_records: NotRequired[List[ResponseRecord]]
    response_outcomes: NotRequired[List[ResponseOutcomeRecord]]

    current_question: Optional[str]

    latest_response: Optional[str]

    latest_response_id: NotRequired[Optional[str]]
    latest_response_captured_at: NotRequired[Optional[str]]
    latest_response_turn: NotRequired[Optional[int]]
    latest_response_question: NotRequired[Optional[str]]
    latest_participant_id: NotRequired[Optional[str]]


    # --------------------------------------------------------
    # TEMPORARY PROCESSING STATE
    # --------------------------------------------------------

    latest_decomposition: Optional[Dict[str, Any]]


    # --------------------------------------------------------
    # STATE QUALITY
    # --------------------------------------------------------

    information_sufficiency: Dict[
        str,
        SufficiencyLevel
    ]

    confidence: Dict[
        str,
        ConfidenceLevel
    ]

    criticality: Dict[
        str,
        ImportanceLevel
    ]

    verification_status: Dict[
        str,
        VerificationStatus
    ]

    research_goal_alignment: Dict[
        str,
        PriorityLevel
    ]

    # --------------------------------------------------------
    # STATE HISTORY
    # --------------------------------------------------------

    previous_state_snapshot: Optional[StateSnapshot]
    
    state_history: List[StateSnapshot]


    # --------------------------------------------------------
    # GAP ANALYSIS
    # --------------------------------------------------------

    unresolved_gaps: List[
        InformationGap
    ]


    # --------------------------------------------------------
    # QUESTION PROPOSAL SYSTEM
    # --------------------------------------------------------

    proposals: List[
        Proposal
    ]

    evaluated_proposals: List[
        Proposal
    ]

    selected_proposal: Optional[
        Proposal
    ]


    # --------------------------------------------------------
    # DECISION
    # --------------------------------------------------------

    next_action: Optional[str]


    # --------------------------------------------------------
    # REFLECTION
    # --------------------------------------------------------

    reflection: Optional[
        Reflection
    ]


    # --------------------------------------------------------
    # INTERVIEW CONTROL
    # --------------------------------------------------------

    iteration_count: int

    interview_complete: bool

    termination_reason: Optional[str]


    # --------------------------------------------------------
    # FINAL OUTPUT
    # --------------------------------------------------------

    final_insights: Optional[
        Dict[str, Any]
    ]

        # --------------------------------------------------------
    # VOICE INTERACTION STATE
    # --------------------------------------------------------

    voice_interaction_mode: VoiceInteractionMode

    response_status: Optional[ResponseStatus]

    response_recovery_action: Optional[ResponseRecoveryAction]

    response_retry_count: int