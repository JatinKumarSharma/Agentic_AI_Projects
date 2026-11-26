from agent.state_updater import update_state


base_state = {
    "meta_plan": {
        "research_goal": "Understand customer sentiment.",

        "required_information": [
            {
                "parameter": "Product relationship",
                "importance_weight": 0.92,
                "goal_alignment": 0.97,
                "sufficiency_threshold": 0.85,
                "criticality": "critical",
                "verification_requirement": True,
                "confidence_threshold": 0.85,
            },
            {
                "parameter": "Feature-specific sentiment",
                "importance_weight": 0.7,
                "goal_alignment": 0.85,
                "sufficiency_threshold": 0.75,
                "criticality": "medium",
                "verification_requirement": True,
                "confidence_threshold": 0.7,
            },
            {
                "parameter": "Pain points",
                "importance_weight": 0.8,
                "goal_alignment": 0.9,
                "sufficiency_threshold": 0.8,
                "criticality": "high",
                "verification_requirement": True,
                "confidence_threshold": 0.75,
            },
        ],

        "termination_criteria": {}
    },

    "product_relationship": None,
    "feature_sentiment": {},
    "pain_points": {},
}


# ============================================================
# FIRST RESPONSE
# ============================================================

state_1 = {
    **base_state,

    "latest_decomposition": {
        "evidence": [
            {
                "category": "product_relationship",
                "parameter": "device",
                "value": "using iPhone 14",
                "sentiment": None,
                "evidence_type": "explicit",
                "confidence": 0.95,
            },
            {
                "category": "feature_sentiment",
                "parameter": "camera",
                "value": "really like",
                "sentiment": "positive",
                "evidence_type": "explicit",
                "confidence": 0.95,
            },
            {
                "category": "pain_points",
                "parameter": "battery",
                "value": "frustrating",
                "sentiment": "negative",
                "evidence_type": "explicit",
                "confidence": 0.95,
            },
        ]
    }
}


print("\nFIRST RESPONSE")
print("=" * 50)

updated_state_1 = update_state(state_1)

print(updated_state_1["feature_sentiment"])
print(updated_state_1["pain_points"])


# ============================================================
# SECOND RESPONSE
# ============================================================

state_2 = {
    **updated_state_1,

    "latest_decomposition": {
        "evidence": [
            {
                "category": "feature_sentiment",
                "parameter": "camera",
                "value": "excellent for photos in low light",
                "sentiment": "positive",
                "evidence_type": "explicit",
                "confidence": 0.90,
            },
            {
                "category": "pain_points",
                "parameter": "battery",
                "value": "does not last a full day while traveling",
                "sentiment": "negative",
                "evidence_type": "explicit",
                "confidence": 0.95,
            },
        ]
    }
}


print("\nSECOND RESPONSE")
print("=" * 50)

updated_state_2 = update_state(state_2)

print(updated_state_2["feature_sentiment"])
print(updated_state_2["pain_points"])