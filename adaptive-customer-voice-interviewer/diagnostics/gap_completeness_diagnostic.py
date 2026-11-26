
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import evaluate_proposals


target_gap = (
    "Understand the consumer's satisfaction with Test Product, "
    "including reasons for satisfaction or dissatisfaction and "
    "the specific product attributes influencing their experience."
)

questions = [
    "Do you like our product?",

    "How satisfied are you with our product?",

    "What do you like or dislike about our product?",

    (
        "What specific product attributes influenced your "
        "satisfaction, and how did they affect your experience?"
    ),
]

state = {
    "research_context": {
        "company_name": "Test Company",
        "product_name": "Test Product",
        "product_category": "Consumer Electronics",
        "research_goal": (
            "Understand consumer satisfaction and identify "
            "product attributes that influence their experience."
        ),
    },

    "consumer_context": (
        "The consumer has used Test Product once. "
        "No satisfaction rating, reasons for satisfaction, "
        "or attribute-level feedback have been collected."
    ),

    "proposals": [
        {
            "proposal_id": str(uuid4()),
            "target_gap": target_gap,
            "question": question,
            "importance": "high",
            "goal_alignment": "high",
            "criticality": "high",
        }
        for question in questions
    ],
}


result = evaluate_proposals(state)
evaluated_proposals = result["evaluated_proposals"]


print("\n" + "=" * 75)
print("TARGET GAP COMPLETENESS TEST")
print("=" * 75)

for index, proposal in enumerate(evaluated_proposals):
    print(f"\nPROPOSAL {index + 1}")
    print("-" * 50)

    print("Question:", proposal["question"])
    print("Information Gain:", proposal["expected_information_gain"])
    print("State Improvement:", proposal["expected_state_improvement"])
    print("Risk:", proposal["risk"])
    print("Cost:", proposal["cost"])
    print("Consumer Burden:", proposal["consumer_burden"])


assert len(evaluated_proposals) == 4

assert [
    p["proposal_id"] for p in evaluated_proposals
] == [
    p["proposal_id"] for p in state["proposals"]
]

print("\nGAP COMPLETENESS TEST PASSED")
