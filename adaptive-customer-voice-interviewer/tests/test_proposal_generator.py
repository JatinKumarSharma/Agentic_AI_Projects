from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

import agent.proposal_generator as proposal_generator


CSV_RESPONSE = """target_index,question_number,question
0,1,How long have you used the product?
0,2,What do you use it for most often?
0,3,What would make you use it less?
1,1,What frustrates you most about the product?
1,2,When does the product fall short?
1,3,What would you change first?
"""


MESSY_CSV_RESPONSE = '''Here are the candidate questions:
```csv
target_index,question_number,question
0,1,"How long have you used "the product"? It matters.",
0,2,What do you use it for most often?
0,3,What would make you use it less?
```
Hope this helps.
'''


def test_parse_batch_questions_tolerates_messy_local_model_output():
    parsed = proposal_generator.parse_batch_questions(
        MESSY_CSV_RESPONSE,
        target_count=1,
    )

    assert parsed == [[
        'How long have you used "the product"? It matters.',
        "What do you use it for most often?",
        "What would make you use it less?",
    ]]


EXTRA_TARGET_CSV = """target_index,question_number,question
0,1,"How do you use Widget day to day?"
0,2,"How often do you use Widget?"
0,3,"What makes you reach for Widget?"
1,1,"What frustrates you about Widget?"
1,2,"When did Widget fall short?"
1,3,"What would you change first?"
2,1,"What else should Widget do?"
2,2,"When did it miss expectations?"
2,3,"How could it improve?"
"""


def test_parse_batch_questions_ignores_hallucinated_extra_targets():
    parsed = proposal_generator.parse_batch_questions(
        EXTRA_TARGET_CSV,
        target_count=2,
    )

    assert len(parsed) == 2
    assert parsed[0][0] == "How do you use Widget day to day?"
    assert parsed[1][2] == "What would you change first?"


ONE_BASED_CSV = """target_index,question_number,question
1,1,"First target q1?"
1,2,"First target q2?"
1,3,"First target q3?"
2,1,"Second target q1?"
2,2,"Second target q2?"
2,3,"Second target q3?"
"""


def test_parse_batch_questions_accepts_one_based_target_indices():
    parsed = proposal_generator.parse_batch_questions(
        ONE_BASED_CSV,
        target_count=2,
    )

    assert parsed[0][0] == "First target q1?"
    assert parsed[1][0] == "Second target q1?"


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs = kwargs
        return SimpleNamespace(content=self.content)


def make_state():
    return {
        "iteration_count": 1,
        "research_context": {
            "company_name": "Test Company",
            "product_name": "Test Product",
            "product_category": "Consumer Software",
            "research_goal": (
                "Understand product usage and pain points."
            ),
        },
        "unresolved_gaps": [
            {
                "parameter": "Product relationship",
                "category": "product_relationship",
                "gap_type": "missing",
                "importance": "high",
                "goal_alignment": "high",
                "criticality": "critical",
                "expected_goal_improvement": "high",
                "estimated_cost": "low",
                "estimated_consumer_burden": "low",
                "confidence": "low",
                "verification_required": True,
            },
            {
                "parameter": "Pain points",
                "category": "pain_points",
                "gap_type": "insufficient",
                "importance": "high",
                "goal_alignment": "high",
                "criticality": "high",
                "expected_goal_improvement": "high",
                "estimated_cost": "low",
                "estimated_consumer_burden": "low",
                "confidence": "medium",
                "verification_required": True,
            },
            {
                "parameter": "Purchase intent",
                "category": "purchase_intent",
                "gap_type": "missing",
                "importance": "medium",
                "goal_alignment": "medium",
                "criticality": "medium",
                "expected_goal_improvement": "medium",
                "estimated_cost": "medium",
                "estimated_consumer_burden": "medium",
                "confidence": "low",
                "verification_required": False,
            },
        ],
    }


def test_generate_adaptive_proposals_uses_mocked_model_boundary(monkeypatch):
    fake_llm = FakeLLM(CSV_RESPONSE)
    factory_calls = []

    def fake_get_llm(**kwargs):
        factory_calls.append(kwargs)
        return fake_llm

    monkeypatch.setattr(
        proposal_generator,
        "get_llm",
        fake_get_llm,
    )

    result = proposal_generator.generate_proposals(make_state())

    assert len(factory_calls) == 1
    assert factory_calls[0] == {"purpose": "proposal"}
    assert len(fake_llm.calls) == 1
    # Provider-neutral invoke limit: Groq uses max_tokens, Ollama may use {}.
    assert fake_llm.kwargs.get("max_tokens", 900) == 900

    messages = fake_llm.calls[0]
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert "Understand product usage and pain points." in messages[1].content

    proposals = result["proposals"]
    assert len(proposals) == 6

    proposal_ids = [proposal["proposal_id"] for proposal in proposals]
    assert len(set(proposal_ids)) == 6

    assert {proposal["target_gap"] for proposal in proposals} == {
        "Product relationship",
        "Pain points",
    }

    questions_by_gap = {}
    for proposal in proposals:
        questions_by_gap.setdefault(
            proposal["target_gap"], []
        ).append(proposal["question"])

    assert questions_by_gap["Product relationship"] == [
        "How long have you used the product?",
        "What do you use it for most often?",
        "What would make you use it less?",
    ]
    assert questions_by_gap["Pain points"] == [
        "What frustrates you most about the product?",
        "When does the product fall short?",
        "What would you change first?",
    ]

    for proposal in proposals:
        assert proposal["expected_information_gain"] == "low"
        assert proposal["expected_state_improvement"] == "low"
        assert proposal["importance"] in {"low", "medium", "high", "critical"}
        assert proposal["goal_alignment"] in {"low", "medium", "high"}
