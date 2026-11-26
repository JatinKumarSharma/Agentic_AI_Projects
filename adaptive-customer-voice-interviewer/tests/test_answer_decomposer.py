
import agent.answer_decomposer as answer_decomposer

from agent.decomposition_schemas import (
    AnswerDecompositionSchema,
    ExtractedEvidence,
)


class FakeStructuredLLM:
    """A predictable replacement for the real Ollama model."""

    def __init__(self, result):
        self.result = result
        self.prompt = None
        self.invoke_count = 0

    def invoke(self, prompt):
        self.prompt = prompt
        self.invoke_count += 1
        return self.result


def make_research_context():
    return {
        "company_name": "Apple",
        "product_name": "iPhone",
        "product_category": "Smartphone",
        "research_goal": (
            "Understand consumer awareness, relationship, perception, "
            "sentiment, experiences, needs, pain points, aspirations, "
            "purchase intent, and constraints related to the product."
        ),
    }


def make_decomposition():
    return AnswerDecompositionSchema(
        summary=(
            "The consumer likes the iPhone camera and smooth performance "
            "but is frustrated by declining battery life."
        ),
        evidence=[
            ExtractedEvidence(
                category="pain_points",
                parameter="battery_life",
                value="Battery life has become frustrating.",
                sentiment="negative",
                evidence_type="explicit",
                confidence="high",
            )
        ],
        ambiguity_detected=False,
        potential_contradictions=[],
    )


def test_empty_response_skips_llm(monkeypatch):
    def unexpected_llm_call(schema):
        raise AssertionError(
            "LLM should not be called when the response is empty."
        )

    monkeypatch.setattr(
        answer_decomposer,
        "get_structured_llm",
        unexpected_llm_call,
    )

    result = answer_decomposer.decompose_answer(
        {"latest_response": ""}
    )

    assert result == {"latest_decomposition": None}


def test_decompose_answer_uses_structured_llm(monkeypatch):
    expected_decomposition = make_decomposition()
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
        "research_context": make_research_context(),
        "latest_response": (
            "I've been using an iPhone 14 for almost two years. "
            "I really like the camera and how smooth everything feels, "
            "but the battery has become frustrating recently. "
            "I'm thinking about upgrading next year, although the price "
            "of newer iPhones makes me hesitate."
        ),
    }

    result = answer_decomposer.decompose_answer(state)

    assert requested_schemas == [AnswerDecompositionSchema]
    assert fake_llm.invoke_count == 1
    assert fake_llm.prompt is not None

    assert result == {
        "latest_decomposition": expected_decomposition.model_dump()
    }

    assert (
        result["latest_decomposition"]["evidence"][0]["parameter"]
        == "battery_life"
    )
