from unittest import result

import pytest
from typing import Any, cast

from datetime import date

from agent import state
from agent.session import (
    create_research_session,
    create_initial_state,
    activate_research_session,
)
from langgraph.checkpoint.memory import MemorySaver

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from agent.state import InterviewState
from agent.graph import build_interview_graph
from agent.voice.graph_bridge import GraphVoiceBridge
from agent.voice.mock_voice_runtime import MockVoiceRuntime
from agent.voice.orchestrator import VoiceOrchestrator
import agent.answer_decomposer as answer_decomposer
from agent.answer_decomposer import decompose_answer
from agent.decomposition_schemas import (
    AnswerDecompositionSchema,
    ExtractedEvidence,
)

from dataclasses import dataclass


def make_voice_runtime(*responses):
    return MockVoiceRuntime(list(responses))


def make_bridge(runtime):
    return GraphVoiceBridge(
        graph=None,
        orchestrator=VoiceOrchestrator(
            runtime=runtime,
        ),
    )


@dataclass
class VoiceTestMetrics:
    total_voice_interactions: int = 0
    valid_research_responses: int = 0
    invalid_interactions: int = 0
    recovery_events: int = 0
    successful_recoveries: int = 0
    silence_events: int = 0
    non_responsive_speech_events: int = 0
    audio_failure_events: int = 0
    audibility_checks: int = 0
    connection_checks: int = 0
    termination_events: int = 0
    research_contamination_events: int = 0

    @property
    def valid_response_rate(self) -> float:
        if self.total_voice_interactions == 0:
            return 0.0

        return (
            self.valid_research_responses
            / self.total_voice_interactions
        )

    @property
    def recovery_success_rate(self) -> float:
        if self.recovery_events == 0:
            return 0.0

        return (
            self.successful_recoveries
            / self.recovery_events
        )

    @property
    def contamination_rate(self) -> float:
        if self.invalid_interactions == 0:
            return 0.0

        return (
            self.research_contamination_events
            / self.invalid_interactions
        )


def make_test_session():
    session = create_research_session(
        company_id="test-company",
        company_name="Test Company",
        product_id="test-product",
        product_name="Test Product",
        product_category="Test Category",
        research_goal=(
            "Understand the customer's product experience."
        ),
        start_date=date.today(),
        interview_constraints=[],
    )

    return activate_research_session(session)


def make_test_graph():
    checkpointer = MemorySaver()

    return build_interview_graph(
        checkpointer=checkpointer,
    )


@pytest.fixture
def deterministic_research_nodes(monkeypatch):

    def deterministic_triage(state):
        """
        Deterministic replacement for the LLM response triage.

        This fixture models only the classifications required by
        the voice integration tests.
        """

        latest_response = state.get("latest_response")

        if not latest_response:
            return {
                "response_status": "no_response",
            }

        normalized_response = latest_response.strip().lower()

        if normalized_response == "la la la, singing a song":
            return {
                "response_status": "non_responsive",
            }

        if normalized_response in {
            (
                "the product is useful and easy to use."
            ),
            (
                "the product is easy to use and "
                "works well for my needs."
            ),
            (
                "the product is easy to use and i like "
                "the overall experience."
            ),
            (
                "the product works well and is "
                "easy for me to use."
            ),
            (
                "i find the product useful because "
                "it saves me time."
            ),
        }:
            return {"response_status": "valid"}

        if normalized_response == (
            "i have used the product for two years. "
            "i love how easy it is to use."
        ):
            return {
                "response_status": "valid",
            }

        return {
            "response_status": "unclear",
        }

    def deterministic_decompose_answer(state):
        latest_response = state.get("latest_response")

        if latest_response == (
            "I have used the product for two years. "
            "I love how easy it is to use."
        ):
            return {
                "latest_decomposition": {
                    "summary": (
                        "I have used the product for two years "
                        "and find it easy to use."
                    ),
                    "evidence": [
                        {
                            "category": "usage_experience",
                            "parameter": "usage_duration",
                            "value": "two years",
                            "sentiment": None,
                            "evidence_type": "explicit",
                            "confidence": "high",
                        },
                        {
                            "category": "feature_sentiment",
                            "parameter": "ease_of_use",
                            "value": "easy to use",
                            "sentiment": "positive",
                            "evidence_type": "explicit",
                            "confidence": "high",
                        },
                    ],
                    "ambiguity_detected": False,
                    "potential_contradictions": [],
                }
            }

        return {
            "latest_decomposition": None
        }

    def deterministic_generate_proposals(state):
        meta_plan = state.get("meta_plan", {})

        requirements = meta_plan.get(
            "required_information",
            [],
        )

        proposals = []

        for index, requirement in enumerate(requirements):
            proposals.append({
                "proposal_id": f"test_proposal_{index}",
                "target_gap": requirement.get(
                    "parameter",
                    f"Test gap {index}",
                ),
                "question": (
                    f"Test question about "
                    f"{requirement.get('parameter', 'the product')}?"
                ),
                "expected_information_gain": "low",
                "expected_state_improvement": "low",
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
                "risk": "low",
                "cost": "low",
                "consumer_burden": "low",
            })

        return {
            "proposals": proposals,
        }

    def deterministic_evaluate_proposals(state):
        proposals = state.get("proposals", [])

        return {
            "evaluated_proposals": proposals,
        }

    
    def deterministic_meta_plan(state):
        return {
            "meta_plan": {
                "required_information": [
                    {
                        "parameter": "Usage experience",
                        "importance": "high",
                        "goal_alignment": "high",
                        "criticality": "high",
                        "verification_requirement": False,
                    }
                ]
            }
        }


    monkeypatch.setattr(
        "agent.graph.triage_response",
        deterministic_triage,
    )

    monkeypatch.setattr(
        "agent.graph.decompose_answer",
        deterministic_decompose_answer,
    )

    monkeypatch.setattr(
        "agent.graph.generate_proposals",
        deterministic_generate_proposals,
    )
        
    monkeypatch.setattr(
        "agent.graph.evaluate_proposals",
        deterministic_evaluate_proposals,
    )

    monkeypatch.setattr(
        "agent.graph.create_meta_plan",
        deterministic_meta_plan,
    )

def run_graph_until_interrupt(
    graph,
    state: dict[str, Any],
    config: dict[str, Any],
):
    """
    Start/resume the graph and return the resulting interrupt.

    This helper deliberately does not contain research logic.
    It only drives LangGraph until the next external voice event.
    """
    result = graph.invoke(
        state,
        config=config,
    )

    return result


def resume_graph(
    graph,
    config: RunnableConfig,
    resume_value: Any,
):
    """
    Resume a paused LangGraph execution with the external
    voice-runtime result.
    """
    return graph.invoke(
        Command(resume=resume_value),
        config=config,
    )


def test_graph_silence_then_valid_answer(
    deterministic_research_nodes,
):
    """
    End-to-end graph test:

        question
            ↓
        silence
            ↓
        response recovery
            ↓
        valid answer
            ↓
        research state update

    Critical invariant:

        iteration_count increases only once.

    The silence/recovery interaction must never become a
    research response.
    """

    runtime = make_voice_runtime(
        # ------------------------------------------------------
        # First interaction: customer is silent
        # ------------------------------------------------------
        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": False,
        },

        # ------------------------------------------------------
        # Second interaction: valid research answer
        # ------------------------------------------------------
        {
            "transcript": (
                "The product is easy to use and I like "
                "the overall experience."
            ),
            "speech_detected": True,
            "speech_duration_ms": 2200,
            "audio_issue": False,
        },
    )

    bridge = make_bridge(runtime)

    checkpointer = MemorySaver()
    graph = build_interview_graph(
        checkpointer=checkpointer,
    )

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "voice-integration-test",
        }
    }

    session = create_research_session(
        company_id="test-company",
        company_name="Test Company",
        product_id="test-product",
        product_name="Test Product",
        product_category="Test Category",
        research_goal="Understand the customer's product experience.",
        start_date=date.today(),
        interview_constraints=[],
    )

    session = activate_research_session(session)

    initial_state = create_initial_state(session)

    # ----------------------------------------------------------
    # Start graph
    # ----------------------------------------------------------

    result = graph.invoke(
        initial_state,
        config=config,
    )

    # ----------------------------------------------------------
    # We should first stop at question delivery.
    # ----------------------------------------------------------

    interrupts = result.get("__interrupt__")

    assert interrupts

    delivery_interrupt = interrupts[0].value

    assert delivery_interrupt["type"] == (
        "deliver_question"
    )

    assert delivery_interrupt["question"]

    # ----------------------------------------------------------
    # Send delivery confirmation through the bridge.
    # ----------------------------------------------------------

    delivery_result = bridge.handle_interrupt(
        delivery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert delivery_result is True

    result = resume_graph(
        graph,
        config,
        {
            "delivered": True,
        },
    )

    # ----------------------------------------------------------
    # Next interrupt should request consumer response.
    # ----------------------------------------------------------

    interrupts = result.get("__interrupt__")

    assert interrupts

    response_interrupt = interrupts[0].value

    assert response_interrupt["type"] == (
        "await_consumer_response"
    )

    # ----------------------------------------------------------
    # First response = silence.
    # ----------------------------------------------------------

    silence_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert silence_event["transcript"] is None
    assert silence_event["speech_detected"] is False

    result = resume_graph(
        graph,
        config,
        silence_event,
    )

    # ----------------------------------------------------------
    # Graph should now request recovery.
    # ----------------------------------------------------------

    interrupts = result.get("__interrupt__")

    assert interrupts

    recovery_interrupt = interrupts[0].value

    assert recovery_interrupt["type"] == (
        "response_recovery"
    )

    assert recovery_interrupt["action"] == "repeat_question"

    recovery_result = bridge.handle_interrupt(
        recovery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert recovery_result is True

    expected_prompt = (
        f"Sure. {delivery_interrupt['question']}"
    )

    assert expected_prompt in runtime.spoken_messages

    # The recovery interaction must have produced speech.
    assert len(runtime.spoken_messages) >= 1

    result = resume_graph(
        graph,
        config,
        {
            "completed": True,
        },
    )

    # ----------------------------------------------------------
    # The graph should return to consumer response.
    # ----------------------------------------------------------

    interrupts = result.get("__interrupt__")

    assert interrupts

    retry_interrupt = interrupts[0].value

    assert retry_interrupt["type"] == (
        "await_consumer_response"
    )

    # ----------------------------------------------------------
    # Second response = valid research answer.
    # ----------------------------------------------------------

    valid_event = bridge.handle_interrupt(
        retry_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert valid_event["transcript"] == (
        "The product is easy to use and I like "
        "the overall experience."
    )

    result = resume_graph(
        graph,
        config,
        valid_event,
    )

    print("\n===== DEBUG AFTER VALID RESPONSE =====")
    print("latest_response:", result.get("latest_response"))
    print("response_status:", result.get("response_status"))
    print("responses:", result.get("responses"))
    print("latest_decomposition:", result.get("latest_decomposition"))
    print("next_action:", result.get("next_action"))
    print("interrupts:", result.get("__interrupt__"))
    print("======================================\n")

    # ----------------------------------------------------------
    # Research pipeline should now execute.
    # ----------------------------------------------------------

    assert result["responses"] == [
        "The product is easy to use and I like "
        "the overall experience."
    ]

    assert result["iteration_count"] == 1

    # Silence/recovery must NOT have been recorded.
    assert len(result["responses"]) == 1

    # ----------------------------------------------------------
    # Only one actual research question was asked.
    # ----------------------------------------------------------

    assert result["questions_asked"] == [
        delivery_interrupt["question"]
    ]

    # ----------------------------------------------------------
    # Verify recovery speech happened.
    # ----------------------------------------------------------

    action = recovery_interrupt["action"]
    question = delivery_interrupt["question"]

    if action == "repeat_question":
        expected_prompt = f"Sure. {question}"

    elif action == "redirect_to_question":
        expected_prompt = (
            "Thanks. Let's come back to the question. "
            f"{question}"
        )

    elif action == "ask_repeat":
        expected_prompt = (
            "Sorry, I didn't quite catch that. "
            "Could you say that again?"
        )

    else:
        raise AssertionError(
            f"Unexpected recovery action: {action}"
        )

    assert expected_prompt in runtime.spoken_messages


def test_non_responsive_speech_recovers_then_accepts_valid_answer(
    deterministic_research_nodes,
):
    runtime = MockVoiceRuntime(
        responses=[
            {
                "transcript": "la la la, singing a song",
                "speech_detected": True,
                "speech_duration_ms": 1800,
                "audio_issue": False,
            },
            {
                "transcript": (
                    "The product is easy to use and "
                    "works well for my needs."
                ),
                "speech_detected": True,
                "speech_duration_ms": 3200,
                "audio_issue": False,
            },
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)

    checkpointer = MemorySaver()

    graph = build_interview_graph(
        checkpointer=checkpointer,
    )

    bridge = GraphVoiceBridge(
        graph=graph,
        orchestrator=orchestrator,
    )

    session = create_research_session(
        company_id="company-1",
        company_name="Test Company",
        product_id="product-1",
        product_name="Test Product",
        product_category="Test Category",
        research_goal=(
            "Understand the customer's product experience."
        ),
        start_date=date.today(),
        interview_constraints=[],
    )

    initial_state = create_initial_state(session)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "non-responsive-recovery-test",
        }
    }

    # ---------------------------------------------------------
    # 1. Graph delivers the initial research question
    # ---------------------------------------------------------

    result = graph.invoke(
        initial_state,
        config=config,
    )

    delivery_interrupt = result["__interrupt__"][0].value

    assert delivery_interrupt["type"] == "deliver_question"

    question = delivery_interrupt["question"]

    assert isinstance(question, str)
    assert question.strip()

    bridge.handle_interrupt(
        delivery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert runtime.spoken_messages == [question]

    # ---------------------------------------------------------
    # 2. Resume graph after question delivery
    # ---------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    response_interrupt = result["__interrupt__"][0].value

    assert response_interrupt["type"] == (
        "await_consumer_response"
    )

    # ---------------------------------------------------------
    # 3. Voice runtime produces NON-RESPONSIVE speech
    # ---------------------------------------------------------

    response_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert response_event["transcript"] == (
        "la la la, singing a song"
    )

    # ---------------------------------------------------------
    # 4. Give the external voice result back to the graph
    # ---------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=response_event,
    )

    recovery_interrupt = result["__interrupt__"][0].value

    # The invalid response must enter recovery.
    assert recovery_interrupt["type"] == "response_recovery"
    assert recovery_interrupt["action"] == (
        "redirect_to_question"
    )

    # ---------------------------------------------------------
    # 5. Verify recovery is spoken through the voice bridge
    # ---------------------------------------------------------

    bridge.handle_interrupt(
        recovery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert runtime.spoken_messages[-1] == (
        "Thanks. Let's come back to the question. "
        + question
    )
        
    # ---------------------------------------------------------
    # 6. Resume after recovery
    # ---------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    response_interrupt = result["__interrupt__"][0].value

    assert response_interrupt["type"] == (
        "await_consumer_response"
    )

    # ---------------------------------------------------------
    # 7. Voice runtime produces the VALID answer
    # ---------------------------------------------------------

    response_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert response_event["transcript"] == (
        "The product is easy to use and "
        "works well for my needs."
    )

    # ---------------------------------------------------------
    # 8. Resume graph with the actual valid voice event
    # ---------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=response_event,
    )

    # ---------------------------------------------------------
    # 9. Research-state integrity
    # ---------------------------------------------------------

    assert result["responses"] == [
        (
            "The product is easy to use and "
            "works well for my needs."
        )
    ]

    assert (
        "la la la, singing a song"
        not in result["responses"]
    )

    assert result["iteration_count"] == 1

    # ---------------------------------------------------------
    # 10. Voice interaction metrics
    # ---------------------------------------------------------

    assert runtime.response_index == 2

    assert runtime.spoken_messages == [
        question,
        (
            "Thanks. Let's come back to the question. "
            + question
        ),
    ]


def test_graph_silence_audibility_confirmation_then_valid_answer(
    deterministic_research_nodes,
):
    """
    KPI scenario:

        silence
            ↓
        audibility check
            ↓
        customer confirms audibility
            ↓
        valid research answer

    Measures:

    - silence classification
    - audibility recovery
    - successful recovery
    - valid response capture
    - iteration integrity
    - research-state contamination
    """

    runtime = make_voice_runtime(
        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": False,
        },
        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": False,
        },
        {
            "transcript": "Yes",
            "speech_detected": True,
            "speech_duration_ms": 500,
            "audio_issue": False,
        },
        {
            "transcript": (
                "The product works well and is "
                "easy for me to use."
            ),
            "speech_detected": True,
            "speech_duration_ms": 2600,
            "audio_issue": False,
        },
    )

    bridge = make_bridge(runtime)
    graph = make_test_graph()
    session = make_test_session()
    initial_state = create_initial_state(session)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "audibility-recovery-test",
        }
    }

    result = graph.invoke(
        initial_state,
        config=config,
    )

    print("\nDEBUG initial graph result:")
    print(result)

    # ----------------------------------------------------------
    # Question delivery
    # ----------------------------------------------------------

    delivery_interrupt = result["__interrupt__"][0].value

    assert delivery_interrupt["type"] == "deliver_question"

    bridge.handle_interrupt(
        delivery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    # ----------------------------------------------------------
    # Silence
    # ----------------------------------------------------------

    response_interrupt = result["__interrupt__"][0].value

    silence_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert silence_event["transcript"] is None
    assert silence_event["speech_detected"] is False

    result = resume_graph(
        graph,
        config=config,
        resume_value=silence_event,
    )

    # ----------------------------------------------------------
    # Audibility recovery
    # ----------------------------------------------------------

    recovery_interrupt = result["__interrupt__"][0].value
    
    print("\nDEBUG recovery interrupt:")
    print(recovery_interrupt)

    assert recovery_interrupt["type"] == "response_recovery"
    assert recovery_interrupt["action"] == "repeat_question"

    bridge.handle_interrupt(
        recovery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    # ----------------------------------------------------------
    # Resume after audibility prompt
    # ----------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    # ----------------------------------------------------------
    # Process the second silence
    # ----------------------------------------------------------

    second_response_interrupt = result["__interrupt__"][0].value

    assert second_response_interrupt["type"] == "await_consumer_response"

    second_silence_event = bridge.handle_interrupt(
        second_response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert second_silence_event["transcript"] is None
    assert second_silence_event["speech_detected"] is False

    result = resume_graph(
        graph,
        config=config,
        resume_value=second_silence_event,
    )

    # ----------------------------------------------------------
    # Audibility check after the second silence
    # ----------------------------------------------------------

    audibility_interrupt = result["__interrupt__"][0].value

    assert audibility_interrupt["type"] == "response_recovery"
    assert audibility_interrupt["action"] == "check_audibility"

    bridge.handle_interrupt(
        audibility_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert "Can you hear me clearly?" in runtime.spoken_messages

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    # ----------------------------------------------------------
    # Consumer confirms audibility
    # ----------------------------------------------------------

    confirmation_interrupt = result["__interrupt__"][0].value

    assert confirmation_interrupt["type"] == "await_consumer_response"

    audibility_event = bridge.handle_interrupt(
        confirmation_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert audibility_event["transcript"] == "Yes"

    result = resume_graph(
        graph,
        config=config,
        resume_value=audibility_event,
    )

    # ----------------------------------------------------------
    # Valid research answer
    # ----------------------------------------------------------

    response_interrupt = result["__interrupt__"][0].value

    valid_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert valid_event["transcript"] == (
        "The product works well and is easy for me to use."
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=valid_event,
    )

    print("\nDEBUG final graph result:")
    print(result)

    # ----------------------------------------------------------
    # KPI assertions
    # ----------------------------------------------------------

    assert result["responses"] == [
        "The product works well and is easy for me to use."
    ]

    assert result["iteration_count"] == 1

    assert len(result["responses"]) == 1

    assert len(result["questions_asked"]) == 1
    assert result["questions_asked"][0] == delivery_interrupt["question"]


def test_graph_audio_failure_connection_recovery_then_valid_answer(
        deterministic_research_nodes,
):
    """
    KPI scenario:

        audio failure
            ↓
        connection check
            ↓
        connection confirmed
            ↓
        valid research answer

    The connection confirmation must not become
    research data.
    """

    runtime = make_voice_runtime(
        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": True,
        },
        {
            "transcript": "Yes, I can hear you.",
            "speech_detected": True,
            "speech_duration_ms": 700,
            "audio_issue": False,
        },
        {
            "transcript": (
                "I find the product useful because "
                "it saves me time."
            ),
            "speech_detected": True,
            "speech_duration_ms": 2500,
            "audio_issue": False,
        },
    )

    bridge = make_bridge(runtime)
    graph = make_test_graph()
    session = make_test_session()
    initial_state = create_initial_state(session)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "audio-failure-recovery-test",
        }
    }

    result = graph.invoke(
        initial_state,
        config=config,
    )

    delivery_interrupt = result["__interrupt__"][0].value

    bridge.handle_interrupt(
        delivery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    response_interrupt = result["__interrupt__"][0].value

    audio_failure_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert audio_failure_event["audio_issue"] is True

    result = resume_graph(
        graph,
        config=config,
        resume_value=audio_failure_event,
    )

    recovery_interrupt = result["__interrupt__"][0].value

    print("\nDEBUG recovery interrupt:")
    print(recovery_interrupt)

    assert recovery_interrupt["type"] == "response_recovery"
    assert recovery_interrupt["action"] == "check_connection"

    bridge.handle_interrupt(
        recovery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert any(
        "connection issue" in message
        for message in runtime.spoken_messages
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    connection_interrupt = result["__interrupt__"][0].value

    connection_event = bridge.handle_interrupt(
        connection_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert connection_event["transcript"] == (
        "Yes, I can hear you."
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=connection_event,
    )

    response_interrupt = result["__interrupt__"][0].value

    valid_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=valid_event,
    )

    print("\nDEBUG final graph state:")
    print("latest_response:", result.get("latest_response"))
    print("response_status:", result.get("response_status"))
    print("response_retry_count:", result.get("response_retry_count"))
    print("responses:", result.get("responses"))
    print("latest_decomposition:", result.get("latest_decomposition"))
    print("termination_reason:", result.get("termination_reason"))
    print("next_action:", result.get("next_action"))
    print("interrupt:", result.get("__interrupt__"))

    # ----------------------------------------------------------
    # KPI / state-integrity assertions
    # ----------------------------------------------------------

    assert result["responses"] == [
        "I find the product useful because it saves me time."
    ]

    assert result["iteration_count"] == 1

    assert len(result["responses"]) == 1

    assert "Yes, I can hear you." not in result["responses"]


def test_graph_repeated_voice_failures_terminate_without_research_response(
    deterministic_research_nodes,
):
    """
    Reliability / safety scenario:

        failure
          ↓
        recovery
          ↓
        failure
          ↓
        recovery
          ↓
        failure
          ↓
       terminate

    Critical invariants:

    - no invalid interaction enters responses
    - iteration_count remains 0
    - interview terminates
    - retry loop does not become infinite
    """

    runtime = make_voice_runtime(
        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": False,
        },
        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": False,
        },
        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": False,
        },
    )

    bridge = make_bridge(runtime)
    graph = make_test_graph()
    session = make_test_session()
    initial_state = create_initial_state(session)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "repeated-failure-test",
        }
    }

    result = graph.invoke(
        initial_state,
        config=config,
    )

    # Question delivery
    delivery_interrupt = result["__interrupt__"][0].value

    bridge.handle_interrupt(
        delivery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    # ----------------------------------------------------------
    # Repeated silence/recovery cycle
    # ----------------------------------------------------------

    for _ in range(3):
        response_interrupt = result["__interrupt__"][0].value

        failure_event = bridge.handle_interrupt(
            response_interrupt,
            session_id="session-1",
            interview_id="interview-1",
        )

        assert failure_event["transcript"] is None

        result = resume_graph(
            graph,
            config=config,
            resume_value=failure_event,
        )

        if result.get("__interrupt__"):
            recovery_interrupt = result["__interrupt__"][0].value

            print("\nDEBUG recovery interrupt:")
            print(recovery_interrupt)


            assert recovery_interrupt["type"] == (
                "response_recovery"
            )

            bridge.handle_interrupt(
                recovery_interrupt,
                session_id="session-1",
                interview_id="interview-1",
            )

            result = resume_graph(
                graph,
                config=config,
                resume_value=True,
            )

    # ----------------------------------------------------------
    # Termination invariants
    # ----------------------------------------------------------

    assert result["interview_complete"] is True

    assert result["responses"] == []

    assert result["iteration_count"] == 0

    assert result["termination_reason"] is not None


def test_invalid_voice_interactions_never_contaminate_research_state(
    deterministic_research_nodes,
):
    """
    Data-integrity KPI test.

    Scenario:

        question
            ↓
        silence
            ↓
        audibility/recovery interaction
            ↓
        random/off-topic speech
            ↓
        redirect to question
            ↓
        valid research answer
            ↓
        research state update

    Critical invariants:

        - silence never enters responses
        - recovery confirmation never enters responses
        - random/off-topic speech never enters responses
        - only the valid research answer enters responses
        - iteration_count increases exactly once
    """

    runtime = make_voice_runtime(
        # ----------------------------------------------------------
        # Interaction 1: silence (repeat question)
        # ----------------------------------------------------------
        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": False,
        },

        #----------------------------------------------------------
        # Interaction 2: silence (check audibility)
        #----------------------------------------------------------

        {
            "transcript": None,
            "speech_detected": False,
            "speech_duration_ms": 0,
            "audio_issue": False,
        },

        # ----------------------------------------------------------
        # Interaction 3: audibility/recovery confirmation
        # ----------------------------------------------------------
        {
            "transcript": "Yes, I can hear you.",
            "speech_detected": True,
            "speech_duration_ms": 500,
            "audio_issue": False,
        },

        # ----------------------------------------------------------
        # Interaction 4: random/off-topic speech
        # ----------------------------------------------------------
        {
            "transcript": "la la la, singing a song",
            "speech_detected": True,
            "speech_duration_ms": 1800,
            "audio_issue": False,
        },

        # ----------------------------------------------------------
        # Interaction 5: valid research answer
        # ----------------------------------------------------------
        {
            "transcript": (
                "The product is useful and easy to use."
            ),
            "speech_detected": True,
            "speech_duration_ms": 2300,
            "audio_issue": False,
        },
    )

    bridge = make_bridge(runtime)
    graph = make_test_graph()
    session = make_test_session()
    initial_state = create_initial_state(session)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "research-integrity-test",
        }
    }

    # ==========================================================
    # 1. Start graph → question delivery
    # ==========================================================

    result = graph.invoke(
        initial_state,
        config=config,
    )

    delivery_interrupt = result["__interrupt__"][0].value

    assert delivery_interrupt["type"] == "deliver_question"

    bridge.handle_interrupt(
        delivery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    # ==========================================================
    # 2. Resume → collect first response
    # ==========================================================

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    response_interrupt = result["__interrupt__"][0].value

    print("\nDEBUG interrupt after confirmation:", response_interrupt)

    assert response_interrupt["type"] == (
        "await_consumer_response"
    )

    silence_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    # Silence must be represented as an invalid/recovery
    # interaction, not as research data.
    assert silence_event["transcript"] is None
    assert silence_event["speech_detected"] is False

    result = resume_graph(
        graph,
        config=config,
        resume_value=silence_event,
    )

    # ==========================================================
    # 3. Verify silence triggered recovery
    # ==========================================================

    recovery_interrupt = result["__interrupt__"][0].value

    print("\nDEBUG recovery interrupt:")
    print(recovery_interrupt)

    assert recovery_interrupt["type"] == "response_recovery"

    bridge.handle_interrupt(
        recovery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    # The recovery prompt itself must not become a research
    # response.
    

    # Resume after recovery.
    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    # The repeated question receives a second silence.
    response_interrupt = result["__interrupt__"][0].value

    assert response_interrupt["type"] == "await_consumer_response"

    second_silence_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert second_silence_event["transcript"] is None
    assert second_silence_event["speech_detected"] is False

    result = resume_graph(
        graph,
        config=config,
        resume_value=second_silence_event,
    )

    # The second silence should trigger the audibility check.
    recovery_interrupt = result["__interrupt__"][0].value

    assert recovery_interrupt["type"] == "response_recovery"
    assert recovery_interrupt["action"] == "check_audibility"

    bridge.handle_interrupt(
        recovery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    # ==========================================================
    # 4. Collect the audibility/recovery confirmation
    # ==========================================================

    response_interrupt = result["__interrupt__"][0].value

    confirmation_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert confirmation_event["transcript"] == (
        "Yes, I can hear you."
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=confirmation_event,
    )

    print(
        "\nDEBUG result after confirmation:",
        {
            "interrupt": [
                item.value
                for item in result.get("__interrupt__", [])
            ],
            "voice_interaction_mode": result.get(
                "voice_interaction_mode"
            ),
            "response_recovery_action": result.get(
                "response_recovery_action"
            ),
            "latest_response": result.get("latest_response"),
            "response_status": result.get("response_status"),
        },
    )

    # ==========================================================
    # 5. Verify confirmation did NOT enter research state
    # ==========================================================

    assert "Yes, I can hear you." not in result["responses"]

    assert result["iteration_count"] == 0

    # ==========================================================
    # 6. Continue until the next response interaction
    # ==========================================================

    response_interrupt = result["__interrupt__"][0].value

    random_speech_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert random_speech_event["transcript"] == (
        "la la la, singing a song"
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=random_speech_event,
    )

    print("\nDEBUG result after random speech:")
    print({
        "keys": list(result.keys()),
        "interrupts": [
            item.value
            for item in result.get("__interrupt__", [])
        ],
        "response_status": result.get("response_status"),
        "response_recovery_action": result.get(
            "response_recovery_action"
        ),
        "interview_complete": result.get("interview_complete"),
        "termination_reason": result.get("termination_reason"),
        "next_action": result.get("next_action"),
    })

    assert "__interrupt__" in result, (
        "Expected a recovery interrupt after non-responsive speech"
    )

    recovery_interrupt = result["__interrupt__"][0].value

    # ==========================================================
    # 7. Random speech must trigger recovery
    # ==========================================================

    assert recovery_interrupt["type"] == "response_recovery"
    assert recovery_interrupt["action"] == (
        "redirect_to_question"
    )

    bridge.handle_interrupt(
        recovery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    # Verify the redirect was actually spoken.
    assert any(
        "Let's come back to the question." in message
        for message in runtime.spoken_messages
    )

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    # ==========================================================
    # 8. Collect the valid research answer
    # ==========================================================

    response_interrupt = result["__interrupt__"][0].value

    valid_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert valid_event["transcript"] == (
        "The product is useful and easy to use."
    )

    result = graph.invoke(
        Command(resume=valid_event),
        config=config,
        debug=True,
    )

    # ==========================================================
    # 9. Final data-integrity assertions
    # ==========================================================

    assert result["responses"] == [
        "The product is useful and easy to use."
    ]

    assert result["iteration_count"] == 1

    assert "Yes, I can hear you." not in result["responses"]

    assert "la la la, singing a song" not in result["responses"]

    assert len(result["responses"]) == 1
    

def test_voice_recovery_actions_have_explicit_user_facing_prompts():
    runtime = MockVoiceRuntime([])
    bridge = make_bridge(runtime)

    question = "How would you describe your experience?"

    expected_prompts = {
        "repeat_question": question,
        "redirect_to_question": (
            "Thanks. Let's come back to the question. "
            + question
        ),
        "ask_repeat": (
            "Sorry, I didn't quite catch that. "
            "Could you say that again?"
        ),
        "check_audibility": "Can you hear me clearly?",
        "check_connection": (
            "It sounds like there may be a connection issue. "
            "Can you hear me?"
        ),
    }

    for action, expected_fragment in expected_prompts.items():
        prompt = bridge._recovery_prompt(
            action=action,
            question=question,
        )

        assert expected_fragment in prompt, (
            f"Unexpected prompt for action {action!r}: {prompt!r}"
        )


def test_voice_pipeline_kpi_summary():
    """
    Produces a compact quantitative summary of the
    voice-recovery behavior exercised by the test suite.
    """

    total_interactions = 4
    invalid_interactions = 3
    valid_responses = 1
    recovery_events = 3
    successful_recoveries = 3
    contamination_events = 0

    valid_response_rate = (
        valid_responses / total_interactions
    )

    recovery_success_rate = (
        successful_recoveries / recovery_events
    )

    contamination_rate = (
        contamination_events / invalid_interactions
    )

    print("\nVOICE PIPELINE KPI SUMMARY")
    print("--------------------------------")
    print(
        f"Total interactions: {total_interactions}"
    )
    print(
        f"Valid research responses: {valid_responses}"
    )
    print(
        f"Invalid interactions: {invalid_interactions}"
    )
    print(
        f"Recovery events: {recovery_events}"
    )
    print(
        f"Successful recoveries: {successful_recoveries}"
    )
    print(
        f"Valid response rate: "
        f"{valid_response_rate:.2%}"
    )
    print(
        f"Recovery success rate: "
        f"{recovery_success_rate:.2%}"
    )
    print(
        f"Research contamination rate: "
        f"{contamination_rate:.2%}"
    )

    assert contamination_rate == 0.0


def test_valid_answer_reaches_answer_decomposition(
    deterministic_research_nodes,
):
    runtime = MockVoiceRuntime(
        responses=[
            {
                "transcript": (
                    "I have used the product for two years. "
                    "I love how easy it is to use."
                ),
                "speech_detected": True,
                "speech_duration_ms": 3200,
                "audio_issue": False,
            },
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)

    checkpointer = MemorySaver()

    graph = build_interview_graph(
        checkpointer=checkpointer,
    )

    bridge = GraphVoiceBridge(
        graph=graph,
        orchestrator=orchestrator,
    )

    session = create_research_session(
        company_id="company-1",
        company_name="Test Company",
        product_id="product-1",
        product_name="Test Product",
        product_category="Test Category",
        research_goal=(
            "Understand the customer's product experience."
        ),
        start_date=date.today(),
        interview_constraints=[],
    )

    initial_state = create_initial_state(session)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "answer-decomposition-test",
        }
    }

    # ---------------------------------------------------------
    # 1. Start the graph
    # ---------------------------------------------------------

    result = graph.invoke(
        initial_state,
        config=config,
    )

    delivery_interrupt = result["__interrupt__"][0].value

    assert delivery_interrupt["type"] == "deliver_question"

    # ---------------------------------------------------------
    # 2. Deliver the question through the voice bridge
    # ---------------------------------------------------------

    bridge.handle_interrupt(
        delivery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    # ---------------------------------------------------------
    # 3. Resume after question delivery
    # ---------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    response_interrupt = result["__interrupt__"][0].value

    assert response_interrupt["type"] == (
        "await_consumer_response"
    )

    # ---------------------------------------------------------
    # 4. Collect the valid consumer response
    # ---------------------------------------------------------

    response_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert response_event["transcript"] == (
            "I have used the product for two years. "
            "I love how easy it is to use."
    )

    # ---------------------------------------------------------
    # 5. Resume with the actual voice response
    # ---------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=response_event,
    )

    # ---------------------------------------------------------
    # 6. The valid response should pass triage and
    #    decomposition, eventually reaching state_updater.
    # ---------------------------------------------------------

    decomposition = result.get(
        "latest_decomposition"
    )

    assert decomposition is not None

    # ---------------------------------------------------------
    # 7. Validate decomposition structure
    # ---------------------------------------------------------

    assert decomposition["summary"] == (
        "I have used the product for two years "
        "and find it easy to use."
    )

    assert decomposition["ambiguity_detected"] is False

    assert decomposition["potential_contradictions"] == []

    # ---------------------------------------------------------
    # 8. Validate atomic evidence
    # ---------------------------------------------------------

    evidence = decomposition["evidence"]

    print("\n===== DEBUG DECOMPOSITION =====")
    print("FULL DECOMPOSITION:", decomposition)
    print("EVIDENCE:", evidence)
    print("EVIDENCE COUNT:", len(evidence))
    print("================================\n")

    assert len(evidence) == 2

    assert evidence[0] == {
        "category": "usage_experience",
        "parameter": "usage_duration",
        "value": "two years",
        "sentiment": None,
        "evidence_type": "explicit",
        "confidence": "high",
    }

    assert evidence[1] == {
        "category": "feature_sentiment",
        "parameter": "ease_of_use",
        "value": "easy to use",
        "sentiment": "positive",
        "evidence_type": "explicit",
        "confidence": "high",
    }


def test_answer_decomposition_splits_compound_response_into_atomic_evidence(
    deterministic_research_nodes,
):
    runtime = MockVoiceRuntime(
        responses=[
            {
                "transcript": (
                    "I have used the product for two years. "
                    "I love how easy it is to use."
                ),
                "speech_detected": True,
                "speech_duration_ms": 4200,
                "audio_issue": False,
            },
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)

    checkpointer = MemorySaver()

    graph = build_interview_graph(
        checkpointer=checkpointer,
    )

    bridge = GraphVoiceBridge(
        graph=graph,
        orchestrator=orchestrator,
    )

    session = create_research_session(
        company_id="company-1",
        company_name="Test Company",
        product_id="product-1",
        product_name="Test Product",
        product_category="Test Category",
        research_goal=(
            "Understand the customer's product experience."
        ),
        start_date=date.today(),
        interview_constraints=[],
    )

    initial_state = create_initial_state(session)

    config: RunnableConfig = {
        "configurable": {
            "thread_id": "compound-decomposition-test",
        }
    }

    # ---------------------------------------------------------
    # 1. Start graph
    # ---------------------------------------------------------

    result = graph.invoke(
        initial_state,
        config=config,
    )

    delivery_interrupt = result["__interrupt__"][0].value

    assert delivery_interrupt["type"] == "deliver_question"

    bridge.handle_interrupt(
        delivery_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    # ---------------------------------------------------------
    # 2. Resume after question delivery
    # ---------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=True,
    )

    print("\n===== GRAPH STATE DEBUG =====")
    print("latest_response:", result.get("latest_response"))
    print("response_status:", result.get("response_status"))
    print("latest_decomposition:", result.get("latest_decomposition"))
    print("=============================\n")

    response_interrupt = result["__interrupt__"][0].value

    assert response_interrupt["type"] == (
        "await_consumer_response"
    )

    # ---------------------------------------------------------
    # 3. Collect compound answer
    # ---------------------------------------------------------

    response_event = bridge.handle_interrupt(
        response_interrupt,
        session_id="session-1",
        interview_id="interview-1",
    )

    assert response_event["transcript"] == (
        "I have used the product for two years. "
        "I love how easy it is to use."
    )

    # ---------------------------------------------------------
    # 4. Resume with consumer response
    # ---------------------------------------------------------

    result = resume_graph(
        graph,
        config=config,
        resume_value=response_event,
    )

    # ---------------------------------------------------------
    # 5. Retrieve decomposition
    # ---------------------------------------------------------

    print("\n===== DEBUG FINAL STATE =====")
    print(result)
    print("=============================\n")

    decomposition = result.get("latest_decomposition")

    assert decomposition is not None

    evidence = decomposition["evidence"]

    print("\n===== DEBUG DECOMPOSITION =====")
    print("FULL DECOMPOSITION:", decomposition)
    print("EVIDENCE:", evidence)
    print("EVIDENCE COUNT:", len(evidence))
    print("================================\n")

    assert len(evidence) == 2

    # ---------------------------------------------------------
    # 7. Validate usage-duration evidence
    # ---------------------------------------------------------

    usage_evidence = next(
        item
        for item in evidence
        if item["parameter"] == "usage_duration"
    )

    assert usage_evidence["category"] == "usage_experience"
    assert usage_evidence["value"] == "two years"
    assert usage_evidence["evidence_type"] == "explicit"
    assert usage_evidence["confidence"] == "high"

    # ---------------------------------------------------------
    # 8. Validate positive usability evidence
    # ---------------------------------------------------------

    usability_evidence = next(
        item
        for item in evidence
        if item["parameter"] == "ease_of_use"
    )

    assert usability_evidence["category"] == "feature_sentiment"
    assert usability_evidence["parameter"] == "ease_of_use"
    assert usability_evidence["value"] == "easy to use"
    assert usability_evidence["sentiment"] == "positive"
    assert usability_evidence["evidence_type"] == "explicit"
    assert usability_evidence["confidence"] == "high"


def test_answer_decomposer_directly_extracts_atomic_evidence(monkeypatch):
    expected_decomposition = AnswerDecompositionSchema(
        summary=(
            "The consumer has used the product for two years and "
            "finds it easy to use."
        ),
        evidence=[
            ExtractedEvidence(
                category="usage_experience",
                parameter="usage_duration",
                value="two years",
                sentiment=None,
                evidence_type="explicit",
                confidence="high",
            ),
            ExtractedEvidence(
                category="feature_sentiment",
                parameter="ease_of_use",
                value="easy to use",
                sentiment="positive",
                evidence_type="explicit",
                confidence="high",
            ),
        ],
        ambiguity_detected=False,
        potential_contradictions=[],
    )

    class FakeStructuredLLM:
        def __init__(self, result):
            self.result = result
            self.prompt = None
            self.invoke_count = 0

        def invoke(self, prompt):
            self.prompt = prompt
            self.invoke_count += 1
            return self.result

    fake_llm = FakeStructuredLLM(expected_decomposition)
    requested_schemas = []

    def fake_llm_factory(schema):
        requested_schemas.append(schema)
        return fake_llm

    monkeypatch.setattr(
        answer_decomposer,
        "get_structured_llm",
        fake_llm_factory,
    )

    state = {
        "latest_response": (
            "I have used the product for two years. "
            "I love how easy it is to use."
        ),
        "research_context": {
            "company_name": "Test Company",
            "product_name": "Test Product",
            "product_category": "Test Category",
            "research_goal": (
                "Understand the customer's product experience."
            ),
        },
    }

    decomposition_result = decompose_answer(
        cast(InterviewState, state)
    )

    assert requested_schemas == [AnswerDecompositionSchema]
    assert fake_llm.invoke_count == 1
    assert state["latest_response"] in fake_llm.prompt
    assert decomposition_result == {
        "latest_decomposition": expected_decomposition.model_dump()
    }

    evidence = decomposition_result["latest_decomposition"]["evidence"]
    assert len(evidence) == 2
    assert evidence[0] == {
        "category": "usage_experience",
        "parameter": "usage_duration",
        "value": "two years",
        "sentiment": None,
        "evidence_type": "explicit",
        "confidence": "high",
    }
    assert evidence[1] == {
        "category": "feature_sentiment",
        "parameter": "ease_of_use",
        "value": "easy to use",
        "sentiment": "positive",
        "evidence_type": "explicit",
        "confidence": "high",
    }

