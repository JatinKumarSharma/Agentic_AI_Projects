
import os
import pytest
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import evaluate_proposals


state = {
    "research_context": {
        "company_name": "Test Company",
        "product_name": "Test Product",
        "product_category": "Consumer Electronics",
        "research_goal": (
            "Understand brand awareness, purchase behaviour, "
            "and consumer satisfaction."
        ),
    },

    "consumer_context": """
    Known consumer information:

    1. The consumer has never heard of Test Company.
    2. The consumer has purchased this product category before.
    3. The consumer's purchase frequency is completely unknown.
    4. The consumer has not shared any information about
       packaging preferences.
    5. The consumer has used Test Product once.
    6. The consumer has not provided detailed satisfaction
       feedback about Test Product.
    """,

    "proposals": [
        {
            "proposal_id": str(uuid4()),
            "target_gap": (
                "Determine whether the consumer is aware "
                "of the company's brand."
            ),
            "question": (
                "Have you heard of Test Company before?"
            ),
            "importance": "high",
            "goal_alignment": "high",
            "criticality": "high",
        },

        {
            "proposal_id": str(uuid4()),
            "target_gap": (
                "Determine how frequently the consumer "
                "purchases products in this category."
            ),
            "question": (
                "How often do you purchase products "
                "in this category?"
            ),
            "importance": "high",
            "goal_alignment": "high",
            "criticality": "high",
        },

        {
            "proposal_id": str(uuid4()),
            "target_gap": (
                "Determine how frequently the consumer "
                "purchases products in this category."
            ),
            "question": (
                "What colour packaging do you prefer?"
            ),
            "importance": "high",
            "goal_alignment": "low",
            "criticality": "medium",
        },

        {
            "proposal_id": str(uuid4()),
            "target_gap": (
                "Understand the consumer's satisfaction "
                "with Test Product."
            ),
            "question": (
                "Do you like our product?"
            ),
            "importance": "high",
            "goal_alignment": "high",
            "criticality": "high",
        },
    ],
}


# --------------------------------------------------
# LIVE GROQ INTEGRATION TEST
# --------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="Live Groq tests disabled. Set RUN_LIVE_LLM_TESTS=1 to enable."
)
def test_information_gain_vs_state_improvement():

    # Run evaluator
    result = evaluate_proposals(state)
    evaluated_proposals = result["evaluated_proposals"]

    # Display results
    print("\n" + "=" * 75)
    print("INFORMATION GAIN VS STATE IMPROVEMENT")
    print("=" * 75)

    for index, proposal in enumerate(evaluated_proposals):

        print(f"\nPROPOSAL {index + 1}")
        print("-" * 50)

        print("Target gap:", proposal["target_gap"])
        print("Question:", proposal["question"])

        print(
            "Information Gain:",
            proposal["expected_information_gain"]
        )

        print(
            "State Improvement:",
            proposal["expected_state_improvement"]
        )

        print("Risk:", proposal["risk"])

    # Structural validation
    assert len(evaluated_proposals) == 4

    assert [
        p["proposal_id"] for p in evaluated_proposals
    ] == [
        p["proposal_id"] for p in state["proposals"]
    ]

    valid_values = {"low", "medium", "high"}

    for proposal in evaluated_proposals:
        assert proposal["expected_information_gain"] in valid_values
        assert proposal["expected_state_improvement"] in valid_values

    print("\n" + "=" * 75)
    print("TEST PASSED")
    print("4 proposals evaluated.")
    print("8 information/state evaluation values validated.")
    print("Original proposal IDs preserved.")
    print("=" * 75)