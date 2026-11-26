
from typing import TypedDict, Any

from langgraph.graph import StateGraph, START, END

from agent.answer_decomposer import decompose_answer


class DecomposerTestState(TypedDict, total=False):
    latest_response: str
    research_context: dict[str, Any]
    research_session: dict[str, Any]
    latest_decomposition: dict[str, Any]
    downstream_ran: bool


def test_decomposer_emits_custom_event_and_graph_continues(monkeypatch):

    expected_decomposition = {
        "summary": "The customer has used the product for two years.",
        "evidence": [
            {
                "category": "usage_experience",
                "parameter": "usage_duration",
                "value": "two years",
                "sentiment": None,
                "evidence_type": "explicit",
                "confidence": "high",
            }
        ],
        "ambiguity_detected": False,
        "potential_contradictions": [],
    }

    class FakeResult:
        @property
        def evidence(self):
            return expected_decomposition["evidence"]

        def model_dump(self):
            return expected_decomposition

    class FakeStructuredLLM:
        def invoke(self, prompt):
            return FakeResult()

    monkeypatch.setattr(
        "agent.answer_decomposer.get_structured_llm",
        lambda schema: FakeStructuredLLM(),
    )

    def downstream_node(state):
        return {"downstream_ran": True}

    builder = StateGraph(DecomposerTestState)

    builder.add_node("decompose", decompose_answer)
    builder.add_node("downstream", downstream_node)

    builder.add_edge(START, "decompose")
    builder.add_edge("decompose", "downstream")
    builder.add_edge("downstream", END)

    graph = builder.compile()

    initial_state = {
        "latest_response": "I have used the product for two years.",
        "research_context": {
            "company_name": "Test Company",
            "product_name": "Test Product",
            "product_category": "Test Category",
            "research_goal": "Understand product experience.",
        },
        "research_session": {
            "session_id": "test-session-123",
        },
    }

    streamed_events = list(
        graph.stream(
            initial_state,
            stream_mode=["custom", "updates"],
        )
    )

    custom_events = [
        data
        for mode, data in streamed_events
        if mode == "custom"
    ]

    assert len(custom_events) == 1

    event = custom_events[0]

    assert event["event"] == "contextual_engagement_input"
    assert event["session_id"] == "test-session-123"
    assert event["interview_id"] == "test-session-123"
    assert event["decomposition"] == expected_decomposition

    updates = [
        data
        for mode, data in streamed_events
        if mode == "updates"
    ]

    assert any("downstream" in update for update in updates)
