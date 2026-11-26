import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import evaluate_proposals

from unittest.mock import patch
from types import SimpleNamespace

from agent.proposal_evaluator import evaluate_proposals


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
# MOCK RESPONSES — GROUP-LOCAL INDICES
# --------------------------------------------------

product_awareness_primary_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,high,medium,low,low,medium
"""

purchase_intent_primary_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,medium,high,low,medium,low
"""

# Deliberately incomplete: recovery returns no evaluation rows.
invalid_recovery_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
"""


# --------------------------------------------------
# PROMPT-AWARE MOCK
# --------------------------------------------------

def mock_invoke(self, prompt, *args, **kwargs):
    prompt_text = str(prompt)

    contains_a = "How did you first learn about this product?" in prompt_text
    contains_b = "What information did you find most useful?" in prompt_text
    contains_c = "What would influence your purchase decision?" in prompt_text

    if contains_a and contains_b and not contains_c:
        return SimpleNamespace(
            content=product_awareness_primary_csv
        )

    if contains_c and not contains_a and not contains_b:
        return SimpleNamespace(
            content=purchase_intent_primary_csv
        )

    if contains_b and not contains_a and not contains_c:
        return SimpleNamespace(
            content=invalid_recovery_csv
        )

    raise AssertionError(
        f"Unexpected evaluator prompt:\n{prompt_text}"
    )


# --------------------------------------------------
# EXECUTE TEST
# --------------------------------------------------

with patch(
    "langchain_groq.ChatGroq.invoke",
    autospec=True,
    side_effect=mock_invoke,
) as mocked_invoke:

    try:
        evaluate_proposals(state)

        raise AssertionError("Expected ValueError was not raised")

    except ValueError as exc:
        assert "Missing evaluations" in str(exc)

        print("\nINVALID RECOVERY TEST PASSED")
        print("Correctly rejected incomplete recovery response:")
        print(exc)

    assert mocked_invoke.call_count == 3
    print("LLM calls:", mocked_invoke.call_count)
    