"""Governed retrieval over authoritative research memory.

Analysis code should cite evidence that has already been accepted by the
interview engine. This module reads append-only ``research_memory_records``,
selects the latest authoritative snapshot per session, and returns normalized
citation records with provenance. It never invents, rewrites, or infers quotes.
"""

from __future__ import annotations

import re
from typing import Any

from agent.persistence import get_repository
from agent.provenance import participant_id_for_session, stable_id
from agent.read_model import flatten_knowledge_items
from agent.tenancy import normalize_tenant_id

STATE_SNAPSHOT_RECORD_TYPE = "state_snapshot"

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

_SEARCH_FIELD_WEIGHTS = (
    ("source_quote", 5),
    ("value", 4),
    ("response_text", 2),
    ("question", 1),
    ("category", 1),
    ("parameter", 1),
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _response_text(record: Any) -> str:
    if isinstance(record, str):
        return _text(record)

    if not isinstance(record, dict):
        return ""

    if "text" in record:
        return _text(record.get("text"))

    return _text(record.get("response"))


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
        or participant_id_for_session({"session_id": session_id, **session})
    )

    rebuilt = []

    for index, response in enumerate(responses):
        rebuilt.append({
            "response_id": stable_id(
                "resp",
                session_id,
                index + 1,
                _response_text(response),
            ),
            "turn": index + 1,
            "question": (
                questions[index]
                if index < len(questions)
                else None
            ),
            "text": _response_text(response),
            "captured_at": None,
            "participant_id": participant_id,
        })

    return rebuilt


def _latest_snapshots(
    tenant_id: str,
    session_id: str | None,
) -> list[dict]:
    records = get_repository().list_research_memory_records(
        tenant_id=tenant_id,
        session_id=session_id,
        record_type=STATE_SNAPSHOT_RECORD_TYPE,
    )

    latest: dict[tuple[str, str], dict] = {}
    for record in records:
        key = (record["tenant_id"], record["session_id"])
        # Records are already newest first; keep the first per session.
        latest.setdefault(key, record)

    return sorted(
        latest.values(),
        key=lambda record: (
            record["session_id"],
            record["captured_at"] or "",
            record["record_id"],
        ),
    )


def get_latest_memory_snapshot(
    tenant_id: str | None,
    session_id: str,
) -> dict | None:
    """Return the latest authoritative state snapshot for one session."""

    tenant = normalize_tenant_id(tenant_id)
    records = _latest_snapshots(tenant, session_id)

    if not records:
        return None

    return records[0]


def retrieve_evidence(
    *,
    tenant_id: str | None = None,
    session_id: str | None = None,
    evidence_id: str | None = None,
    response_id: str | None = None,
    turn: int | None = None,
    category: str | None = None,
    parameter: str | None = None,
    include_unverified: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """Return tenant-scoped, grounded citations from authoritative memory.

    Unverifiable quotes are suppressed unless ``include_unverified`` is set.
    Results are deterministic and ordered by session, turn, capture time, and
    evidence ID.
    """

    tenant = normalize_tenant_id(tenant_id)
    wanted_turn = _as_int(turn) if turn is not None else None

    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    results: list[dict] = []

    for record in _latest_snapshots(tenant, session_id):
        state = record["payload"].get("state") or {}
        response_records = _response_records(state, record["session_id"])
        responses_by_id = {
            _text(response.get("response_id")): response
            for response in response_records
            if _text(response.get("response_id"))
        }

        for item in flatten_knowledge_items(state):
            item_category = _text(item.get("category"))
            item_parameter = _text(item.get("parameter"))

            if category and item_category != category:
                continue
            if parameter and item_parameter != parameter:
                continue

            for evidence in item.get("values", []):
                if not isinstance(evidence, dict):
                    continue

                current_evidence_id = _text(evidence.get("evidence_id"))
                current_response_id = _text(evidence.get("response_id"))

                if evidence_id and current_evidence_id != evidence_id:
                    continue
                if response_id and current_response_id != response_id:
                    continue

                response = responses_by_id.get(current_response_id, {})
                response_text = _response_text(response)
                quote = _text(evidence.get("source_quote"))
                quote_is_verbatim = bool(quote and quote in response_text)

                if not include_unverified and not quote_is_verbatim:
                    continue

                evidence_turn = _as_int(evidence.get("turn"))
                if evidence_turn is None:
                    evidence_turn = _as_int(response.get("turn"))
                if evidence_turn is None:
                    evidence_turn = 0

                if wanted_turn is not None and evidence_turn != wanted_turn:
                    continue

                results.append({
                    "tenant_id": record["tenant_id"],
                    "session_id": record["session_id"],
                    "record_id": record["record_id"],
                    "record_captured_at": record["captured_at"],
                    "schema_version": record["schema_version"],
                    "record_type": record["record_type"],
                    "source": record["source"],
                    "evidence_id": current_evidence_id,
                    "response_id": current_response_id,
                    "turn": evidence_turn,
                    "captured_at": (
                        evidence.get("captured_at")
                        or response.get("captured_at")
                    ),
                    "participant_id": (
                        _text(evidence.get("participant_id"))
                        or _text(response.get("participant_id"))
                    ),
                    "category": item_category,
                    "parameter": item_parameter,
                    "value": evidence.get("value"),
                    "source_quote": quote,
                    "sentiment": evidence.get("sentiment"),
                    "confidence": evidence.get("confidence"),
                    "evidence_type": evidence.get("evidence_type"),
                    "question": response.get("question"),
                    "response_text": response_text,
                    "quote_is_verbatim": quote_is_verbatim,
                    # Item-level metadata is carried separately from the
                    # evidence-level fields so report builders can reconstruct
                    # knowledge groups without reparsing loose graph state.
                    "item_confidence": item.get("confidence"),
                    "item_sufficiency": item.get("sufficiency"),
                    "item_criticality": item.get("criticality"),
                    "item_verification_status": (
                        item.get("verification_status")
                    ),
                })

    results.sort(
        key=lambda result: (
            result["session_id"],
            result["turn"],
            result["captured_at"] or "",
            result["evidence_id"],
        )
    )

    if limit is not None:
        return results[:limit]

    return results


def _lexical_score(item: dict, terms: list[str], phrase: str) -> int:
    score = 0

    for field, weight in _SEARCH_FIELD_WEIGHTS:
        field_tokens = _TOKEN_PATTERN.findall(_text(item.get(field)).lower())

        for term in terms:
            score += weight * field_tokens.count(term)

    if phrase:
        quote = _text(item.get("source_quote")).lower()
        response_text = _text(item.get("response_text")).lower()

        if phrase in quote:
            score += 10
        elif phrase in response_text:
            score += 4

    return score


def search_evidence(
    *,
    query: str,
    tenant_id: str | None = None,
    session_id: str | None = None,
    evidence_id: str | None = None,
    response_id: str | None = None,
    turn: int | None = None,
    category: str | None = None,
    parameter: str | None = None,
    include_unverified: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """Return deterministic lexical matches over governed evidence.

    Ranking is model-free: weighted term frequency across citation fields with
    bonuses for exact phrases in the source quote or response. Ties preserve
    the deterministic order from :func:`retrieve_evidence`.
    """

    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    common = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "evidence_id": evidence_id,
        "response_id": response_id,
        "turn": turn,
        "category": category,
        "parameter": parameter,
        "include_unverified": include_unverified,
    }

    normalized_query = " ".join(_text(query).lower().split())
    terms = _TOKEN_PATTERN.findall(normalized_query)

    if not terms:
        return retrieve_evidence(**common, limit=limit)

    items = retrieve_evidence(**common)
    scored = []

    for index, item in enumerate(items):
        score = _lexical_score(item, terms, normalized_query)

        if score > 0:
            scored.append((score, index, item))

    scored.sort(key=lambda row: (-row[0], row[1]))

    ranked = [
        {**item, "lexical_score": score}
        for score, _, item in scored
    ]

    if limit is not None:
        return ranked[:limit]

    return ranked


def get_evidence_reference(
    evidence_id: str,
    *,
    tenant_id: str | None = None,
    session_id: str | None = None,
) -> dict | None:
    """Resolve one deterministic citation by evidence ID."""

    matches = retrieve_evidence(
        tenant_id=tenant_id,
        session_id=session_id,
        evidence_id=evidence_id,
        limit=1,
    )

    return matches[0] if matches else None


__all__ = [
    "STATE_SNAPSHOT_RECORD_TYPE",
    "get_latest_memory_snapshot",
    "retrieve_evidence",
    "search_evidence",
    "get_evidence_reference",
]
