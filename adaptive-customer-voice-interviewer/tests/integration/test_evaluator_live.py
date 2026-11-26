
import os
import pytest
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import evaluate_proposals


# --------------------------------------------------
# NINE SYNTHETIC PROPOSALS
# --------------------------------------------------

proposals = [
    {
        "proposal_id": f"UUID_{i}",
        "target_gap": gap,
        "question": question,
        "importance": importance,
        "goal_alignment": "high",
        "criticality": criticality,
        "expected_information_gain": "low",
        "expected_state_improvement": "low",
        "risk": "low",
        "cost": "low",
        "consumer_burden": "low",
    }
    for i, (gap, question, importance, criticality) in enumerate([
        (
            "Product awareness",
            "How did you first learn about this product?",
            "high", "high"
        ),
        (
            "Initial perception",
            "What was your first impression of the product?",
            "medium", "medium"
        ),
        (
            "Product understanding",
            "What do you understand this product is designed to help you do?",
            "high", "high"
        ),
        (
            "Usage experience",
            "Can you describe how you currently use this product?",
            "high", "high"
        ),
        (
            "Unmet needs",
            "What problem or need does this product fail to address for you?",
            "high", "high"
        ),
        (
            "Alternative solutions",
            "What other products or solutions have you considered?",
            "medium", "medium"
        ),
        (
            "Purchase motivation",
            "What factors would motivate you to purchase this product?",
            "high", "high"
        ),
        (
            "Purchase barriers",
            "What might prevent you from purchasing this product?",
            "high", "high"
        ),
        (
            "Improvement opportunities",
            "What is the most important improvement you would like to see?",
            "medium", "medium"
        ),
    ], start=1)
]


# --------------------------------------------------
# INTERVIEW STATE
# --------------------------------------------------

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
# LIVE GROQ INTEGRATION TEST
# --------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="Live Groq tests disabled. Set RUN_LIVE_LLM_TESTS=1 to enable."
)
def test_live_groq_evaluation():

    result = evaluate_proposals(state)

    evaluated = result["evaluated_proposals"]

    # --------------------------------------------------
    # VALIDATION
    # --------------------------------------------------

    assert len(evaluated) == 9, (
        f"Expected 9 evaluated proposals, received {len(evaluated)}"
    )

    expected_ids = [f"UUID_{i}" for i in range(1, 10)]
    actual_ids = [p["proposal_id"] for p in evaluated]

    assert actual_ids == expected_ids, (
        f"Proposal IDs or order changed: {actual_ids}"
    )

    evaluation_fields = [
        "expected_information_gain",
        "expected_state_improvement",
        "risk",
        "cost",
        "consumer_burden",
    ]

    valid_values = {"low", "medium", "high"}

    for proposal in evaluated:
        for field in evaluation_fields:
            assert proposal[field] in valid_values, (
                f"Invalid value for {proposal['proposal_id']}, "
                f"{field}: {proposal[field]}"
            )

    # --------------------------------------------------
    # RESULTS
    # --------------------------------------------------

    print("\nLIVE NINE-PROPOSAL GROQ TEST PASSED")

    print(f"Proposals evaluated: {len(evaluated)}")
    print(
        f"Evaluation values validated: "
        f"{len(evaluated) * len(evaluation_fields)}"
    )

    print("\nEvaluation results:")

    for proposal in evaluated:
        print(
            proposal["proposal_id"],
            "| Information gain:", proposal["expected_information_gain"],
            "| State improvement:", proposal["expected_state_improvement"],
            "| Risk:", proposal["risk"],
            "| Cost:", proposal["cost"],
            "| Consumer burden:", proposal["consumer_burden"],
        )
