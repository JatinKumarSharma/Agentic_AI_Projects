"""Deterministic provenance helpers for evidence-grounded analysis.

These helpers are deliberately model-free. Response identity, source quotes,
and evidence IDs are derived from state that the interview engine has already
accepted, so an LLM cannot invent a citation.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


PROVENANCE_FIELDS = (
    "evidence_id",
    "response_id",
    "turn",
    "captured_at",
    "participant_id",
    "source_quote",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_response_id() -> str:
    return f"resp_{uuid.uuid4().hex}"


def stable_id(prefix: str, *parts: Any) -> str:
    """Return a reproducible ID derived from already-stored values."""

    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return f"{prefix}_{digest[:16]}"


def source_quote(value: Any, response: Optional[str]) -> Optional[str]:
    """Return a verbatim excerpt of the accepted consumer response.

    If the normalized evidence value cannot be located in the response, the
    full accepted response is used. That is less precise, but it is still a
    verbatim source and never a model-generated quotation.
    """

    if not response or not str(response).strip():
        return None

    original = str(response)
    candidate = str(value or "").strip()

    if candidate:
        index = original.lower().find(candidate.lower())

        if index >= 0:
            return original[index:index + len(candidate)]

    return original.strip()


def participant_id_for_session(session: dict) -> str:
    """Return a stable participant reference for a research session."""

    session_id = str(session.get("session_id", "unknown-session"))
    explicit = session.get("participant_id")

    if explicit and str(explicit).strip():
        return str(explicit).strip()

    return f"participant-{session_id}"


def attach_response_provenance(
    decomposition: dict,
    state: dict,
) -> dict:
    """Attach response-level provenance to decomposed evidence.

    Provenance is only added when the graph has an accepted response ID. This
    keeps older callers and model-boundary tests unchanged while ensuring the
    live interview path produces citation-ready evidence.
    """

    if not isinstance(decomposition, dict):
        return decomposition

    response_id = state.get("latest_response_id")

    if not response_id:
        return decomposition

    response = state.get("latest_response")
    turn = state.get("latest_response_turn")
    captured_at = state.get("latest_response_captured_at")
    participant_id = state.get("latest_participant_id")

    if participant_id is None:
        research_session = state.get("research_session") or {}
        participant_id = participant_id_for_session(research_session)

    evidence = decomposition.get("evidence")

    if not isinstance(evidence, list):
        return decomposition

    grounded_evidence = []

    for item in evidence:
        if not isinstance(item, dict):
            grounded_evidence.append(item)
            continue

        quote = source_quote(item.get("value"), response)
        category = item.get("category", "")
        parameter = item.get("parameter", "")
        value = item.get("value", "")

        grounded_evidence.append({
            **item,
            "evidence_id": stable_id(
                "ev",
                response_id,
                category,
                parameter,
                value,
            ),
            "response_id": response_id,
            "turn": turn,
            "captured_at": captured_at,
            "participant_id": participant_id,
            "source_quote": quote,
        })

    return {
        **decomposition,
        "evidence": grounded_evidence,
    }
