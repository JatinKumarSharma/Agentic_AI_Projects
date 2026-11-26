
import pytest

from agent.gap_analyzer import analyze_gaps


def make_requirement(
    parameter,
    sufficiency_requirement="adequate",
    verification_requirement=False,
    importance="high",
    goal_alignment="high",
    criticality="high",
):
    return {
        "parameter": parameter,
        "importance": importance,
        "goal_alignment": goal_alignment,
        "sufficiency_requirement": sufficiency_requirement,
        "criticality": criticality,
        "verification_requirement": verification_requirement,
        "confidence_requirement": "medium",
        "expected_goal_improvement": "high",
        "estimated_cost": "medium",
        "estimated_consumer_burden": "medium",
    }


def make_knowledge(
    values=None,
    sufficiency="adequate",
    confidence="high",
    verification_status="verified",
    applicability_status="applicable",
    criticality="high",
):
    return {
        "values": values or [],
        "sufficiency": sufficiency,
        "confidence": confidence,
        "criticality": criticality,
        "verification_status": verification_status,
        "applicability_status": applicability_status,
    }


def make_state(requirements, **knowledge):
    return {
        "meta_plan": {
            "research_goal": "Understand consumer sentiment.",
            "required_information": requirements,
        },
        **knowledge,
    }


def test_missing_information_creates_missing_gap():
    state = make_state([
        make_requirement("Product relationship")
    ])

    result = analyze_gaps(state)

    gaps = result["unresolved_gaps"]

    assert len(gaps) == 1
    assert gaps[0]["parameter"] == "Product relationship"
    assert gaps[0]["category"] == "product_relationship"
    assert gaps[0]["gap_type"] == "missing"


def test_insufficient_information_creates_insufficient_gap():
    state = make_state(
        [make_requirement(
            "Product relationship",
            sufficiency_requirement="adequate",
        )],
        product_relationship=make_knowledge(
            values=[{"value": "Uses iPhone 14"}],
            sufficiency="basic",
            verification_status="verified",
        ),
    )

    result = analyze_gaps(state)

    gaps = result["unresolved_gaps"]

    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "insufficient"


def test_unverified_information_creates_verification_gap():
    state = make_state(
        [make_requirement(
            "Product relationship",
            sufficiency_requirement="basic",
            verification_requirement=True,
        )],
        product_relationship=make_knowledge(
            values=[{"value": "Uses iPhone 14"}],
            sufficiency="adequate",
            verification_status="unverified",
        ),
    )

    result = analyze_gaps(state)

    gaps = result["unresolved_gaps"]

    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "verification_required"
    assert gaps[0]["verification_required"] is True


def test_adequate_verified_information_creates_no_gap():
    state = make_state(
        [make_requirement(
            "Product relationship",
            sufficiency_requirement="adequate",
            verification_requirement=True,
        )],
        product_relationship=make_knowledge(
            sufficiency="adequate",
            verification_status="verified",
        ),
    )

    result = analyze_gaps(state)

    assert result["unresolved_gaps"] == []


def test_not_applicable_information_creates_no_gap():
    state = make_state(
        [make_requirement("Product relationship")],
        product_relationship=make_knowledge(
            sufficiency="missing",
            applicability_status="not_applicable",
        ),
    )

    result = analyze_gaps(state)

    assert result["unresolved_gaps"] == []


def test_missing_meta_plan_returns_empty_gap_list():
    result = analyze_gaps({})

    assert result == {"unresolved_gaps": []}


def test_multiple_knowledge_uses_weakest_sufficiency():
    state = make_state(
        [make_requirement(
            "Feature-specific sentiment",
            sufficiency_requirement="adequate",
        )],
        feature_sentiment={
            "camera": make_knowledge(
                sufficiency="comprehensive",
            ),
            "battery": make_knowledge(
                sufficiency="basic",
            ),
        },
    )

    result = analyze_gaps(state)

    gaps = result["unresolved_gaps"]

    assert len(gaps) == 1
    assert gaps[0]["gap_type"] == "insufficient"


def test_gap_preserves_requirement_metadata():
    state = make_state(
        [make_requirement(
            "Product relationship",
            importance="critical",
            goal_alignment="high",
            criticality="critical",
            verification_requirement=True,
        )],
    )

    result = analyze_gaps(state)

    gap = result["unresolved_gaps"][0]

    assert gap["importance"] == "critical"
    assert gap["goal_alignment"] == "high"
    assert gap["criticality"] == "critical"
    assert gap["verification_required"] is True


def test_unsupported_requirement_parameter_raises_error():
    state = make_state([
        make_requirement("Unknown research parameter")
    ])

    with pytest.raises(ValueError, match="Unsupported"):
        analyze_gaps(state)
