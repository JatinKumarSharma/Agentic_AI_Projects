from copy import deepcopy

from agent.voice.mock_voice_runtime import MockVoiceRuntime
from agent.voice.orchestrator import VoiceOrchestrator


def make_state():
    return {
        "session_id": "session-test",
        "interview_id": "interview-test",

        "current_question": (
            "What do you like most about the product?"
        ),

        "voice_interaction_mode": "research_answer",

        "latest_response": None,
        "responses": [],

        "response_status": None,
        "response_recovery_action": None,
        "response_retry_count": 0,

        "iteration_count": 0,

        "interview_complete": False,
        "next_action": "ask_question",
    }

def accept_valid_response(state, transcript):
    state["latest_response"] = transcript
    state["responses"].append(transcript)
    state["response_status"] = "valid"
    state["response_recovery_action"] = None
    state["response_retry_count"] = 0

    return state


def advance_iteration(state):
    state["iteration_count"] += 1
    return state

def recover_response(state):
    status = state["response_status"]
    retry_count = state["response_retry_count"]

    if status == "valid":
        return "continue_research"

    if retry_count >= 2:
        state["interview_complete"] = True
        state["next_action"] = "stop"
        return "stop"

    if status == "no_response":
        if retry_count == 0:
            action = "repeat_question"
        else:
            action = "check_audibility"

    elif status in {
        "unrelated",
        "non_responsive",
    }:
        action = "redirect_to_question"

    elif status == "unclear":
        action = "ask_repeat"

    elif status == "audio_issue":
        action = "check_connection"

    else:
        action = "ask_repeat"

    state["response_retry_count"] += 1
    state["response_recovery_action"] = action

    return action

def collect_response(
    orchestrator: VoiceOrchestrator,
    state,
):
    event = orchestrator.collect_response(
        session_id=state["session_id"],
        interview_id=state["interview_id"],
        question=state["current_question"],
        interaction_mode=state[
            "voice_interaction_mode"
        ],
    )

    return event

def test_normal_answer():
    runtime = MockVoiceRuntime(
        [
            {
                "transcript": (
                    "I really like the camera."
                ),
                "speech_detected": True,
                "speech_duration_ms": 1800,
                "audio_issue": False,
            }
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)
    state = make_state()

    event = collect_response(
        orchestrator,
        state,
    )

    assert event["transcript"] == (
        "I really like the camera."
    )

    assert event["interaction_mode"] == (
        "research_answer"
    )

    accept_valid_response(
        state,
        event["transcript"],
    )

    advance_iteration(state)

    assert state["responses"] == [
        "I really like the camera."
    ]

    assert state["iteration_count"] == 1

    assert state["response_retry_count"] == 0

def test_silence_then_repeat_then_valid_answer():

    runtime = MockVoiceRuntime(
        [
            {
                "transcript": None,
                "speech_detected": False,
                "speech_duration_ms": 0,
                "audio_issue": False,
            },
            {
                "transcript": (
                    "I like the battery life."
                ),
                "speech_detected": True,
                "speech_duration_ms": 1600,
                "audio_issue": False,
            },
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)
    state = make_state()

    # First attempt
    event = collect_response(
        orchestrator,
        state,
    )

    assert event["transcript"] is None

    state["response_status"] = "no_response"

    action = recover_response(state)

    assert action == "repeat_question"

    # Critical invariant:
    assert state["responses"] == []
    assert state["iteration_count"] == 0

    # Second attempt
    state["voice_interaction_mode"] = (
        "research_answer"
    )

    event = collect_response(
        orchestrator,
        state,
    )

    assert event["transcript"] == (
        "I like the battery life."
    )

    accept_valid_response(
        state,
        event["transcript"],
    )

    advance_iteration(state)

    assert state["responses"] == [
        "I like the battery life."
    ]

    assert state["iteration_count"] == 1

def test_non_responsive_speech_then_valid_answer():

    runtime = MockVoiceRuntime(
        [
            {
                "transcript": (
                    "la la la la la..."
                ),
                "speech_detected": True,
                "speech_duration_ms": 2500,
                "audio_issue": False,
            },
            {
                "transcript": (
                    "I use it mainly for photography."
                ),
                "speech_detected": True,
                "speech_duration_ms": 2100,
                "audio_issue": False,
            },
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)
    state = make_state()

    # Singing / non-responsive speech
    event = collect_response(
        orchestrator,
        state,
    )

    assert event["transcript"] == (
        "la la la la la..."
    )

    # Triage result is deliberately scripted.
    state["latest_response"] = event["transcript"]
    state["response_status"] = "non_responsive"

    action = recover_response(state)

    assert action == "redirect_to_question"

    # Invalid speech must never enter research responses.
    assert state["responses"] == []
    assert state["iteration_count"] == 0

    # Valid answer
    state["voice_interaction_mode"] = (
        "research_answer"
    )

    event = collect_response(
        orchestrator,
        state,
    )

    accept_valid_response(
        state,
        event["transcript"],
    )

    advance_iteration(state)

    assert state["responses"] == [
        "I use it mainly for photography."
    ]

    assert state["iteration_count"] == 1

def test_silence_then_audibility_confirmation():

    runtime = MockVoiceRuntime(
        [
            {
                "transcript": None,
                "speech_detected": False,
                "speech_duration_ms": 0,
                "audio_issue": False,
            },
            {
                "transcript": "Yes, I can hear you.",
                "speech_detected": True,
                "speech_duration_ms": 700,
                "audio_issue": False,
            },
            {
                "transcript": (
                    "I like how easy the product is to use."
                ),
                "speech_detected": True,
                "speech_duration_ms": 1900,
                "audio_issue": False,
            },
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)
    state = make_state()

    # Attempt 1: silence
    event = collect_response(
        orchestrator,
        state,
    )

    assert event["transcript"] is None

    state["response_status"] = "no_response"

    action = recover_response(state)

    assert action == "repeat_question"

    assert state["iteration_count"] == 0
    assert state["responses"] == []

    # In a real graph, a second silence would trigger
    # audibility confirmation.
    state["response_retry_count"] = 1
    state["response_status"] = "no_response"

    action = recover_response(state)

    assert action == "check_audibility"

    state["voice_interaction_mode"] = (
        "audibility_confirmation"
    )

    # "Yes" is a recovery response, NOT a research answer.
    event = collect_response(
        orchestrator,
        state,
    )

    assert event["interaction_mode"] == (
        "audibility_confirmation"
    )

    assert event["transcript"] == (
        "Yes, I can hear you."
    )

    # Confirmation succeeds.
    state["voice_interaction_mode"] = (
        "research_answer"
    )

    state["latest_response"] = None
    state["response_status"] = None

    # The confirmation must NOT affect research state.
    assert state["responses"] == []
    assert state["iteration_count"] == 0

    # Now collect the actual research answer.
    event = collect_response(
        orchestrator,
        state,
    )

    assert event["interaction_mode"] == (
        "research_answer"
    )

    accept_valid_response(
        state,
        event["transcript"],
    )

    advance_iteration(state)

    assert state["responses"] == [
        "I like how easy the product is to use."
    ]

    assert state["iteration_count"] == 1

def test_audio_failure_then_connection_recovery():

    runtime = MockVoiceRuntime(
        [
            {
                "transcript": None,
                "speech_detected": False,
                "speech_duration_ms": 0,
                "audio_issue": True,
            },
            {
                "transcript": "Yes, I can hear you now.",
                "speech_detected": True,
                "speech_duration_ms": 700,
                "audio_issue": False,
            },
            {
                "transcript": (
                    "The design is what I like most."
                ),
                "speech_detected": True,
                "speech_duration_ms": 1700,
                "audio_issue": False,
            },
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)
    state = make_state()

    # Audio failure
    event = collect_response(
        orchestrator,
        state,
    )

    assert event["audio_issue"] is True
    assert event["transcript"] is None

    state["response_status"] = "audio_issue"

    action = recover_response(state)

    assert action == "check_connection"

    assert state["responses"] == []
    assert state["iteration_count"] == 0

    # Connection confirmation
    state["voice_interaction_mode"] = (
        "connection_confirmation"
    )

    event = collect_response(
        orchestrator,
        state,
    )

    assert event["interaction_mode"] == (
        "connection_confirmation"
    )

    assert event["transcript"] == (
        "Yes, I can hear you now."
    )

    # Confirmation doesn't count as research.
    assert state["responses"] == []
    assert state["iteration_count"] == 0

    # Return to research mode.
    state["voice_interaction_mode"] = (
        "research_answer"
    )

    event = collect_response(
        orchestrator,
        state,
    )

    accept_valid_response(
        state,
        event["transcript"],
    )

    advance_iteration(state)

    assert state["responses"] == [
        "The design is what I like most."
    ]

    assert state["iteration_count"] == 1

def test_repeated_failures_terminate():

    runtime = MockVoiceRuntime(
        [
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
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)
    state = make_state()

    for _ in range(3):

        if state["interview_complete"]:
            break

        event = collect_response(
            orchestrator,
            state,
        )

        assert event["transcript"] is None

        state["response_status"] = "no_response"

        action = recover_response(state)

        if action == "stop":
            break

    assert state["interview_complete"] is True
    assert state["next_action"] == "stop"

    assert state["responses"] == []
    assert state["iteration_count"] == 0

def test_invalid_interactions_never_enter_research_state():

    runtime = MockVoiceRuntime(
        [
            {
                "transcript": None,
                "speech_detected": False,
                "speech_duration_ms": 0,
                "audio_issue": False,
            },
            {
                "transcript": "la la la",
                "speech_detected": True,
                "speech_duration_ms": 1500,
                "audio_issue": False,
            },
            {
                "transcript": "Can you hear me?",
                "speech_detected": True,
                "speech_duration_ms": 600,
                "audio_issue": False,
            },
        ]
    )

    orchestrator = VoiceOrchestrator(runtime)
    state = make_state()

    initial_responses = deepcopy(
        state["responses"]
    )
    initial_iteration = state["iteration_count"]

    # Silence
    event = collect_response(
        orchestrator,
        state,
    )

    assert event["transcript"] is None
    assert state["responses"] == initial_responses
    assert state["iteration_count"] == initial_iteration

    # Non-responsive speech
    event = collect_response(
        orchestrator,
        state,
    )

    state["response_status"] = "non_responsive"

    assert state["responses"] == initial_responses
    assert state["iteration_count"] == initial_iteration

    # Recovery confirmation
    state["voice_interaction_mode"] = (
        "audibility_confirmation"
    )

    event = collect_response(
        orchestrator,
        state,
    )

    assert event["interaction_mode"] == (
        "audibility_confirmation"
    )

    assert state["responses"] == initial_responses
    assert state["iteration_count"] == initial_iteration

