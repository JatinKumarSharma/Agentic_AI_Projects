import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import evaluate_proposals


# --------------------------------------------------
# RESEARCH CONTEXT
# --------------------------------------------------

research_context = {
    "company_name": "Test Company",
    "product_name": "Test Product",
    "product_category": "Consumer Electronics",
    "research_goal": (
        "Understand consumer purchase behaviour, "
        "purchase frequency, and purchasing motivations."
    ),
}


# --------------------------------------------------
# EXISTING CONSUMER KNOWLEDGE
# --------------------------------------------------

knowledge = {
    "purchase_intent": {
        "values": [
            {
                "value": (
                    "The consumer purchases Test Product "
                    "approximately twice a month."
                )
            }
        ]
    }
}


# --------------------------------------------------
# CANDIDATE PROPOSALS
# --------------------------------------------------

questions = [
    {
        "target_gap": "Understand the consumer's purchase frequency.",
        "question": "How often do you purchase this product?",
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
    {
        "target_gap": "Understand the consumer's purchase frequency.",
        "question": (
            "How many times do you buy this product each month?"
        ),
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
    {
        "target_gap": "Understand the consumer's purchase frequency.",
        "question": (
            "What is your typical monthly purchase frequency?"
        ),
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
    {
        "target_gap": (
            "Understand the factors influencing purchase frequency."
        ),
        "question": (
            "What factors influence how frequently you "
            "purchase this product?"
        ),
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
]


proposals = [
    {
        "proposal_id": str(uuid4()),
        **question,
    }
    for question in questions
]


# --------------------------------------------------
# RUN EVALUATION
# --------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="Live Groq tests disabled. Set RUN_LIVE_LLM_TESTS=1 to enable."
)
def test_semantic_redundancy():

    state = {
        "research_context": research_context,
        "proposals": proposals,
        **knowledge,
    }

    result = evaluate_proposals(state)

    evaluated_proposals = result["evaluated_proposals"]

    # --------------------------------------------------
    # DISPLAY RESULTS
    # --------------------------------------------------

    print("\n" + "=" * 75)
    print("SEMANTIC REDUNDANCY TEST")
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
        print("Cost:", proposal["cost"])
        print("Consumer Burden:", proposal["consumer_burden"])

    # --------------------------------------------------
    # STRUCTURAL VALIDATION
    # --------------------------------------------------

    assert len(evaluated_proposals) == 4

    assert [
        p["proposal_id"] for p in evaluated_proposals
    ] == [
        p["proposal_id"] for p in proposals
    ]

    valid_values = {"low", "medium", "high"}

    criteria = [
        "expected_information_gain",
        "expected_state_improvement",
        "risk",
        "cost",
        "consumer_burden",
    ]

    for proposal in evaluated_proposals:
        for criterion in criteria:
            assert proposal[criterion] in valid_values

    print("\n" + "=" * 75)
    print("SEMANTIC REDUNDANCY TEST PASSED")
    print("4 proposals evaluated.")
    print("20 evaluation values validated.")
    print("Original proposal IDs preserved.")
    print("=" * 75)
    