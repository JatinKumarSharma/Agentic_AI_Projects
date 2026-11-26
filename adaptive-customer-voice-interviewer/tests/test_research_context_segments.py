from datetime import date
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

import agent.meta_planner as meta_planner
import agent.proposal_generator as proposal_generator
from agent.session import create_initial_state, create_research_session


SEGMENT_CSV = """target_index,question_number,question
0,1,What do you use most often?
0,2,What frustrates you most?
0,3,What would you change first?
"""

AWARENESS_CSV = """target_index,question_number,question
0,1,How did you first hear about UI Check Product?
0,2,What do you use most often?
0,3,What would you change first?
"""


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(content=self.content)


def make_session():
    return create_research_session(
        company_id="company-1",
        company_name="UI Check Company",
        product_id="product-1",
        product_name="UI Check Product",
        product_category="Analytics",
        research_goal="Understand feature-level sentiment.",
        start_date=date(2026, 11, 21),
        target_audience="Existing customers",
        customer_segment="existing customer",
        interview_constraints=["Keep answers under two minutes"],
    )


def test_initial_state_carries_known_segment_into_research_context():
    state = create_initial_state(make_session())

    assert state["research_context"]["customer_segment"] == (
        "existing customer"
    )
    assert state["research_context"]["target_audience"] == (
        "Existing customers"
    )
    assert state["research_context"]["interview_constraints"] == [
        "Keep answers under two minutes"
    ]


def test_meta_plan_survives_provider_rate_limit(monkeypatch):
    class RateLimitError(Exception):
        pass

    class FailingStructuredLLM:
        def invoke(self, prompt):
            raise RateLimitError("rate limited")

    monkeypatch.setattr(
        meta_planner,
        "get_structured_llm",
        lambda schema: FailingStructuredLLM(),
    )

    result = meta_planner.create_meta_plan(
        create_initial_state(make_session())
    )
    parameters = {
        item["parameter"]
        for item in result["meta_plan"]["required_information"]
    }

    assert "Usage experience" in parameters
    assert "Product relationship" not in parameters
    assert "Product awareness" not in parameters


def test_known_segment_skips_relationship_target(monkeypatch):
    state = create_initial_state(make_session())
    state["iteration_count"] = 0
    state["meta_plan"] = {
        "required_information": [
            {
                "parameter": "Product relationship",
                "importance": "medium",
                "goal_alignment": "low",
                "criticality": "critical",
                "expected_goal_improvement": "medium",
                "estimated_cost": "low",
                "estimated_consumer_burden": "low",
            },
            {
                "parameter": "Usage experience",
                "importance": "high",
                "goal_alignment": "high",
                "criticality": "high",
                "expected_goal_improvement": "high",
                "estimated_cost": "low",
                "estimated_consumer_burden": "low",
            },
        ],
    }

    fake_llm = FakeLLM(SEGMENT_CSV)
    monkeypatch.setattr(
        proposal_generator,
        "get_llm",
        lambda **kwargs: fake_llm,
    )

    result = proposal_generator.generate_proposals(state)
    prompt = fake_llm.calls[0][1].content

    assert "existing customer" in prompt
    assert "Do not ask questions that establish" in prompt
    assert "Product relationship" not in prompt
    assert len(result["proposals"]) == 3
    assert {
        proposal["target_gap"] for proposal in result["proposals"]
    } == {"Usage experience"}


def test_known_segment_replaces_awareness_questions_deterministically(
    monkeypatch,
):
    state = create_initial_state(make_session())
    state["meta_plan"] = {
        "required_information": [
            {
                "parameter": "Onboarding experience",
                "importance": "high",
                "goal_alignment": "high",
                "criticality": "high",
                "expected_goal_improvement": "high",
                "estimated_cost": "low",
                "estimated_consumer_burden": "low",
            },
        ],
    }

    monkeypatch.setattr(
        proposal_generator,
        "get_llm",
        lambda **kwargs: FakeLLM(AWARENESS_CSV),
    )

    result = proposal_generator.generate_proposals(state)
    questions = [
        proposal["question"] for proposal in result["proposals"]
    ]

    assert len(questions) == 3
    assert not any(
        proposal_generator.is_established_context_question(question)
        for question in questions
    )
    assert "What do you use most often?" in questions


def test_question_generation_survives_provider_rate_limit(monkeypatch):
    class RateLimitError(Exception):
        pass

    class FailingLLM:
        def invoke(self, *args, **kwargs):
            raise RateLimitError("rate limited")

    state = create_initial_state(make_session())
    state["meta_plan"] = {
        "required_information": [
            {
                "parameter": "Usage experience",
                "importance": "high",
                "goal_alignment": "high",
                "criticality": "high",
                "expected_goal_improvement": "high",
                "estimated_cost": "low",
                "estimated_consumer_burden": "low",
            },
            {
                "parameter": "Pain points",
                "importance": "high",
                "goal_alignment": "high",
                "criticality": "high",
                "expected_goal_improvement": "high",
                "estimated_cost": "low",
                "estimated_consumer_burden": "low",
            },
        ],
    }

    monkeypatch.setattr(
        proposal_generator,
        "get_llm",
        lambda **kwargs: FailingLLM(),
    )

    result = proposal_generator.generate_proposals(state)
    questions = [
        proposal["question"] for proposal in result["proposals"]
    ]

    assert len(questions) == 6
    assert not any(
        proposal_generator.is_established_context_question(question)
        for question in questions
    )


def test_unknown_segment_keeps_generated_questions():
    question = "How did you first hear about this product?"

    assert proposal_generator.is_established_context_question(
        question
    ) is True
    assert proposal_generator.is_established_context_question(
        "Can you tell me a bit about how you first came across "
        "UI Check Product?"
    ) is True
    assert proposal_generator.sanitize_established_context_questions(
        [question],
        known=False,
        parameter="Awareness",
    ) == [question]
