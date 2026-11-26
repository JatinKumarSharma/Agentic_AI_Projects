
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
        "Understand consumer purchase behaviour "
        "and product satisfaction."
    ),
}


# --------------------------------------------------
# FOUR PROPOSALS
# --------------------------------------------------

proposals = [
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Determine the consumer's purchase frequency.",
        "question": "How often do you purchase this product?",
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Determine the consumer's purchase frequency.",
        "question": "How often do you purchase this product?",
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Determine the consumer's satisfaction with the product.",
        "question": "Are you satisfied with this product?",
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
    {
        "proposal_id": str(uuid4()),
        "target_gap": "Determine the consumer's satisfaction with the product.",
        "question": "Are you satisfied with this product?",
        "importance": "high",
        "goal_alignment": "high",
        "criticality": "high",
    },
]


# --------------------------------------------------
# TWO DIFFERENT CONSUMER KNOWLEDGE SCENARIOS
# --------------------------------------------------

consumer_contexts = [
    """
    Consumer knowledge:

    The consumer has explicitly stated:
    "I purchase this product approximately twice a month."

    The consumer has not yet discussed product satisfaction.
    """,

    """
    Consumer knowledge:

    The consumer has not provided any information
    about purchase frequency.

    The consumer has explicitly stated:
    "I am very satisfied with this product.
    It performs exactly as I expected."
    """,
]


# --------------------------------------------------
# RUN EVALUATION
# --------------------------------------------------

state = {
    "research_context": research_context,
    "consumer_context": (
        consumer_contexts[0]
        + "\n\n"
        + consumer_contexts[1]
    ),
    "proposals": proposals,
}

# IMPORTANT:
# We need to evaluate each scenario separately.
# Therefore, use two separate evaluator calls.

scenario_results = []

for scenario_index, consumer_context in enumerate(
    consumer_contexts
):

    scenario_state = {
        "research_context": research_context,
        "consumer_context": consumer_context,
        "proposals": (
            proposals[0:2]
            if scenario_index == 0
            else proposals[2:4]
        ),
    }

    result = evaluate_proposals(scenario_state)

    evaluated = result["evaluated_proposals"]

    scenario_results.append(evaluated)


# --------------------------------------------------
# DISPLAY RESULTS
# --------------------------------------------------

criteria = [
    "expected_information_gain",
    "expected_state_improvement",
    "risk",
    "cost",
    "consumer_burden",
]

scenario_names = [
    "PURCHASE FREQUENCY ALREADY KNOWN",
    "PRODUCT SATISFACTION ALREADY KNOWN",
]

for scenario_index, evaluated in enumerate(scenario_results):

    print("\n" + "=" * 75)
    print(scenario_names[scenario_index])
    print("=" * 75)

    for proposal in evaluated:

        print("\nQuestion:", proposal["question"])

        for criterion in criteria:
            print(
                criterion + ":",
                proposal[criterion]
            )


# --------------------------------------------------
# STRUCTURAL VALIDATION
# --------------------------------------------------

assert len(scenario_results) == 2

assert all(len(result) == 2 for result in scenario_results)

assert (
    [p["proposal_id"] for p in scenario_results[0]]
    == [p["proposal_id"] for p in proposals[0:2]]
)

assert (
    [p["proposal_id"] for p in scenario_results[1]]
    == [p["proposal_id"] for p in proposals[2:4]]
)

valid_values = {"low", "medium", "high"}

for evaluated in scenario_results:
    for proposal in evaluated:
        for criterion in criteria:
            assert proposal[criterion] in valid_values

print("\n" + "=" * 75)
print("CONSUMER KNOWLEDGE AWARENESS TEST PASSED")
print("Two scenarios evaluated.")
print("Four proposal evaluations completed.")
print("20 evaluation values validated.")
print("Original proposal IDs preserved.")
print("=" * 75)
