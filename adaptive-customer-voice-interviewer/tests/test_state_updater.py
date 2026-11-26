from agent.state_updater import update_state


def make_initial_state():
    return {
        "meta_plan": {
            "required_information": [
                {
                    "parameter": "Product relationship",
                    "criticality": "high",
                },
                {
                    "parameter": "Feature sentiment",
                    "criticality": "high",
                },
                {
                    "parameter": "Pain points",
                    "criticality": "critical",
                },
            ]
        },
        "product_relationship": None,
        "feature_sentiment": {},
        "pain_points": {},
        "latest_decomposition": {
            "evidence": [
                {
                    "category": "product_relationship",
                    "parameter": "device",
                    "value": "using an iPhone 14",
                    "sentiment": None,
                    "evidence_type": "explicit",
                    "confidence": "high",
                },
                {
                    "category": "feature_sentiment",
                    "parameter": "camera",
                    "value": "really likes the camera",
                    "sentiment": "positive",
                    "evidence_type": "explicit",
                    "confidence": "high",
                },
                {
                    "category": "pain_points",
                    "parameter": "battery",
                    "value": "battery life is frustrating",
                    "sentiment": "negative",
                    "evidence_type": "explicit",
                    "confidence": "high",
                },
            ]
        },
    }


def test_update_state_skips_empty_decomposition():
    assert update_state({"latest_decomposition": None}) == {}
    assert update_state({}) == {}


def test_update_state_creates_and_extends_qualitative_knowledge():
    state = make_initial_state()

    first = update_state(state)

    assert state["product_relationship"] is None
    assert state["feature_sentiment"] == {}
    assert state["pain_points"] == {}

    relationship = first["product_relationship"]
    assert relationship["values"] == [
        {
            "value": "using an iPhone 14",
            "source": "consumer_response",
            "confidence": "high",
            "sentiment": None,
            "evidence_type": "explicit",
        }
    ]
    assert relationship["sufficiency"] == "basic"
    assert relationship["confidence"] == "high"
    assert relationship["criticality"] == "high"
    assert relationship["verification_status"] == "unverified"
    assert relationship["applicability_status"] == "applicable"

    camera = first["feature_sentiment"]["camera"]
    assert camera["criticality"] == "high"
    assert camera["values"][0]["sentiment"] == "positive"

    battery = first["pain_points"]["battery"]
    assert battery["criticality"] == "critical"
    assert battery["values"][0]["sentiment"] == "negative"

    second_state = {
        **first,
        "latest_decomposition": {
            "evidence": [
                {
                    "category": "feature_sentiment",
                    "parameter": "camera",
                    "value": "excellent for low-light photos",
                    "sentiment": "positive",
                    "evidence_type": "explicit",
                    "confidence": "high",
                },
                {
                    "category": "pain_points",
                    "parameter": "battery",
                    "value": "does not last a full day while traveling",
                    "sentiment": "negative",
                    "evidence_type": "explicit",
                    "confidence": "high",
                },
            ]
        },
    }

    second = update_state(second_state)

    assert len(first["feature_sentiment"]["camera"]["values"]) == 1
    assert len(second["feature_sentiment"]["camera"]["values"]) == 2
    assert [
        item["value"]
        for item in second["feature_sentiment"]["camera"]["values"]
    ] == [
        "really likes the camera",
        "excellent for low-light photos",
    ]

    camera = second["feature_sentiment"]["camera"]
    assert camera["sufficiency"] == "adequate"
    assert camera["confidence"] == "high"
    assert camera["verification_status"] == "verified"

    battery = second["pain_points"]["battery"]
    assert battery["sufficiency"] == "adequate"
    assert battery["verification_status"] == "verified"
    assert battery["values"][1]["value"] == (
        "does not last a full day while traveling"
    )
