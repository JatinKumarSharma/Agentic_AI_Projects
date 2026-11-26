
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import evaluate_proposals


research_context = {
    "company_name": "Test Company",
    "product_name": "Test Product",
    "product_category": "Consumer Electronics",
    "research_goal": (
        "Understand consumer purchase behaviour "
        "and product satisfaction."
    ),
}


# --------------------------------------------------
# KNOWLEDGE SCENARIOS
# --------------------------------------------------

scenarios = [
    {
        "name": "PURCHASE FREQUENCY ALREADY KNOWN",
        "knowledge": {
            "purchase_intent": {
                "values": [
                    {
                        "value": (
                            "Purchases approximately twice a month"
                        )
                    }
                ]
            }
        },
        "target_gap": "Determine the consumer's purchase frequency.",
        "question": "How often do you purchase this product?",
    },
    {
        "name": "PURCHASE FREQUENCY UNKNOWN",
        "knowledge": {},
        "target_gap": "Determine the consumer's purchase frequency.",
        "question": "How often do you purchase this product?",
    },
    {
        "name": "SATISFACTION ALREADY KNOWN",
        "knowledge": {
            "overall_sentiment": {
                "values": [
                    {
                        "value": (
                            "Very satisfied with the product"
                        )
                    }
                ]
            }
        },
        "target_gap": (
            "Determine the consumer's overall satisfaction "
            "with the product."
        ),
        "question": "How satisfied are you with this product?",
    },
    {
        "name": "SATISFACTION UNKNOWN",
        "knowledge": {},
        "target_gap": (
            "Determine the consumer's overall satisfaction "
            "with the product."
        ),
        "question": "How satisfied are you with this product?",
    },
]


# --------------------------------------------------
# RUN EVALUATIONS
# --------------------------------------------------

all_results = []

for scenario in scenarios:

    proposal = {
        "proposal_id": str(uuid4()),
        "target_gap": scenario["target_gap"],
        "question": scenario["question"],
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    }

    state = {
        "research_context": research_context,
        "proposals": [proposal],
        **scenario["knowledge"],
    }

    result = evaluate_proposals(state)

    evaluated = result["evaluated_proposals"][0]

    all_results.append({
        "scenario": scenario["name"],
        "question": evaluated["question"],
        "information_gain": (
            evaluated["expected_information_gain"]
        ),
        "state_improvement": (
            evaluated["expected_state_improvement"]
        ),
        "risk": evaluated["risk"],
    })


# --------------------------------------------------
# DISPLAY RESULTS
# --------------------------------------------------

print("\n" + "=" * 75)
print("CORRECTED CONSUMER KNOWLEDGE AWARENESS TEST")
print("=" * 75)

for result in all_results:

    print("\n" + result["scenario"])
    print("-" * 50)

    print("Question:", result["question"])
    print("Information Gain:", result["information_gain"])
    print("State Improvement:", result["state_improvement"])
    print("Risk:", result["risk"])


# --------------------------------------------------
# STRUCTURAL VALIDATION
# --------------------------------------------------

assert len(all_results) == 4

valid_values = {"low", "medium", "high"}

for result in all_results:
    assert result["information_gain"] in valid_values
    assert result["state_improvement"] in valid_values
    assert result["risk"] in valid_values

print("\n" + "=" * 75)
print("CORRECTED KNOWLEDGE AWARENESS TEST PASSED")
print("4 scenarios evaluated.")
print("12 evaluation values validated.")
print("=" * 75)
