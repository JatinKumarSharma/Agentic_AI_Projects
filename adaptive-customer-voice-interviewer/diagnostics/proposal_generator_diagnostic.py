from agent.proposal_generator import generate_proposals


state = {

    "research_context": {
        "company_name": "Apple",
        "product_name": "iPhone",
        "product_category": "Smartphone",
        "research_goal": (
            "Understand consumer awareness, relationship, "
            "perception, sentiment, needs, pain points, "
            "purchase intent and constraints related to "
            "the iPhone."
        ),
    },

    "product_relationship": {
        "values": [
            {
                "value": "using iPhone 14 for almost two years",
                "source": "consumer_response",
                "confidence": 0.95,
            }
        ],
        "sufficiency": 0.60,
        "confidence": 0.95,
        "criticality": "critical",
        "verification_status": "unverified",
        "applicability_status": "applicable",
    },

    "feature_sentiment": {
        "camera": {
            "values": [
                {
                    "value": "really likes the camera",
                    "confidence": 0.95,
                }
            ]
        },
        "smoothness": {
            "values": [
                {
                    "value": "likes how smooth everything feels",
                    "confidence": 0.95,
                }
            ]
        },
    },

    "pain_points": {
        "battery": {
            "values": [
                {
                    "value": "battery life has become frustrating",
                    "confidence": 0.95,
                }
            ]
        }
    },

    "purchase_intent": {
        "values": [
            {
                "value": "considering upgrading next year",
                "confidence": 0.90,
            }
        ],
        "sufficiency": 0.30,
        "confidence": 0.90,
        "criticality": "high",
        "verification_status": "unverified",
        "applicability_status": "applicable",
    },

    "buying_constraints": {
        "price": {
            "values": [
                {
                    "value": "higher price makes upgrading hesitate",
                    "confidence": 0.95,
                }
            ]
        }
    },

    "questions_asked": [
        "Which smartphone are you currently using?",
        "What do you like most about your iPhone?"
    ],

    "unresolved_gaps": [

        {
            "parameter": "Product relationship",
            "category": "product_relationship",
            "gap_type": "insufficient",
            "remaining_distance": 0.25,
            "importance_weight": 0.92,
            "goal_alignment": 0.97,
            "criticality": "critical",
            "confidence": 0.95,
            "verification_required": True,
        },

        {
            "parameter": "Pain points",
            "category": "pain_points",
            "gap_type": "insufficient",
            "remaining_distance": 0.23,
            "importance_weight": 0.80,
            "goal_alignment": 0.90,
            "criticality": "high",
            "confidence": 0.95,
            "verification_required": True,
        },

        {
            "parameter": "Purchase intent",
            "category": "purchase_intent",
            "gap_type": "missing",
            "remaining_distance": 0.80,
            "importance_weight": 0.85,
            "goal_alignment": 0.92,
            "criticality": "high",
            "confidence": 0.0,
            "verification_required": True,
        },
    ],
}


result = generate_proposals(state)

print("\nGENERATED PROPOSALS")
print("=" * 70)

for proposal in result["proposals"]:

    print("\nTarget Gap:", proposal["target_gap"])
    print("Question:", proposal["question"])
    print("Proposal ID:", proposal["proposal_id"])