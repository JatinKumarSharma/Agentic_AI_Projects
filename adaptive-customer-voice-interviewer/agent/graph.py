from typing import Dict, Any

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from functools import wraps
from time import perf_counter

from agent.state import InterviewState
from agent import timing

from agent.meta_planner import create_meta_plan
from agent.proposal_generator import generate_proposals
from agent.proposal_evaluator import evaluate_proposals
from agent.proposal_ranker import rank_proposals
from agent.decision_policy import decide_next_action
from agent.state_snapshot import capture_pre_question_snapshot
from agent.executive_controller import execute_decision
from agent.answer_decomposer import decompose_answer
from agent.state_updater import update_state
from agent.gap_analyzer import analyze_gaps
from agent.reflection import reflect_on_response
from agent.session_validator import validate_research_session
from agent.response_triage import triage_response
from agent.response_acceptor import accept_valid_response
from agent.response_outcomes import append_outcome
from agent.response_recovery import recover_from_response
from agent.observability import get_logger

logger = get_logger(__name__)



FIRST_QUESTION_NODES = {
    "meta_planner",
    "proposal_generator",
    "proposal_evaluator",
    "proposal_ranker",
    "decision_policy",
    "executive_controller",
    "question_delivery",
}


def timed_node(node_name, node_function):
    @wraps(node_function)
    def wrapper(*args, **kwargs):
        started_at = perf_counter()
        started_ms = timing.now_ms()

        try:
            return node_function(*args, **kwargs)
        finally:
            elapsed = perf_counter() - started_at
            logger.info(
                f"[NODE TIMING] {node_name}: {elapsed:.2f}s",
            )

            state = args[0] if args else {}
            session = state.get("research_session") or {}
            session_id = session.get("session_id")

            if (
                session_id
                and node_name in FIRST_QUESTION_NODES
                and state.get("iteration_count", 0) == 0
            ):
                timing.record(
                    session_id,
                    1,
                    f"first_question_{node_name}",
                    started_ms,
                    metadata={"node": node_name},
                )

    return wrapper

# ============================================================
# VOICE RUNTIME BOUNDARY
# ============================================================

def deliver_question(state: InterviewState) -> Dict[str, Any]:
    """
    Pause the graph and hand the selected question to the
    external voice runtime.

    The external runtime is responsible for:
        - TTS
        - audio playback
        - confirming delivery

    The question is added to questions_asked only after
    delivery has been confirmed.
    """

    question = state.get("current_question")

    if not question:
        raise ValueError(
            "Question delivery requested but current_question "
            "is missing."
        )

    result = interrupt(
        {
            "type": "deliver_question",
            "question": question,
            "iteration": state.get("iteration_count", 0),
            "turn": len(state.get("questions_asked", [])) + 1,
        }
    )

    # Expected resume payload:
    #
    # {
    #     "delivered": True
    # }

    if isinstance(result, dict):
        if result.get("delivered") is False:
            return {
                "interview_complete": True,
                "next_action": "stop",
                "termination_reason": (
                    "question_delivery_failed"
                ),
            }

    questions_asked = list(
        state.get("questions_asked", [])
    )

    questions_asked.append(question)

    return {
        "questions_asked": questions_asked,
    }


def _response_provenance(
    response: Any,
    current_question: str,
) -> Dict[str, Any]:
    """Carry accepted response metadata through every outcome branch."""

    if not isinstance(response, dict):
        return {}

    return {
        "latest_response_id": response.get("response_id"),
        "latest_response_captured_at": response.get("captured_at"),
        "latest_response_question": (
            response.get("question") or current_question
        ),
        "latest_participant_id": response.get("participant_id"),
    }


def _runtime_outcome(
    state: InterviewState,
    status: str,
) -> Dict[str, Any]:
    """Record runtime classifications only for research answers."""

    mode = state.get("voice_interaction_mode", "research_answer")

    if mode != "research_answer":
        return {}

    return append_outcome(state, status, "runtime")


def receive_consumer_response(state: InterviewState) -> Dict[str, Any]:
    current_question = state.get("current_question")

    logger.info("debug_payload", extra={"parts": ["DEBUG receive_consumer_response:", {             "voice_interaction_mode": state.get(                 "voice_interaction_mode"             ),             "response_retry_count": state.get(                 "response_retry_count"             ),         }]})
    logger.info("debug_payload", extra={"parts": ["DEBUG consumer response output:", {             "latest_response": state.get("latest_response"),             "response_status": state.get("response_status"),             "voice_interaction_mode": state.get(                 "voice_interaction_mode"             ),             "response_retry_count": state.get(                 "response_retry_count"             ),         }]})
    if not current_question:
        return {
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": "missing_current_question",
        }

    interaction_mode = state.get(
        "voice_interaction_mode",
        "research_answer",
    )

    response = interrupt(
        {
            "type": "await_consumer_response",
            "question": current_question,
            "interaction_mode": interaction_mode,
            "turn": len(state.get("questions_asked", [])),
        }
    )

    logger.info("debug_payload", extra={"parts": ["DEBUG resumed interrupt value:", repr(response)]})
    if not response:
        return {
            "latest_response": None,
            "response_status": "no_response",
            **_response_provenance(response, current_question),
            **_runtime_outcome(state, "no_response"),
        }

    if not isinstance(response, dict):
        response = {
            "transcript": str(response),
            "speech_detected": True,
            "audio_issue": False,
        }

    if response.get("audio_issue"):
        return {
            "latest_response": None,
            "response_status": "audio_issue",
            **_response_provenance(response, current_question),
            **_runtime_outcome(state, "audio_issue"),
        }

    transcript = response.get("transcript")

    if not transcript or not transcript.strip():
        return {
            "latest_response": None,
            "response_status": "no_response",
            **_response_provenance(response, current_question),
            **_runtime_outcome(state, "no_response"),
        }

    return {
        "latest_response": transcript.strip(),
        "response_status": None,
        **_response_provenance(response, current_question),
    }


def voice_confirmation(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Handle deterministic voice confirmations after an audio or
    audibility recovery action.

    This node does not use an LLM.

    Expected confirmations:
        - confirmed: consumer can hear/interact normally
        - rejected: consumer reports they cannot hear/interact
        - unclear: confirmation could not be determined

    The voice runtime should provide the latest response through
    `latest_response`.
    """

    logger.info("debug_payload", extra={"parts": ["DEBUG voice_confirmation input:", {             "latest_response": state.get("latest_response"),             "response_status": state.get("response_status"),             "voice_interaction_mode": state.get(                 "voice_interaction_mode"             ),             "response_recovery_action": state.get(                 "response_recovery_action"             ),         }]})
    response = state.get("latest_response")

    if not response:
        return {
            "response_recovery_action": "check_audibility",
        }

    normalized = response.strip().lower().rstrip(".!?")
    logger.info("debug_payload", extra={"parts": ["DEBUG normalized:", repr(normalized)]})
    # ------------------------------------------------------------
    # Positive confirmation
    # ------------------------------------------------------------

    confirmed_responses = {
        "yes",
        "yeah",
        "yep",
        "yup",
        "yes i can hear you",
        "yes, i can hear you",
        "i can hear you",
        "i hear you",
        "loud and clear",
        "clear",
        "it's clear",
        "it is clear",
        "works",
        "working",
        "i can hear",
    }

    if normalized in confirmed_responses:
        logger.info("debug_payload", extra={"parts": ["DEBUG voice_confirmation accepted:", repr(normalized)]})
        logger.info("debug_payload", extra={"parts": ["DEBUG accepted confirmation state:", {                 "normalized": normalized,                 "voice_interaction_mode": "research_answer",                 "response_recovery_action": None,                 "response_status": None,             }]})
        return {
            "voice_interaction_mode": "research_answer",
            "response_recovery_action": None,
            "response_status": None,
            "latest_response": None,
            "response_retry_count": 0,
        }

    # ------------------------------------------------------------
    # Negative confirmation
    # ------------------------------------------------------------

    rejected_responses = {
        "no",
        "nope",
        "not really",
        "i can't hear you",
        "i cannot hear you",
        "can't hear you",
        "cannot hear you",
        "i can't hear",
        "i cannot hear",
        "not clear",
        "it's not clear",
        "it is not clear",
        "still having trouble",
        "still not working",
    }

    if normalized in rejected_responses:
        return {
            "response_recovery_action": "check_connection",
            "response_status": "audio_issue",
        }

    # ------------------------------------------------------------
    # Unclear confirmation
    # ------------------------------------------------------------

    return {
        "response_recovery_action": "check_audibility",
    }


def execute_response_recovery(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Execute the response-recovery action through the external
    voice runtime.

    This node does not generate recovery language itself.
    The voice runtime receives the recovery action and handles
    the actual spoken interaction.

    Recovery actions:

        repeat_question
            -> repeat the current research question

        redirect_to_question
            -> redirect the consumer back to the question

        ask_repeat
            -> ask the consumer to repeat their answer

        check_audibility
            -> switch to audibility-confirmation mode

        check_connection
            -> switch to connection-confirmation mode

        stop
            -> terminate the interview
    """

    action = state.get("response_recovery_action")

    question = state.get("current_question")

    # ------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------

    if action == "stop":
        return {
            "interview_complete": True,
            "next_action": "stop",
        }

    # ------------------------------------------------------------
    # Validate recovery action
    # ------------------------------------------------------------

    valid_actions = {
        "repeat_question",
        "redirect_to_question",
        "ask_repeat",
        "check_audibility",
        "check_connection",
    }

    if action not in valid_actions:
        return {
            "interview_complete": True,
            "next_action": "stop",
            "termination_reason": "invalid_response_recovery_action",
        }

    # ------------------------------------------------------------
    # Determine interaction mode
    # ------------------------------------------------------------

    interaction_mode = state.get(
        "voice_interaction_mode",
        "research_answer",
    )

    if action in {
        "repeat_question",
        "redirect_to_question",
        "ask_repeat",
    }:
        interaction_mode = "research_answer"

    elif action == "check_audibility":
        interaction_mode = "audibility_confirmation"

    elif action == "check_connection":
        interaction_mode = "connection_confirmation"

    # ------------------------------------------------------------
    # Hand recovery action to external voice runtime
    # ------------------------------------------------------------

    result = interrupt(
        {
            "type": "response_recovery",
            "action": action,
            "question": question,
            "retry_count": state.get(
                "response_retry_count",
                0,
            ),
            "interaction_mode": interaction_mode,
        }
    )

    # ------------------------------------------------------------
    # Voice runtime failed to execute recovery
    # ------------------------------------------------------------

    if isinstance(result, dict):
        if result.get("failed") is True:
            return {
                "interview_complete": True,
                "next_action": "stop",
                "termination_reason": "response_recovery_failed",
            }

    # ------------------------------------------------------------
    # Recovery was successfully executed
    # ------------------------------------------------------------

    return {
        "voice_interaction_mode": interaction_mode,
        "latest_response": None,
        "response_status": None,
    }


# ============================================================
# ITERATION MANAGEMENT
# ============================================================

def advance_iteration(
    state: InterviewState,
) -> Dict[str, Any]:
    """
    Increment the completed interview-turn counter.

    The iteration is advanced only after:
        question delivery
        consumer response
        answer decomposition
        state update
        reflection
    """

    return {
        "iteration_count": (
            state.get("iteration_count", 0) + 1
        )
    }


# ============================================================
# ROUTING
# ============================================================

def route_after_session_validation(
    state: InterviewState,
) -> str:
    """
    Route according to research-session eligibility.
    """

    if state.get("interview_complete"):
        return "stop"

    return "continue"

def route_after_decision(
    state: InterviewState,
) -> str:
    """Route according to the Decision Policy."""

    if state.get("next_action") == "ask_question":
        return "ask_question"

    return "stop"

def route_after_execution(
    state: InterviewState,
) -> str:
    """Route according to Executive Controller outcome."""

    if state.get("interview_complete"):
        return "stop"

    if state.get("next_action") == "ask_question":
        return "ask_question"

    return "stop"



def route_after_question_delivery(
    state: InterviewState,
) -> str:

    if state.get("interview_complete"):
        return "stop"

    return "continue"


def route_after_voice_confirmation(
    state: InterviewState,
) -> str:
    """
    Route after deterministic voice confirmation.

    confirmed:
        Return to research-answer mode.

    check_connection / check_audibility:
        Execute another recovery action.

    stop:
        End the interview.
    """

    if state.get("interview_complete"):
        return "stop"

    action = state.get("response_recovery_action")

    if action is None:
        return "confirmed"

    if action == "stop":
        return "stop"

    return "retry"


def route_after_consumer_response(
    state: InterviewState,
) -> str:
    """
    Route the consumer response according to the current
    interaction mode and deterministic runtime status.

    Deterministic runtime failures such as no_response and
    audio_issue bypass LLM response triage and go directly
    to response recovery.

    Audibility and connection confirmations go through the
    deterministic voice-confirmation node.
    """

    logger.info("debug_payload", extra={"parts": ["DEBUG route_after_consumer_response:", {         "response_status": state.get("response_status"),         "voice_interaction_mode": state.get("voice_interaction_mode"),         "latest_response": state.get("latest_response"),     }]})
    response_status = state.get("response_status")

    # ------------------------------------------------------------
    # Deterministic runtime failures
    # ------------------------------------------------------------

    if response_status in {
        "no_response",
        "audio_issue",
    }:
        return "response_recovery"

    # ------------------------------------------------------------
    # Voice confirmation mode
    # ------------------------------------------------------------

    mode = state.get(
        "voice_interaction_mode",
        "research_answer",
    )

    if mode in {
        "audibility_confirmation",
        "connection_confirmation",
    }:
        return "voice_confirmation"

    # ------------------------------------------------------------
    # Normal research-answer mode
    # ------------------------------------------------------------

    logger.info("DEBUG routing valid research response to response_triage")
    
    return "research_answer"


def route_after_response_triage(
    state: InterviewState,
) -> str:
    status = state.get("response_status")

    logger.info("debug_payload", extra={"parts": ["DEBUG route_after_response_triage:", {             "response_status": status,             "latest_response": state.get("latest_response"),             "response_recovery_action": state.get(                 "response_recovery_action"             ),         }]})
    if status == "valid":
        return "valid"

    return "recover"


def route_after_response_recovery(
    state: InterviewState,
) -> str:

    if state.get("interview_complete"):
        return "stop"

    return "retry"


# ============================================================
# GRAPH CONSTRUCTION
# ============================================================

def build_interview_graph(
    checkpointer=None,
):
    """
    Build the adaptive consumer interview graph.

    Meta Planner executes only once.

    Gap Analyzer executes after consumer evidence has
    been incorporated into state and subsequently drives
    adaptive proposal generation.
    """

    builder = StateGraph(InterviewState)

    # ========================================================
    # SESSION VALIDATOR
    # ========================================================
    
    builder.add_node(
        "session_validator",
        validate_research_session,
    )

    # ========================================================
    # INITIAL PLANNING
    # ========================================================

    builder.add_node(
        "meta_planner",
        timed_node("meta_planner", create_meta_plan),
    )

    # ========================================================
    # RESPONSE TRIAGE
    # ========================================================

    builder.add_node(
        "response_triage",
        triage_response,
    )

    # ========================================================
    # VALID-RESPONSE ACCEPTANCE
    # ========================================================

    builder.add_node(
        "response_acceptor",
        accept_valid_response,
    )

    # ========================================================
    # RECOVERY POLICY
    # ========================================================

    builder.add_node(
        "response_recovery",
        recover_from_response,
    )

    # ========================================================
    # RECOVERY EXECUTION
    # ========================================================

    builder.add_node(
        "execute_response_recovery",
        execute_response_recovery,
    )

    # ========================================================
    # PROPOSAL PIPELINE
    # ========================================================

    builder.add_node(
        "proposal_generator",
        timed_node("proposal_generator", generate_proposals),
    )

    builder.add_node(
        "proposal_evaluator",
        timed_node("proposal_evaluator", evaluate_proposals),
    )

    builder.add_node(
        "proposal_ranker",
        timed_node("proposal_ranker", rank_proposals),
    )

    builder.add_node(
        "decision_policy",
        timed_node("decision_policy", decide_next_action),
    )

    # ========================================================
    # QUESTION EXECUTION
    # ========================================================

    builder.add_node(
        "pre_question_snapshot",
        timed_node("pre_question_snapshot", capture_pre_question_snapshot),
    )

    builder.add_node(
        "executive_controller",
        timed_node("executive_controller", execute_decision),
    )

    # ========================================================
    # VOICE BOUNDARY
    # ========================================================

    builder.add_node(
        "question_delivery",
        timed_node("question_delivery", deliver_question),
    )

    builder.add_node(
        "consumer_response",
        receive_consumer_response,
    )

    builder.add_node(
        "voice_confirmation",
        voice_confirmation,
    )    

    # ========================================================
    # RESPONSE PROCESSING
    # ========================================================

    builder.add_node(
        "answer_decomposition",
        timed_node("answer_decomposition", decompose_answer),
    )

    builder.add_node(
        "state_updater",
        timed_node("state_updater", update_state),
    )

    # ========================================================
    # ADAPTIVE STATE ANALYSIS
    # ========================================================

    builder.add_node(
        "gap_analyzer",
        timed_node("gap_analyzer", analyze_gaps),
    )

    builder.add_node(
        "reflection",
        timed_node("reflection", reflect_on_response),
    )

    builder.add_node(
        "advance_iteration",
        timed_node("advance_iteration", advance_iteration),
    )

    # ========================================================
    # INITIAL FLOW
    # ========================================================

    builder.add_edge(
        START,
        "session_validator",
    )

    builder.add_conditional_edges(
        "session_validator",
        route_after_session_validation,
        {
            "continue": "meta_planner",
            "stop": END,
        },
    )

    builder.add_edge(
        "meta_planner",
        "proposal_generator",
    )

    # ========================================================
    # PROPOSAL PIPELINE
    # ========================================================

    builder.add_edge(
        "proposal_generator",
        "proposal_evaluator",
    )

    builder.add_edge(
        "proposal_evaluator",
        "proposal_ranker",
    )

    builder.add_edge(
        "proposal_ranker",
        "decision_policy",
    )

    # ========================================================
    # DECISION → QUESTION / STOP
    # ========================================================
    
    builder.add_conditional_edges(
        "decision_policy",
        route_after_decision,
        {
            "ask_question": "pre_question_snapshot",
            "stop": END,
        },
    )

    # ========================================================
    # SNAPSHOT MUST OCCUR BEFORE EXECUTION
    # ========================================================

    builder.add_edge(
        "pre_question_snapshot",
        "executive_controller",
    )

    # ========================================================
    # EXECUTIVE CONTROLLER
    # ========================================================

    builder.add_conditional_edges(
        "executive_controller",
        route_after_execution,
        {
            "ask_question": "question_delivery",
            "stop": END,
        },
    )

    # ========================================================
    # QUESTION DELIVERY
    # ========================================================

    builder.add_conditional_edges(
        "question_delivery",
        route_after_question_delivery,
        {
            "continue": "consumer_response",
            "stop": END,
        },
    )

    # ========================================================
    # CONSUMER RESPONSE
    # ========================================================

    builder.add_conditional_edges(
        "consumer_response",
        route_after_consumer_response,
        {
            "research_answer": "response_triage",
            "voice_confirmation": "voice_confirmation",
            "response_recovery": "response_recovery",
        },
    )
    
    builder.add_conditional_edges(
        "response_triage",
        route_after_response_triage,
        {
            "valid": "response_acceptor",
            "recover": "response_recovery",
        },
    )

    builder.add_edge(
        "response_acceptor",
        "answer_decomposition",
    )

    builder.add_edge(
        "response_recovery",
        "execute_response_recovery",
    )

    builder.add_conditional_edges(
        "execute_response_recovery",
        route_after_response_recovery,
        {
            "retry": "consumer_response",
            "stop": END,
        },
    )

    builder.add_conditional_edges(
        "voice_confirmation",
        route_after_voice_confirmation,
        {
            "confirmed": "consumer_response",
            "retry": "execute_response_recovery",
            "stop": END,
        },
    )

    # ========================================================
    # RESPONSE UNDERSTANDING
    # ========================================================

    builder.add_edge(
        "answer_decomposition",
        "state_updater",
    )

    # ========================================================
    # STATE → GAP ANALYSIS
    # ========================================================

    builder.add_edge(
        "state_updater",
        "gap_analyzer",
    )

    # ========================================================
    # GAP ANALYSIS → REFLECTION
    # ========================================================

    builder.add_edge(
        "gap_analyzer",
        "reflection",
    )

    # ========================================================
    # REFLECTION → NEXT TURN
    # ========================================================

    builder.add_edge(
        "reflection",
        "advance_iteration",
    )

    builder.add_edge(
        "advance_iteration",
        "proposal_generator",
    )

    # ========================================================
    # COMPILE
    # ========================================================

    return builder.compile(
        checkpointer=checkpointer,
    )
