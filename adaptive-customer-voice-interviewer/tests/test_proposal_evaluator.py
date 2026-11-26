
import pytest
from unittest.mock import Mock

import agent.proposal_evaluator as evaluator


CSV_HEADER = (
    "proposal_index,expected_information_gain,"
    "expected_state_improvement,risk,cost,consumer_burden"
)

VALID_ROW_0 = "0,high,medium,low,low,medium"
VALID_ROW_1 = "1,medium,high,low,medium,low"


def make_csv(*rows):
    return "\n".join([CSV_HEADER, *rows])


def test_parser_accepts_valid_complete_csv():
    response = make_csv(VALID_ROW_0, VALID_ROW_1)

    result = evaluator.parse_evaluation_csv(
        response=response,
        proposal_count=2,
    )

    assert result == {
        0: {
            "expected_information_gain": "high",
            "expected_state_improvement": "medium",
            "risk": "low",
            "cost": "low",
            "consumer_burden": "medium",
        },
        1: {
            "expected_information_gain": "medium",
            "expected_state_improvement": "high",
            "risk": "low",
            "cost": "medium",
            "consumer_burden": "low",
        },
    }


def test_parser_allows_partial_csv_when_requested():
    response = make_csv(VALID_ROW_0)

    result = evaluator.parse_evaluation_csv(
        response=response,
        proposal_count=2,
        require_complete=False,
    )

    assert list(result.keys()) == [0]


def test_parser_rejects_missing_evaluations():
    response = make_csv(VALID_ROW_0)

    with pytest.raises(ValueError, match="Missing evaluations"):
        evaluator.parse_evaluation_csv(
            response=response,
            proposal_count=2,
            require_complete=True,
        )


def test_parser_rejects_duplicate_indices():
    response = make_csv(
        VALID_ROW_0,
        VALID_ROW_0,
    )

    with pytest.raises(ValueError, match="Duplicate evaluation"):
        evaluator.parse_evaluation_csv(
            response=response,
            proposal_count=2,
        )


@pytest.mark.parametrize("invalid_index", ["abc", "-1"])
def test_parser_rejects_invalid_indices(invalid_index):
    response = make_csv(
        f"{invalid_index},high,medium,low,low,medium"
    )

    with pytest.raises(ValueError):
        evaluator.parse_evaluation_csv(
            response=response,
            proposal_count=2,
            require_complete=False,
        )


def test_parser_ignores_out_of_range_indices_when_not_required():
    response = make_csv("2,high,medium,low,low,medium")

    parsed = evaluator.parse_evaluation_csv(
        response=response,
        proposal_count=2,
        require_complete=False,
    )

    assert parsed == {}


def test_parser_rejects_invalid_evaluation_values():
    response = make_csv(
        "0,very_high,medium,low,low,medium"
    )

    with pytest.raises(ValueError, match="Invalid value"):
        evaluator.parse_evaluation_csv(
            response=response,
            proposal_count=1,
        )


def test_parser_rejects_invalid_csv_header():
    response = (
        "proposal_id,expected_information_gain,"
        "expected_state_improvement,risk,cost,consumer_burden\n"
        + VALID_ROW_0
    )

    with pytest.raises(ValueError, match="No CSV header or rows found"):
        evaluator.parse_evaluation_csv(
            response=response,
            proposal_count=1,
        )


def test_parser_rejects_rows_with_wrong_number_of_columns():
    response = (
        CSV_HEADER
        + "\n"
        + "0,high,medium,low"
    )

    with pytest.raises(ValueError, match="Missing evaluations"):
        evaluator.parse_evaluation_csv(
            response=response,
            proposal_count=1,
        )


def test_evaluator_rejects_duplicate_input_proposal_ids(
    monkeypatch,
):
    proposals = [
        {
            "proposal_id": "duplicate_id",
            "target_gap": "Product awareness",
            "question": "Question A?",
        },
        {
            "proposal_id": "duplicate_id",
            "target_gap": "Purchase intent",
            "question": "Question B?",
        },
    ]

    mock_llm = Mock()
    monkeypatch.setattr(
        evaluator,
        "llm",
        mock_llm,
    )

    with pytest.raises(
        ValueError,
        match="duplicate proposal IDs",
    ):
        evaluator.evaluate_proposals(
            {"proposals": proposals}
        )

    mock_llm.invoke.assert_not_called()


def test_evaluator_context_includes_knowledge_and_evidence_metadata(
    monkeypatch,
):
    monkeypatch.setattr(
        evaluator,
        "build_consumer_context",
        lambda state: "Base consumer context",
    )

    state = {
        "purchase_intent": {
            "values": [
                {
                    "value": "Purchases twice a month",
                    "source": "Consumer interview",
                    "confidence": "high",
                    "evidence_type": "explicit",
                    "sentiment": "neutral",
                }
            ],
            "sufficiency": "adequate",
            "confidence": "high",
            "criticality": "high",
            "verification_status": "verified",
            "applicability_status": "applicable",
        }
    }

    context = evaluator.build_evaluator_consumer_context(state)

    assert "Base consumer context" in context
    assert "KNOWLEDGE QUALITY METADATA" in context

    # KnowledgeItem metadata
    assert "Sufficiency: adequate" in context
    assert "Confidence: high" in context
    assert "Criticality: high" in context
    assert "Verification status: verified" in context
    assert "Applicability status: applicable" in context

    # EvidenceItem metadata
    assert "Purchases twice a month" in context
    assert "Consumer interview" in context
    assert "Evidence type: explicit" in context
    assert "Sentiment: neutral" in context

def test_evaluate_proposals_passes_enriched_context_to_groups(monkeypatch):
    captured_contexts = []

    monkeypatch.setattr(
        evaluator,
        "build_evaluator_consumer_context",
        lambda state: "ENRICHED_CONTEXT_SENTINEL",
    )

    def fake_evaluate_group(target_gap, proposals, state, consumer_context):
        captured_contexts.append(consumer_context)
        return {
            proposals[0]["proposal_id"]: {
                "expected_information_gain": "high",
                "expected_state_improvement": "medium",
                "risk": "low",
                "cost": "low",
                "consumer_burden": "low",
            }
        }

    monkeypatch.setattr(
        evaluator,
        "evaluate_proposal_group",
        fake_evaluate_group,
    )

    state = {
        "proposals": [
            {
                "proposal_id": "proposal_1",
                "target_gap": "Purchase intent",
                "question": "How likely are you to buy?",
            }
        ]
    }

    result = evaluator.evaluate_proposals(state)

    assert captured_contexts == ["ENRICHED_CONTEXT_SENTINEL"]
    assert len(result["evaluated_proposals"]) == 1


def test_evaluator_recovers_from_empty_primary_response(monkeypatch):
    proposals = [
        {
            "proposal_id": "proposal_1",
            "target_gap": "Product relationship",
            "question": "How do you use the product today?",
        },
        {
            "proposal_id": "proposal_2",
            "target_gap": "Product relationship",
            "question": "Which workflow would be hardest to replace?",
        },
    ]
    state = {
        "proposals": proposals,
        "research_context": {"research_goal": "Test the live flow."},
    }
    recovery_csv = make_csv(
        "0,high,medium,low,low,medium",
        "1,medium,high,low,medium,low",
    )

    fake_llm = Mock()
    fake_llm.invoke.side_effect = [
        Mock(content=""),
        Mock(content=recovery_csv),
    ]
    monkeypatch.setattr(evaluator, "llm", fake_llm)

    result = evaluator.evaluate_proposals(state)

    assert fake_llm.invoke.call_count == 2
    assert [p["proposal_id"] for p in result["evaluated_proposals"]] == [
        "proposal_1",
        "proposal_2",
    ]
    assert result["evaluated_proposals"][0][
        "expected_information_gain"
    ] == "high"
    assert result["evaluated_proposals"][1][
        "expected_state_improvement"
    ] == "high"


def test_evaluate_proposals_survives_terminal_rate_limit(monkeypatch):
    monkeypatch.setattr(evaluator.time, "sleep", lambda delay: None)

    class RateLimitError(Exception):
        pass

    fake_llm = Mock()
    fake_llm.invoke.side_effect = RateLimitError("rate limited")
    monkeypatch.setattr(evaluator, "llm", fake_llm)

    state = {
        "proposals": [
            {
                "proposal_id": "proposal_1",
                "target_gap": "Usage experience",
                "question": "What works well?",
            },
            {
                "proposal_id": "proposal_2",
                "target_gap": "Usage experience",
                "question": "What is difficult?",
            },
        ],
        "research_context": {"research_goal": "Test the live flow."},
    }

    result = evaluator.evaluate_proposals(state)
    evaluations = result["evaluated_proposals"]

    assert len(evaluations) == 2
    assert all(
        item["expected_information_gain"] == "medium"
        and item["expected_state_improvement"] == "medium"
        and item["risk"] == "medium"
        and item["cost"] == "medium"
        and item["consumer_burden"] == "medium"
        for item in evaluations
    )


def test_rate_limit_retry_defaults_keep_live_interviews_fast(monkeypatch):
    sleeps = []
    monkeypatch.setattr(evaluator.time, "sleep", sleeps.append)

    class RateLimitError(Exception):
        def __init__(self):
            super().__init__("rate limited")
            self.response = type(
                "Response",
                (),
                {"headers": {"retry-after": "1665"}},
            )()

    attempts = []

    class FlakyModel:
        def invoke(self, prompt):
            attempts.append(prompt)
            if len(attempts) < 2:
                raise RateLimitError()
            return "ok"

    result = evaluator.invoke_with_rate_limit_retry(FlakyModel(), "prompt")

    assert result == "ok"
    assert len(attempts) == 2
    assert sleeps == [1.0]


def test_rate_limit_retry_caps_long_provider_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr(evaluator.time, "sleep", sleeps.append)

    class RateLimitError(Exception):
        def __init__(self, retry_after):
            super().__init__("rate limited")
            self.response = type(
                "Response",
                (),
                {"headers": {"retry-after": str(retry_after)}},
            )()

    attempts = []

    class FlakyModel:
        def invoke(self, prompt):
            attempts.append(prompt)
            if len(attempts) < 3:
                raise RateLimitError(1665)
            return "ok"

    result = evaluator.invoke_with_rate_limit_retry(
        FlakyModel(),
        "prompt",
        max_attempts=4,
        max_delay=15.0,
    )

    assert result == "ok"
    assert len(attempts) == 3
    assert sleeps == [15.0, 15.0]
    