"""Evidence-grounded analysis reports.

This module is intentionally deterministic and model-free. Claims are
projections of evidence already accepted by the LangGraph engine. A claim is
only emitted when every cited evidence item resolves to a response record
from the same session and its source quote is a verbatim excerpt of that
response. Unsupported claims are rejected, not softened.
"""

from collections import Counter
from typing import Any, Optional

from agent.provenance import stable_id, utc_now
from agent.response_outcomes import (
    derive_report_labels,
    normalized_outcomes,
)
from agent.tenancy import normalize_tenant_id


SINGLE_KNOWLEDGE_FIELDS = (
    "product_relationship",
    "product_awareness",
    "purchase_intent",
    "overall_sentiment",
    "product_perception",
)

DICTIONARY_KNOWLEDGE_FIELDS = (
    "feature_sentiment",
    "consumer_profile",
    "features",
    "needs",
    "pain_points",
    "use_cases",
    "aspirations",
    "expectations",
    "consumer_intent",
    "competitor_information",
    "budget_constraints",
    "buying_constraints",
    "adoption_barriers",
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _flatten_knowledge_items(state: dict) -> list[dict]:
    """Flatten knowledge into category/parameter records."""

    items = []

    for field in SINGLE_KNOWLEDGE_FIELDS:
        item = state.get(field)

        if isinstance(item, dict) and item:
            items.append({
                "category": field,
                "parameter": field,
                **item,
            })

    for field in DICTIONARY_KNOWLEDGE_FIELDS:
        field_value = state.get(field)

        if not isinstance(field_value, dict):
            continue

        for parameter, item in field_value.items():
            if isinstance(item, dict):
                items.append({
                    "category": field,
                    "parameter": parameter,
                    **item,
                })

    return items


def _response_text(record: dict) -> str:
    return _text(
        record.get("text")
        if "text" in record
        else record.get("response")
    )


def _response_records(state: dict, session_id: str) -> list[dict]:
    """Return response records, rebuilding legacy positional records."""

    records = state.get("response_records")

    if isinstance(records, list) and records:
        return [dict(record) for record in records if isinstance(record, dict)]

    questions = state.get("questions_asked") or []
    responses = state.get("responses") or []
    session = state.get("research_session") or {}
    participant_id = (
        session.get("participant_id")
        or f"participant-{session_id}"
    )

    rebuilt = []

    for index, response in enumerate(responses):
        rebuilt.append({
            "response_id": stable_id(
                "resp",
                session_id,
                index + 1,
                response,
            ),
            "turn": index + 1,
            "question": (
                questions[index]
                if index < len(questions)
                else None
            ),
            "text": response,
            "captured_at": None,
            "participant_id": participant_id,
        })

    return rebuilt


def _validate_claims(
    claims: list[dict],
    response_records: list[dict],
    session_id: str,
) -> dict:
    """Reject claims that are not fully grounded in this session."""

    issues = []
    responses_by_id: dict[str, dict] = {}

    for record in response_records:
        response_id = _text(record.get("response_id"))

        if not response_id:
            issues.append("response record has no response_id")
            continue

        if response_id in responses_by_id:
            issues.append(f"duplicate response_id: {response_id}")
            continue

        responses_by_id[response_id] = record

    claim_ids: set[str] = set()
    evidence_ids: set[str] = set()

    for claim in claims:
        claim_id = _text(claim.get("claim_id"))
        evidence_refs = claim.get("evidence") or []

        if not claim_id:
            issues.append("claim has no claim_id")

        if claim_id in claim_ids:
            issues.append(f"duplicate claim_id: {claim_id}")

        claim_ids.add(claim_id)

        if _text(claim.get("session_id")) != session_id:
            issues.append(f"claim {claim_id} has a different session_id")

        if not _text(claim.get("statement")):
            issues.append(f"claim {claim_id} has no statement")

        if not evidence_refs:
            issues.append(f"claim {claim_id} has no evidence references")

        for reference in evidence_refs:
            evidence_id = _text(reference.get("evidence_id"))
            response_id = _text(reference.get("response_id"))
            quote = _text(reference.get("source_quote"))

            if not evidence_id:
                issues.append(f"claim {claim_id} has an unnamed evidence item")
                continue

            if evidence_id in evidence_ids:
                issues.append(f"duplicate evidence_id: {evidence_id}")

            evidence_ids.add(evidence_id)

            if not quote:
                issues.append(
                    f"evidence {evidence_id} has no source quote"
                )
                continue

            response = responses_by_id.get(response_id)

            if response is None:
                issues.append(
                    f"evidence {evidence_id} cites unknown "
                    f"response {response_id}"
                )
                continue

            if quote not in _response_text(response):
                issues.append(
                    f"evidence {evidence_id} quote is not verbatim"
                )

    return {
        "valid": not issues,
        "claim_count": len(claims),
        "evidence_reference_count": len(evidence_ids),
        "issues": issues,
    }


def _build_claims(
    session_id: str,
    response_records: list[dict],
    knowledge_items: list[dict],
) -> list[dict]:
    claims = []

    for item in knowledge_items:
        category = _text(item.get("category")) or "uncategorized"
        parameter = _text(item.get("parameter")) or "unspecified"
        evidence_refs = []

        for evidence in item.get("values", []):
            if not isinstance(evidence, dict):
                continue

            evidence_refs.append({
                "evidence_id": _text(evidence.get("evidence_id")),
                "response_id": _text(evidence.get("response_id")),
                "turn": evidence.get("turn"),
                "captured_at": evidence.get("captured_at"),
                "participant_id": evidence.get("participant_id"),
                "value": _text(evidence.get("value")),
                "source_quote": _text(evidence.get("source_quote")),
                "sentiment": evidence.get("sentiment"),
                "confidence": evidence.get("confidence"),
                "evidence_type": evidence.get("evidence_type"),
            })

        if not evidence_refs:
            continue

        # Claim text is built only from verbatim source quotes. Normalized
        # evidence values remain metadata; they never become assertions.
        values = []
        for ref in evidence_refs:
            quote = ref["source_quote"]
            if quote and quote not in values:
                values.append(quote)
        evidence_ids = [
            ref["evidence_id"]
            for ref in evidence_refs
        ]
        sentiments = [
            ref["sentiment"]
            for ref in evidence_refs
            if ref["sentiment"]
        ]
        claim_id = stable_id(
            "claim",
            session_id,
            category,
            parameter,
            "|".join(evidence_ids),
        )

        claims.append({
            "claim_id": claim_id,
            "session_id": session_id,
            "claim_type": "grounded_knowledge",
            "category": category,
            "parameter": parameter,
            "statement": "; ".join(values),
            "sentiment": sentiments[0] if len(set(sentiments)) == 1 else (
                "mixed" if len(sentiments) > 1 else None
            ),
            "confidence": item.get("confidence"),
            "sufficiency": item.get("sufficiency"),
            "verification_status": item.get("verification_status"),
            "criticality": item.get("criticality"),
            "evidence_ids": evidence_ids,
            "evidence": evidence_refs,
        })

    return claims


def _build_summary(
    report_dimensions: dict,
    response_records: list[dict],
    claims: list[dict],
) -> dict:
    evidence_refs = [
        reference
        for claim in claims
        for reference in claim.get("evidence", [])
    ]

    captured_times = sorted(
        _text(record.get("captured_at"))
        for record in response_records
        if _text(record.get("captured_at"))
    )

    return {
        **report_dimensions,
        "generated_at": utc_now(),
        "response_count": len(response_records),
        "participant_count": len({
            _text(record.get("participant_id"))
            for record in response_records
            if _text(record.get("participant_id"))
        }),
        "claim_count": len(claims),
        "evidence_count": len(evidence_refs),
        "first_response_at": (
            captured_times[0] if captured_times else None
        ),
        "last_response_at": (
            captured_times[-1] if captured_times else None
        ),
        "sentiment_distribution": dict(sorted(Counter(
            _text(claim.get("sentiment")) or "not_applicable"
            for claim in claims
        ).items())),
        "category_distribution": dict(sorted(Counter(
            claim.get("category", "uncategorized")
            for claim in claims
        ).items())),
        "confidence_distribution": dict(sorted(Counter(
            _text(claim.get("confidence")) or "unknown"
            for claim in claims
        ).items())),
        "verification_distribution": dict(sorted(Counter(
            _text(claim.get("verification_status")) or "unknown"
            for claim in claims
        ).items())),
        "grounding": "deterministic_evidence_only",
    }


def _knowledge_items_from_evidence(items: list[dict]) -> list[dict]:
    """Regroup retrieved evidence into deterministic knowledge items."""

    grouped: dict[tuple[str, str], dict] = {}

    for item in items:
        category = _text(item.get("category")) or "uncategorized"
        parameter = _text(item.get("parameter")) or "unspecified"
        key = (category, parameter)

        group = grouped.setdefault(key, {
            "category": category,
            "parameter": parameter,
            "values": [],
            "confidence": item.get("item_confidence"),
            "sufficiency": item.get("item_sufficiency"),
            "criticality": item.get("item_criticality"),
            "verification_status": item.get("item_verification_status"),
        })

        group["values"].append({
            "evidence_id": _text(item.get("evidence_id")),
            "response_id": _text(item.get("response_id")),
            "turn": item.get("turn"),
            "captured_at": item.get("captured_at"),
            "participant_id": _text(item.get("participant_id")) or None,
            "value": item.get("value"),
            "source_quote": _text(item.get("source_quote")),
            "sentiment": item.get("sentiment"),
            "confidence": item.get("confidence"),
            "evidence_type": item.get("evidence_type"),
        })

    return [grouped[key] for key in sorted(grouped)]


def _build_report(
    session: dict,
    state: dict,
    response_records: list[dict],
    knowledge_items: list[dict],
) -> dict:
    """Build and validate a report from explicitly supplied evidence."""

    session_id = _text(session.get("session_id"))

    if not session_id:
        raise ValueError("A session_id is required for analysis reports.")

    research_session = state.get("research_session") or session
    period_start = _text(
        session.get("start_date")
        or research_session.get("start_date")
    ) or None
    period_end = _text(
        session.get("end_date")
        or research_session.get("end_date")
        or period_start
    ) or None
    customer_segment = _text(
        session.get("customer_segment")
        or research_session.get("customer_segment")
        or session.get("target_audience")
        or research_session.get("target_audience")
    ).lower() or "unspecified"
    labels = derive_report_labels(state)
    dimensions = {
        "session_id": session_id,
        "company_name": _text(
            session.get("company_name")
            or research_session.get("company_name")
        ) or None,
        "product_name": _text(
            session.get("product_name")
            or research_session.get("product_name")
        ) or None,
        "product_category": _text(
            session.get("product_category")
            or research_session.get("product_category")
        ) or "unspecified",
        "research_goal": _text(
            session.get("research_goal")
            or research_session.get("research_goal")
        ) or None,
        "customer_segment": customer_segment,
        "period_start": period_start,
        "period_end": period_end,
        "termination_reason": _text(state.get("termination_reason")) or None,
        **labels,
    }

    claims = _build_claims(
        session_id,
        response_records,
        knowledge_items,
    )
    validation = _validate_claims(
        claims,
        response_records,
        session_id,
    )

    if not validation["valid"]:
        raise ValueError(
            "Refusing to store an ungrounded analysis report: "
            + "; ".join(validation["issues"])
        )

    summary = _build_summary(
        dimensions,
        response_records,
        claims,
    )

    return {
        "report_id": f"report_{session_id}",
        "session_id": session_id,
        "generated_at": summary["generated_at"],
        "dimensions": dimensions,
        "summary": summary,
        "validation": validation,
        "claims": claims,
        "response_evidence": response_records,
        "response_outcomes": normalized_outcomes(state),
    }


def build_session_report(session: dict, state: dict) -> dict:
    """Build a validated report from the live graph state.

    This remains the writer-side path used while projecting a graph state into
    durable memory. Read-side report generation should use
    :func:`build_session_report_from_memory` instead.
    """

    state = state or {}
    session_id = _text(session.get("session_id"))

    if not session_id:
        raise ValueError("A session_id is required for analysis reports.")

    return _build_report(
        session,
        state,
        _response_records(state, session_id),
        _flatten_knowledge_items(state),
    )


def build_session_report_from_memory(
    session_id: str,
    tenant_id: str | None = None,
    *,
    session: dict | None = None,
) -> dict:
    """Build a validated report from authoritative research memory.

    Claims come from governed evidence retrieval, not from a live graph-state
    object. Evidence whose quote is not verbatim is rejected rather than
    silently omitted, so a suppressed citation cannot make an ungrounded
    report look clean.
    """

    from agent.evidence_retrieval import (
        get_latest_memory_snapshot,
        retrieve_evidence,
    )

    tenant = normalize_tenant_id(tenant_id)
    record = get_latest_memory_snapshot(tenant, session_id)

    if record is None:
        raise ValueError(
            "No authoritative memory snapshot is available for this session."
        )

    state = record["payload"].get("state") or {}
    report_session = session or (state.get("research_session") or {})
    report_session = {**report_session, "session_id": session_id}

    all_evidence = retrieve_evidence(
        tenant_id=tenant,
        session_id=session_id,
        include_unverified=True,
    )
    suppressed = [
        item
        for item in all_evidence
        if not item.get("quote_is_verbatim")
    ]

    if suppressed:
        issues = [
            f"evidence {_text(item.get('evidence_id'))} quote is not verbatim"
            for item in suppressed
        ]
        raise ValueError(
            "Refusing to store an ungrounded analysis report: "
            + "; ".join(issues)
        )

    verified_evidence = retrieve_evidence(
        tenant_id=tenant,
        session_id=session_id,
    )

    return _build_report(
        report_session,
        state,
        _response_records(state, session_id),
        _knowledge_items_from_evidence(verified_evidence),
    )


def _tally(
    records: list[dict],
    key: str,
) -> list[dict]:
    counters: dict[str, Counter] = {}

    for record in records:
        value = _text(record.get(key)) or "unspecified"
        bucket = counters.setdefault(value, Counter())
        bucket["report_count"] += int(record.get("report_count", 0))
        bucket["claim_count"] += int(record.get("claim_count", 0))
        bucket["evidence_count"] += int(record.get("evidence_count", 0))

    return [
        {
            "value": value,
            "report_count": counts.get("report_count", 0),
            "claim_count": counts.get("claim_count", 0),
            "evidence_count": counts.get("evidence_count", 0),
        }
        for value, counts in sorted(counters.items())
    ]


def aggregate_reports(reports: list[dict]) -> dict:
    """Aggregate validated reports for period/segment/category analysis."""

    claim_rows = []
    evidence_rows = []

    for report in reports:
        dimensions = report.get("dimensions") or {}
        summary = report.get("summary") or {}

        for claim in report.get("claims", []):
            claim_rows.append({
                **dimensions,
                "category": claim.get("category"),
                "parameter": claim.get("parameter"),
                "claim_count": 1,
                "evidence_count": len(claim.get("evidence", [])),
            })
            evidence_rows.append({
                **dimensions,
                "category": claim.get("category"),
                "parameter": claim.get("parameter"),
                "sentiment": _text(claim.get("sentiment")) or "not_applicable",
                "evidence_count": len(claim.get("evidence", [])),
            })

    report_rows = [
        {
            **(report.get("dimensions") or {}),
            "claim_count": int(
                (report.get("summary") or {}).get("claim_count", 0)
            ),
            "evidence_count": int(
                (report.get("summary") or {}).get("evidence_count", 0)
            ),
            "report_count": 1,
        }
        for report in reports
    ]

    period_rows = [
        {
            **row,
            "period": _text(row.get("period_start"))[:7] or "unspecified",
        }
        for row in report_rows
    ]

    tag_rows = []

    for report in reports:
        summary = report.get("summary") or {}
        dimensions = report.get("dimensions") or {}

        for tag in dimensions.get("tags", []):
            tag_rows.append({
                "value": tag,
                "report_count": 1,
                "claim_count": int(summary.get("claim_count", 0)),
                "evidence_count": int(summary.get("evidence_count", 0)),
            })

    return {
        "report_count": len(reports),
        "claim_count": len(claim_rows),
        "evidence_count": sum(
            row["evidence_count"] for row in claim_rows
        ),
        "by_customer_segment": _tally(report_rows, "customer_segment"),
        "by_product_category": _tally(report_rows, "product_category"),
        "by_research_goal": _tally(report_rows, "research_goal"),
        "by_period": _tally(period_rows, "period"),
        "by_claim_category": _tally(claim_rows, "category"),
        "by_sentiment": _tally(evidence_rows, "sentiment"),
        "by_parameter": _tally(claim_rows, "parameter"),
        "by_status_label": _tally(report_rows, "status_label"),
        "by_response_pattern_label": _tally(
            report_rows,
            "response_pattern_label",
        ),
        "by_tag": _tally(tag_rows, "value"),
    }
