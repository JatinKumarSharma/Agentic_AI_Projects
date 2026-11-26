import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_evaluator import (
    llm,
    build_evaluation_prompt,
    parse_evaluation_csv,
)


state = {
    "research_context": {
        "company_name": "Test Company",
        "product_name": "Test Product",
        "product_category": "Consumer Software",
        "research_goal": "Understand customer expectations for a new product.",
    },
}


proposal = {
    "proposal_id": "TEST_001",
    "target_gap": (
        "Understand the consumer's expectations regarding "
        "the minimum viable product and its functionality."
    ),
    "question": (
        "What specific features should the product include, "
        "and what should each feature allow you to do?"
    ),
    "importance": "high",
    "goal_alignment": "high",
    "criticality": "high",
}



def build_test_context(
    sufficiency,
    confidence,
    verification_status,
    applicability_status,
):
    if sufficiency == "basic":
        functionality = (
            "The consumer expects basic functionality across "
            "all feature categories but has not specified the "
            "individual features, their expected capabilities, "
            "or their relative importance."
        )

    elif sufficiency == "comprehensive":
        functionality = (
            "The consumer expects the product to provide a "
            "minimum viable product with the following features "
            "and capabilities:\n"
            "- User registration: account creation and login.\n"
            "- Profile management: view and update personal details.\n"
            "- Product search: search and filter available products.\n"
            "- Order placement: add products to cart and place orders.\n"
            "- Payment processing: complete payments securely.\n"
            "- Order tracking: monitor order status and delivery.\n"
            "- Customer support: access assistance for issues.\n"
            "The consumer considers these capabilities sufficient "
            "for the basic end-to-end user journey."
        )
        
    else:
        raise ValueError(
            f"Unsupported sufficiency level: {sufficiency}"
        )

    return f"""
Expectations:

product_type:
- Value: minimum viable product
- Evidence confidence: high
- Evidence type: explicit

functionality_coverage:
- Value: {functionality}
- Evidence confidence: high
- Evidence type: explicit

Knowledge quality:
- Sufficiency: {sufficiency}
- Confidence: {confidence}
- Verification status: {verification_status}
- Applicability status: {applicability_status}
"""


conditions = {
    "A_BASIC_VERIFIED": {
        "sufficiency": "basic",
        "confidence": "high",
        "verification_status": "verified",
        "applicability_status": "applicable",
    },

    "B_COMPREHENSIVE_VERIFIED": {
        "sufficiency": "comprehensive",
        "confidence": "high",
        "verification_status": "verified",
        "applicability_status": "applicable",
    },
}


N_TRIALS = 5

@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="Live Groq tests disabled. Set RUN_LIVE_LLM_TESTS=1 to enable."
)
def test_knowledge_quality_awareness():

    for condition_name, metadata in conditions.items():

        print("\n" + "=" * 65)
        print(f"CONDITION: {condition_name}")
        print("=" * 65)

        consumer_context = build_test_context(**metadata)

        prompt = build_evaluation_prompt(
            proposals=[proposal],
            state=state,
            consumer_context=consumer_context,
        )

        for trial in range(1, N_TRIALS + 1):

            response = llm.invoke(prompt)

            if not isinstance(response.content, str):
                raise TypeError("Expected string response from Groq.")

            evaluations = parse_evaluation_csv(
                response.content,
                proposal_count=1,
                require_complete=True,
            )

            evaluation = evaluations[0]

            print(f"\nTrial {trial}")

            for dimension, value in evaluation.items():
                print(f"{dimension}: {value}")