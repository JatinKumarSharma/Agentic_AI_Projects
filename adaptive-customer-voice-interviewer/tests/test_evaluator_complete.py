import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import evaluate_proposals

from unittest.mock import patch
from types import SimpleNamespace

import pytest


# --------------------------------------------------
# SYNTHETIC PROPOSALS
# --------------------------------------------------

proposals = [
    {
        "proposal_id": "UUID_A",
        "target_gap": "Product awareness",
        "question": "How did you first learn about this product?",
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
        "expected_information_gain": "low",
        "expected_state_improvement": "low",
        "risk": "low",
        "cost": "low",
        "consumer_burden": "low",
    },
    {
        "proposal_id": "UUID_B",
        "target_gap": "Product awareness",
        "question": "What information did you find most useful?",
        "importance": "medium",
        "goal_alignment": "high",
        "criticality": "medium",
        "expected_information_gain": "low",
        "expected_state_improvement": "low",
        "risk": "low",
        "cost": "low",
        "consumer_burden": "low",
    },
    {
        "proposal_id": "UUID_C",
        "target_gap": "Purchase intent",
        "question": "What would influence your purchase decision?",
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
        "expected_information_gain": "low",
        "expected_state_improvement": "low",
        "risk": "low",
        "cost": "low",
        "consumer_burden": "low",
    },
]

state = {
    "proposals": proposals,
    "research_context": {
        "company_name": "Test Company",
        "product_name": "Test Product",
        "product_category": "Consumer Product",
        "research_goal": "Understand consumer purchase decisions",
    },
}


# --------------------------------------------------
# MOCK PRIMARY RESPONSE
# Proposal index 1 is deliberately missing.
# --------------------------------------------------

primary_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,high,medium,low,low,medium
2,medium,high,low,medium,low
"""


# --------------------------------------------------
# MOCK RESPONSES
# Each primary response uses group-local indices.
# --------------------------------------------------

product_awareness_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,high,medium,low,low,medium
"""

purchase_intent_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,medium,high,medium,low,medium
"""

recovery_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,high,high,low,medium,low
"""


# --------------------------------------------------
# EXECUTE TEST
# --------------------------------------------------


@pytest.fixture(autouse=True)
def force_mocked_groq_evaluator(monkeypatch):
    monkeypatch.setenv("CVI_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-for-offline-tests")
    monkeypatch.setenv("CVI_EVALUATOR_MODEL", "offline-evaluator-model")


def test_evaluator_completes_all_proposals():
    def mock_invoke(prompt, *args, **kwargs):

        prompt_text = str(prompt)

        if "How did you first learn about this product?" in prompt_text:
            return SimpleNamespace(content=product_awareness_csv)

        if "What would influence your purchase decision?" in prompt_text:
            return SimpleNamespace(content=purchase_intent_csv)

        # Recovery call for UUID_B
        if "What information did you find most useful?" in prompt_text:
            return SimpleNamespace(content=recovery_csv)

        raise AssertionError("Unexpected evaluator prompt")


    with patch("agent.proposal_evaluator.llm") as mocked_llm:
        mocked_llm.invoke.side_effect = mock_invoke

        result = evaluate_proposals(state)

        evaluated = result["evaluated_proposals"]

        assert mocked_llm.invoke.call_count == 3
        assert len(evaluated) == 3

        assert [p["proposal_id"] for p in evaluated] == [
            "UUID_A",
            "UUID_B",
            "UUID_C",
        ]

        assert evaluated[0]["expected_information_gain"] == "high"
        assert evaluated[0]["expected_state_improvement"] == "medium"

        assert evaluated[1]["expected_information_gain"] == "high"
        assert evaluated[1]["expected_state_improvement"] == "high"

        assert evaluated[2]["expected_information_gain"] == "medium"
        assert evaluated[2]["expected_state_improvement"] == "high"

        for proposal in evaluated:
            for field in [
                "expected_information_gain",
                "expected_state_improvement",
                "risk",
                "cost",
                "consumer_burden",
            ]:
                assert proposal[field] in ["low", "medium", "high"]

        print("\nCOMPLETE EVALUATION TEST PASSED")
        print("LLM calls:", mocked_llm.invoke.call_count)

        for proposal in evaluated:
            print(
                proposal["proposal_id"],
                proposal["expected_information_gain"],
                proposal["expected_state_improvement"],
            )