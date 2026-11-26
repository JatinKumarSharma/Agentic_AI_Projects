import sys
from pathlib import Path
from threading import Barrier

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
    {
        "proposal_id": "UUID_D",
        "target_gap": "Price perception",
        "question": "How does the price compare with the value you expect?",
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "medium",
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
# MOCK RESPONSES — GROUP-LOCAL INDICES
# --------------------------------------------------

product_awareness_primary_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,high,medium,low,low,medium
"""

purchase_intent_primary_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,medium,high,low,medium,low
"""

price_perception_primary_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,high,medium,low,low,low
"""

product_awareness_recovery_csv = """proposal_index,expected_information_gain,expected_state_improvement,risk,cost,consumer_burden
0,high,high,low,medium,low
"""


# --------------------------------------------------
# PROMPT-AWARE MOCK
# Responses are selected by proposal content,
# not by concurrent invocation order.
# --------------------------------------------------
primary_barrier = Barrier(3)

def mock_invoke(self, prompt, *args, **kwargs):
    prompt_text = str(prompt)

    contains_a = "How did you first learn about this product?" in prompt_text
    contains_b = "What information did you find most useful?" in prompt_text
    contains_c = "What would influence your purchase decision?" in prompt_text
    contains_d = (
        "How does the price compare with the value you expect?"
        in prompt_text
    )

    # All three primary target-gap evaluations must overlap.
    if (
        (contains_a and contains_b and not contains_c and not contains_d)
        or (contains_c and not contains_a and not contains_b and not contains_d)
        or (contains_d and not contains_a and not contains_b and not contains_c)
    ):
        primary_barrier.wait(timeout=5)

    if contains_a and contains_b and not contains_c:
        return SimpleNamespace(
            content=product_awareness_primary_csv
        )

    if contains_c and not contains_a and not contains_b:
        return SimpleNamespace(
            content=purchase_intent_primary_csv
        )

    if contains_d and not contains_a and not contains_b and not contains_c:
        return SimpleNamespace(
            content=price_perception_primary_csv
        )

    if contains_b and not contains_a and not contains_c:
        return SimpleNamespace(
            content=product_awareness_recovery_csv
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

    result = evaluate_proposals(state)

    evaluated = result["evaluated_proposals"]

    assert mocked_invoke.call_count == 4

    assert len(evaluated) == 4

    assert [p["proposal_id"] for p in evaluated] == [
        "UUID_A",
        "UUID_B",
        "UUID_C",
        "UUID_D",
    ]

    assert evaluated[0]["expected_information_gain"] == "high"

    assert evaluated[1]["expected_information_gain"] == "high"
    assert evaluated[1]["expected_state_improvement"] == "high"

    assert evaluated[2]["expected_state_improvement"] == "high"

    assert evaluated[3]["expected_information_gain"] == "high"
    assert evaluated[3]["expected_state_improvement"] == "medium"


print("\nRECOVERY TEST PASSED")
print("LLM calls:", mocked_invoke.call_count)

for proposal in evaluated:
    print(
        proposal["proposal_id"],
        proposal["expected_information_gain"],
        proposal["expected_state_improvement"],
    )
