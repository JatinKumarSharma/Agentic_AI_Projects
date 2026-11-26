from typing import Any, Dict

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4

from agent.state import (
    InterviewState,
    ResearchContext,
    ResearchSession,
)
from agent.provenance import participant_id_for_session


INTERVIEW_STATUS_CREATED = "created"
INTERVIEW_STATUS_STARTING = "starting"
INTERVIEW_STATUS_IN_PROGRESS = "in_progress"
INTERVIEW_STATUS_COMPLETED = "completed"
INTERVIEW_STATUS_FAILED = "failed"

VALID_INTERVIEW_STATUSES = {
    INTERVIEW_STATUS_CREATED,
    INTERVIEW_STATUS_STARTING,
    INTERVIEW_STATUS_IN_PROGRESS,
    INTERVIEW_STATUS_COMPLETED,
    INTERVIEW_STATUS_FAILED,
}

# Older databases used campaign status to imply a closed interview.
LEGACY_CLOSED_SESSION_STATUSES = {"completed", "cancelled", "expired"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def normalize_interview_status(session: dict) -> str:
    """Resolve the durable interview lifecycle for old and new sessions."""

    explicit = session.get("interview_status")
    if explicit in VALID_INTERVIEW_STATUSES:
        return explicit

    if session.get("interview_completed_at"):
        return INTERVIEW_STATUS_COMPLETED

    if session.get("interview_failed_at"):
        return INTERVIEW_STATUS_FAILED

    if session.get("interview_started_at"):
        return INTERVIEW_STATUS_IN_PROGRESS

    if session.get("status") in LEGACY_CLOSED_SESSION_STATUSES:
        return INTERVIEW_STATUS_COMPLETED

    return INTERVIEW_STATUS_CREATED


def can_start_interview(session: dict) -> bool:
    """Return True only for a session that has never been interview-started."""

    return (
        normalize_interview_status(session) == INTERVIEW_STATUS_CREATED
        and not session.get("interview_started_at")
        and session.get("status") not in LEGACY_CLOSED_SESSION_STATUSES
    )


def begin_interview(session: dict) -> dict:
    """Atomically move a session into its one-time interview start."""

    if not can_start_interview(session):
        raise ValueError("This interview has already started.")

    return {
        **session,
        "status": "active",
        "interview_status": INTERVIEW_STATUS_STARTING,
        "interview_started_at": _utc_now_iso(),
    }


def mark_interview_in_progress(session: dict) -> dict:
    """Record that the graph is waiting on the participant."""

    updated = {
        **session,
        "interview_status": INTERVIEW_STATUS_IN_PROGRESS,
    }

    if updated.get("status") in {"scheduled", "starting", "active", None}:
        updated["status"] = "active"

    return updated


def complete_interview(
    session: dict,
    *,
    termination_reason: str | None = None,
) -> dict:
    """Record a terminal interview state."""

    completed = {
        **session,
        "interview_status": INTERVIEW_STATUS_COMPLETED,
        "interview_completed_at": _utc_now_iso(),
        "interview_termination_reason": (
            termination_reason
            or session.get("interview_termination_reason")
        ),
    }

    if completed.get("status") not in LEGACY_CLOSED_SESSION_STATUSES:
        completed["status"] = "completed"

    return completed


def fail_interview(session: dict, *, error_type: str | None = None) -> dict:
    """Record that an interview start or turn failed before completion."""

    return {
        **session,
        "interview_status": INTERVIEW_STATUS_FAILED,
        "interview_failed_at": _utc_now_iso(),
        "interview_error_type": error_type,
    }


def normalize_date(value: date | str | None) -> date | None:
    """Return a date from live state or a persisted ISO-8601 string."""

    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(
        "Expected a date or ISO date string, "
        f"got {type(value).__name__}."
    )


# Presets keep the common cases simple. A custom value is a timezone-aware
# ISO-8601 timestamp, so a researcher in one timezone can expire a link at a
# precise local time for a participant in another timezone.
LINK_EXPIRY_PRESETS = {
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "1d": timedelta(days=1),
    "1 hour": timedelta(hours=1),
    "3 hours": timedelta(hours=3),
    "1 day": timedelta(days=1),
}


def resolve_link_expiry(
    value: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a requested link expiry into a label and a UTC timestamp.

    Accepts a preset name or a timezone-aware ISO-8601 datetime. Returns
    ``(None, None)`` when no expiry is requested. Naive datetimes are
    rejected because their meaning depends on the server's timezone.
    """

    if value is None:
        return None, None

    if not isinstance(value, str):
        raise ValueError("link_expiry must be a string.")

    raw = value.strip()

    if not raw or raw.lower() == "none":
        return None, None

    preset = LINK_EXPIRY_PRESETS.get(raw.lower())
    if preset is not None:
        current = now or datetime.now(timezone.utc)
        expires_at = current + preset
        return raw.lower(), expires_at.isoformat(timespec="milliseconds")

    iso_value = raw[:-1] + "+00:00" if raw[-1:] in ("Z", "z") else raw

    try:
        expires_at = datetime.fromisoformat(iso_value)
    except ValueError as exc:
        raise ValueError(
            "link_expiry must be 'none', a preset (1h, 3h, 1d), or a "
            "timezone-aware ISO-8601 datetime such as "
            "2026-11-22T15:00:00+05:30."
        ) from exc

    if expires_at.tzinfo is None:
        raise ValueError(
            "link_expiry datetime must include a timezone offset, for "
            "example 2026-11-22T15:00:00+05:30."
        )

    return (
        "custom",
        expires_at.astimezone(timezone.utc).isoformat(
            timespec="milliseconds"
        ),
    )


def is_link_expired(
    session: dict,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Return True only when an unstarted session's link has expired.

    A session that has already been started is never blocked: expiry applies
    to new interviews only, so a participant in progress can finish.
    """

    if session.get("interview_started_at"):
        return False

    expires_at = session.get("link_expires_at")

    if not expires_at:
        return False

    try:
        deadline = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        # Malformed stored metadata must not lock a participant out.
        return False

    if deadline.tzinfo is None:
        return False

    current = now or datetime.now(timezone.utc)

    return current >= deadline


def create_research_session(
    company_id: str,
    company_name: str,
    product_id: str,
    product_name: str,
    product_category: str,
    research_goal: str,
    start_date: date,
    end_date: Optional[date] = None,
    target_audience: Optional[str] = None,
    customer_segment: Optional[str] = None,
    participant_id: Optional[str] = None,
    interview_constraints: Optional[List[str]] = None,
    link_expiry: Optional[str] = None,
) -> ResearchSession:
    """
    Create a new research session.

    A research session represents one research campaign
    for one company/product/research goal over a defined
    time period.
    """

    if not company_id.strip():
        raise ValueError("company_id cannot be empty.")

    if not company_name.strip():
        raise ValueError("company_name cannot be empty.")

    if not product_id.strip():
        raise ValueError("product_id cannot be empty.")

    if not product_name.strip():
        raise ValueError("product_name cannot be empty.")

    if not product_category.strip():
        raise ValueError("product_category cannot be empty.")

    if not research_goal.strip():
        raise ValueError("research_goal cannot be empty.")

    start_date = normalize_date(start_date)
    if start_date is None:
        raise ValueError("start_date cannot be empty.")

    end_date = normalize_date(end_date)

    if end_date is not None and end_date < start_date:
        raise ValueError(
            "end_date cannot be earlier than start_date."
        )

    link_expiry_label, link_expires_at = resolve_link_expiry(link_expiry)

    normalized_segment = (
        customer_segment
        or target_audience
        or "unspecified"
    ).strip().lower()

    session: ResearchSession = {
        "session_id": str(uuid4()),

        "company_id": company_id,
        "company_name": company_name,

        "product_id": product_id,
        "product_name": product_name,
        "product_category": product_category,

        "research_goal": research_goal,

        "start_date": start_date,
        "end_date": end_date,

        "target_audience": target_audience,
        "customer_segment": normalized_segment,
        "participant_id": (
            participant_id.strip()
            if participant_id and participant_id.strip()
            else None
        ),
        "interview_constraints": interview_constraints,

        "link_expiry": link_expiry_label,
        "link_expires_at": link_expires_at,
        "interview_started_at": None,
        "interview_status": INTERVIEW_STATUS_CREATED,
        "interview_completed_at": None,
        "interview_failed_at": None,
        "interview_error_type": None,
        "interview_termination_reason": None,

        "status": "scheduled",
    }

    if session["participant_id"] is None:
        session["participant_id"] = participant_id_for_session(session)

    return session


def create_initial_state(
    session: ResearchSession,
) -> InterviewState:
    """
    Create a fresh InterviewState for a research session.

    Every research session starts with isolated interview
    knowledge and conversation state.
    """

    research_context: ResearchContext = {
        "company_name": session["company_name"],
        "product_name": session["product_name"],
        "product_category": session["product_category"],
        "research_goal": session["research_goal"],
        "customer_segment": session.get("customer_segment"),
        "target_audience": session.get("target_audience"),
        "interview_constraints": session.get("interview_constraints"),
    }

    return {
        "response_status": None,
        "response_recovery_action": None,
        "response_retry_count": 0,

        "research_session": session,
        "research_context": research_context,

        "meta_plan": None,

        "product_relationship": None,
        "product_awareness": None,
        "purchase_intent": None,
        "overall_sentiment": None,
        "product_perception": None,
        "feature_sentiment": {},

        "consumer_profile": {},
        "product_or_service": [],
        "features": {},
        "needs": {},
        "pain_points": {},
        "use_cases": {},
        "aspirations": {},
        "expectations": {},
        "consumer_intent": {},
        "competitor_information": {},
        "budget_constraints": {},
        "buying_constraints": {},
        "adoption_barriers": {},

        "questions_asked": [],
        "responses": [],
        "response_records": [],
        "response_outcomes": [],
        "current_question": None,
        "latest_response": None,
        "latest_response_id": None,
        "latest_response_captured_at": None,
        "latest_response_turn": None,
        "latest_response_question": None,
        "latest_participant_id": session.get("participant_id"),
        "latest_decomposition": None,

        "information_sufficiency": {},
        "confidence": {},
        "criticality": {},
        "verification_status": {},
        "research_goal_alignment": {},

        "previous_state_snapshot": None,
        "state_history": [],

        "unresolved_gaps": [],
        "proposals": [],
        "evaluated_proposals": [],
        "selected_proposal": None,

        "next_action": None,
        "reflection": None,

        "iteration_count": 0,
        "interview_complete": False,
        "termination_reason": None,
        "final_insights": None,

        # Voice interaction state
        "voice_interaction_mode": "research_answer",
    }

def activate_research_session(
    session: ResearchSession,
) -> ResearchSession:
    """
    Mark a scheduled research session as active.

    Lifecycle management only.
    Does not modify interview state or research logic.
    """

    if session["status"] != "scheduled":
        raise ValueError(
            "Only scheduled research sessions can be activated."
        )

    return {
        **session,
        "status": "active",
    }


def complete_research_session(
    session: ResearchSession,
) -> ResearchSession:
    """
    Mark a research session as completed.
    """

    if session["status"] in {
        "completed",
        "cancelled",
        "expired",
    }:
        raise ValueError(
            "Research session is already closed."
        )

    return {
        **session,
        "status": "completed",
    }


def expire_research_session(
    session: ResearchSession,
) -> ResearchSession:
    """
    Mark a research session as expired.
    """

    if session["status"] in {
        "completed",
        "cancelled",
        "expired",
    }:
        raise ValueError(
            "Research session is already closed."
        )

    return {
        **session,
        "status": "expired",
    }


def cancel_research_session(
    session: ResearchSession,
) -> ResearchSession:
    """
    Cancel a research session.
    """

    if session["status"] in {
        "completed",
        "cancelled",
        "expired",
    }:
        raise ValueError(
            "Research session is already closed."
        )

    return {
        **session,
        "status": "cancelled",
    }