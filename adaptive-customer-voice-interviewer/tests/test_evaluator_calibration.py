
import sys
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import evaluate_proposals


# --------------------------------------------------
# RESEARCH CONTEXT AND PROPOSALS
# --------------------------------------------------

proposals = [
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Understand usual purchase behaviour",
        "question": "What product category do you usually purchase?",
        "importance": "medium",
        "goal_alignment": "high",
        "criticality": "medium",
    },
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Understand purchase decision factors",
        "question": (
            "What factors influenced your most recent "
            "purchase decision?"
        ),
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "medium",
    },
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Understand negative product experiences",
        "question": (
            "Describe a specific occasion when the product "
            "failed to meet your expectations, including "
            "what happened and how you responded."
        ),
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Understand comparative product perceptions",
        "question": (
            "Don't you agree that this product is better "
            "than competing alternatives?"
        ),
        "importance": "medium",
        "goal_alignment": "high",
        "criticality": "medium",
    },
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Understand reasons for product dissatisfaction",
        "question": (
            "Why did you waste money on a product that "
            "clearly wasn't suitable for you?"
        ),
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Understand personal financial purchase barriers",
        "question": (
            "Tell me about a deeply personal financial "
            "difficulty that affected your ability to "
            "purchase this product."
        ),
        "importance": "medium",
        "goal_alignment": "medium",
        "criticality": "medium",
    },
]

state = {
    "research_context": {
        "company_name": "Test Company",
        "product_name": "Test Product",
        "product_category": "Consumer Product",
        "research_goal": (
            "Understand consumer purchase decisions, "
            "product experiences, barriers, and unmet needs."
        ),
    },
    "consumer_context": (
        "The consumer has previously discussed purchasing "
        "consumer products but has not provided detailed "
        "information about their purchase decisions."
    ),
    "proposals": proposals,
}


# --------------------------------------------------
# MOCK EVALUATION RESPONSES
# Each group contains one proposal, so its index is 0.
# --------------------------------------------------

evaluation_profiles = {
    "What product category do you usually purchase?":
        "0,medium,medium,low,low,low",

    "What factors influenced your most recent":
        "0,high,high,low,low,low",

    "Describe a specific occasion":
        "0,high,high,medium,medium,medium",

    "Don't you agree":
        "0,medium,low,high,low,medium",

    "Why did you waste money":
        "0,medium,low,high,low,high",

    "Tell me about a deeply personal financial":
        "0,medium,medium,medium,medium,high",
}


def mock_invoke(prompt, *args, **kwargs):
    prompt_text = str(prompt)

    for question_fragment, evaluation in evaluation_profiles.items():
        if question_fragment in prompt_text:
            csv_response = (
                "proposal_index,expected_information_gain,"
                "expected_state_improvement,risk,cost,consumer_burden\n"
                f"{evaluation}\n"
            )
            return SimpleNamespace(content=csv_response)

    raise AssertionError("Unexpected evaluator prompt")


# --------------------------------------------------
# PYTEST TEST
# --------------------------------------------------


@pytest.fixture(autouse=True)
def force_mocked_groq_evaluator(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.setenv("CVI_EVALUATOR_MODEL", "offline-evaluator-model")


def test_evaluator_calibration_structure():

    with patch("agent.proposal_evaluator.llm") as mocked_llm:
        mocked_llm.invoke.side_effect = mock_invoke

        result = evaluate_proposals(state)

    evaluated_proposals = result["evaluated_proposals"]

    # Six unique target gaps -> six primary LLM calls.
    assert mocked_llm.invoke.call_count == 6

    # All proposals must be evaluated.
    assert len(evaluated_proposals) == 6

    # Original ordering and IDs must be preserved.
    assert [
        p["proposal_id"] for p in evaluated_proposals
    ] == [
        p["proposal_id"] for p in proposals
    ]

    criteria = [
        "expected_information_gain",
        "expected_state_improvement",
        "risk",
        "cost",
        "consumer_burden",
    ]

    valid_values = {"low", "medium", "high"}

    for proposal in evaluated_proposals:
        for criterion in criteria:
            assert proposal[criterion] in valid_values

    print("\nCALIBRATION STRUCTURE TEST PASSED")
    print("6 proposals evaluated.")
    print("30 evaluation values validated.")
    print("Original proposal IDs preserved.")
    print("LLM calls:", mocked_llm.invoke.call_count)
