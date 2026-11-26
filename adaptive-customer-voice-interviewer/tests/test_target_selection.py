
from agent.proposal_generator import select_information_targets


def make_target(
    parameter,
    criticality="medium",
    importance="medium",
    goal_alignment="medium",
    expected_goal_improvement="medium",
    estimated_cost="medium",
    estimated_consumer_burden="medium",
):
    return {
        "parameter": parameter,
        "criticality": criticality,
        "importance": importance,
        "goal_alignment": goal_alignment,
        "expected_goal_improvement": expected_goal_improvement,
        "estimated_cost": estimated_cost,
        "estimated_consumer_burden": estimated_consumer_burden,
    }


def test_only_two_targets_are_selected():
    targets = [
        make_target(f"Target {i}", importance="high")
        for i in range(5)
    ]

    selected = select_information_targets(targets)

    assert len(selected) == 2


def test_priority_filtering_happens_before_efficiency_selection():
    targets = [
        make_target(
            "Low priority but cheap",
            criticality="low",
            importance="low",
            goal_alignment="low",
            expected_goal_improvement="high",
            estimated_cost="low",
            estimated_consumer_burden="low",
        ),
        make_target(
            "High priority A",
            criticality="high",
            importance="high",
            goal_alignment="high",
            expected_goal_improvement="medium",
        ),
        make_target(
            "High priority B",
            criticality="high",
            importance="high",
            goal_alignment="medium",
            expected_goal_improvement="medium",
        ),
        make_target(
            "High priority C",
            criticality="high",
            importance="medium",
            goal_alignment="high",
            expected_goal_improvement="medium",
        ),
    ]

    selected = select_information_targets(targets)

    selected_names = {
        target["parameter"]
        for target in selected
    }

    assert "Low priority but cheap" not in selected_names
    assert len(selected) == 2


def test_expected_goal_improvement_is_prioritized():
    targets = [
        make_target(
            "High improvement",
            expected_goal_improvement="high",
        ),
        make_target(
            "Low improvement",
            expected_goal_improvement="low",
        ),
        make_target(
            "Medium improvement",
            expected_goal_improvement="medium",
        ),
    ]

    selected = select_information_targets(targets)

    assert selected[0]["parameter"] == "High improvement"


def test_lower_cost_is_preferred_when_other_factors_tie():
    targets = [
        make_target(
            "Expensive",
            expected_goal_improvement="high",
            estimated_cost="high",
        ),
        make_target(
            "Affordable",
            expected_goal_improvement="high",
            estimated_cost="low",
        ),
        make_target(
            "Medium cost",
            expected_goal_improvement="high",
            estimated_cost="medium",
        ),
    ]

    selected = select_information_targets(targets)

    assert selected[0]["parameter"] == "Affordable"


def test_lower_consumer_burden_is_preferred_when_other_factors_tie():
    targets = [
        make_target(
            "High burden",
            expected_goal_improvement="high",
            estimated_cost="low",
            estimated_consumer_burden="high",
        ),
        make_target(
            "Low burden",
            expected_goal_improvement="high",
            estimated_cost="low",
            estimated_consumer_burden="low",
        ),
        make_target(
            "Medium burden",
            expected_goal_improvement="high",
            estimated_cost="low",
            estimated_consumer_burden="medium",
        ),
    ]

    selected = select_information_targets(targets)

    assert selected[0]["parameter"] == "Low burden"


def test_criticality_protects_critical_target():
    targets = [
        make_target(
            "Critical target",
            criticality="critical",
            expected_goal_improvement="low",
            estimated_cost="high",
            estimated_consumer_burden="high",
        ),
        make_target(
            "Noncritical target A",
            criticality="high",
            expected_goal_improvement="high",
            estimated_cost="low",
            estimated_consumer_burden="low",
        ),
        make_target(
            "Noncritical target B",
            criticality="high",
            expected_goal_improvement="high",
            estimated_cost="low",
            estimated_consumer_burden="low",
        ),
    ]

    selected = select_information_targets(targets)

    selected_names = {
        target["parameter"]
        for target in selected
    }

    assert "Critical target" in selected_names


def test_missing_estimates_remain_backward_compatible():
    targets = [
        {
            "parameter": "Legacy target A",
            "criticality": "high",
            "importance": "high",
            "goal_alignment": "high",
        },
        {
            "parameter": "Legacy target B",
            "criticality": "medium",
            "importance": "medium",
            "goal_alignment": "medium",
        },
    ]

    selected = select_information_targets(targets)

    assert len(selected) == 2


def test_empty_input_returns_empty_list():
    assert select_information_targets([]) == []


def test_single_candidate_returns_that_candidate():
    target = make_target("Only target")

    selected = select_information_targets([target])

    assert len(selected) == 1
    assert selected[0]["parameter"] == "Only target"
