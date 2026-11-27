import base64
import binascii
import os
import json
import asyncio
import csv
import io
import re

from dotenv import load_dotenv

# Load host-run configuration before importing modules that select a
# persistence backend at import time. Compose passes these values directly.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, Response

import time

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from threading import Event, Lock, Thread

import websockets

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Optional
from uuid import uuid4

from pathlib import Path
from urllib.parse import urlencode

from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field, field_validator
from langgraph.types import Command

from agent import (
    analysis_report,
    evidence_retrieval,
    limits as abuse_limits,
    read_model,
    timing,
)
from agent.jobs import service as job_service
from agent.jobs.errors import DuplicateJobError
from agent.persistence import (
    POSTGRES_BACKEND,
    configure_repository,
    load_persistence_config,
)
from agent.persistence.checkpointer import RepositoryCheckpointer
from agent.auth import (
    ResearcherContext,
    require_researcher,
    require_researcher_if_configured,
)
from agent.observability import (
    configure_logging,
    get_logger,
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from agent.participant_token import (
    PARTICIPANT_TOKEN_HEADER,
    PARTICIPANT_TOKEN_QUERY_PARAM,
    hash_participant_token,
    new_participant_token,
    participant_token_is_valid,
)
from agent.tenancy import current_tenant_id, normalize_tenant_id
from agent.graph import build_interview_graph
from agent.provenance import participant_id_for_session
from agent.session import (
    begin_interview,
    can_start_interview,
    complete_interview,
    create_research_session,
    create_initial_state,
    activate_research_session,
    fail_interview,
    is_link_expired,
    mark_interview_in_progress,
    normalize_interview_status,
)

from queue import Queue

from agent.voice.contextual_engagement import (
    generate_contextual_engagement,
)

from agent.voice.event_adapter import (
    create_voice_output_event,
)
from agent.voice.acknowledgement_policy import (
    select_acknowledgement,
    should_acknowledge,
)
from agent.voice.stt_providers import (
    STTProviderError,
    transcribe_with_fallback,
)
from agent.voice.tts_providers import (
    TTSProviderError,
    synthesize_with_fallback,
)
from agent.voice.voice_profile import get_voice_profile

from agent.voice.sarvam_stt import (
    create_sarvam_client,
    create_realtime_audio_input,
    create_realtime_end,
    STT_LANGUAGE_CODE,
    STT_MODEL,
    STT_STREAM_TYPE,
    STT_MODE,
    STT_ENDPOINTING,
    STT_ENCODING,
    STT_SAMPLE_RATE,
)

configure_logging()
logger = get_logger(__name__)

# Hosted production runs background work in the separate `worker` service.
# In-process executors remain only for offline tests.
WORKER_ENABLED = os.getenv(
    "CVI_WORKER_ENABLED", "0"
).strip().lower() in {"1", "true", "yes", "on"}

persistence_config = load_persistence_config()
repository = configure_repository(
    persistence_config,
    ensure_schema=persistence_config.backend != POSTGRES_BACKEND,
)

app = FastAPI(
    title="Customer Voice Interviewer API",
    version="0.3.0",
)


def _context_or_default(context):
    """Allow direct endpoint calls outside HTTP (for example, unit tests)."""

    if context is None:
        return ResearcherContext(
            username="researcher",
            tenant_id=current_tenant_id(),
            role="admin",
        )

    return context


def _tenant(context) -> str:
    return _context_or_default(context).tenant_id


def _actor(context) -> str:
    return _context_or_default(context).username


def _request_id(context) -> str:
    return _context_or_default(context).request_id


def _cache_session(session: dict) -> None:
    """Store a session under its tenant-qualified cache key."""

    tenant_id = normalize_tenant_id(session.get("tenant_id"))
    sessions[(tenant_id, session["session_id"])] = session


def _save_session(session: dict) -> dict:
    """Write a session to the cache and durable repository together."""

    # Cache only; the repository write below is the source of truth.
    _cache_session(session)
    read_model.upsert_session(session)
    return session


def _apply_interview_transition(session_id, transition, **kwargs):
    """Apply one lifecycle transition to the current durable session."""

    session = resolve_session(session_id)
    if session is None:
        return None

    updated = transition(session, **kwargs)
    _save_session(updated)

    read_model.record_event(
        session_id,
        "interview_lifecycle_changed",
        {"interview_status": updated.get("interview_status")},
    )

    return updated


def _session_owner(session: dict) -> str:
    return str(
        session.get("created_by")
        or session.get("owner")
        or ""
    )


def _can_access_session(context, session: dict) -> bool:
    """Researchers see their own sessions; admins see every session."""

    resolved = _context_or_default(context)
    owner = _session_owner(session)

    return (
        resolved.role == "admin"
        or not owner
        or owner == resolved.username
    )


def _visible_session_ids(context) -> set[str]:
    return {
        session["session_id"]
        for session in read_model.list_sessions(
            tenant_id=_tenant(context)
        )
        if _can_access_session(context, session)
    }


def _require_session_access(context, session_id: str) -> dict:
    session = read_model.get_session(
        session_id,
        tenant_id=_tenant(context),
    )

    if session is None or not _can_access_session(context, session):
        raise HTTPException(
            status_code=404,
            detail="Session not found.",
        )

    return session


def _participant_token_from_request(
    http_request: Request | WebSocket | None = None,
    explicit: str | None = None,
) -> str | None:
    """Read the participant capability from a header or query parameter."""

    if explicit:
        return explicit

    if http_request is None:
        return None

    header = http_request.headers.get(PARTICIPANT_TOKEN_HEADER)
    query = http_request.query_params.get(PARTICIPANT_TOKEN_QUERY_PARAM)

    return header or query


def _rate_limit_participant(
    session_id: str,
    http_request: Request | WebSocket | None,
) -> None:
    """Apply per-session and per-IP budgets to participant traffic."""

    if http_request is None:
        return

    client_host = getattr(getattr(http_request, "client", None), "host", None)

    if not rate_limiter.allow("session", session_id):
        raise HTTPException(
            status_code=429,
            detail="Too many requests for this interview. Please slow down.",
        )

    if not rate_limiter.allow(
        "ip",
        abuse_limits.client_ip_key(client_host),
        rate_limiter.ip_limit,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many requests from this network. Please slow down.",
        )


def _require_participant_session(
    session_id: str,
    *,
    http_request: Request | WebSocket | None = None,
    participant_token: Optional[str] = None,
    not_found_detail: str = "Session not found.",
) -> dict:
    """Resolve a session and enforce its participant invitation capability."""

    _rate_limit_participant(session_id, http_request)

    session = resolve_session(session_id)

    if session is None:
        raise HTTPException(
            status_code=404,
            detail=not_found_detail,
        )

    token = _participant_token_from_request(
        http_request,
        participant_token,
    )

    if not participant_token_is_valid(session, token):
        raise HTTPException(
            status_code=401,
            detail=(
                "A valid participant link is required. "
                "Please open the interview from your invitation link."
            ),
        )

    # Expiry is part of the participant authorization contract, not a special
    # check on only invitation/start. Started interviews remain exempt.
    reject_if_link_expired(session_id, session)

    return session


# Public demo links expose a slug, never a participant capability. The slug
# selects a pool of pre-created, unstarted sessions; issuing a demo invite
# rotates one pool member's capability and gives a short deadline.
_DEMO_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


def _valid_demo_slug(slug: str | None) -> bool:
    return bool(slug and _DEMO_SLUG_RE.fullmatch(slug))


def _demo_ttl_seconds() -> int:
    try:
        minutes = int(os.getenv("CVI_DEMO_LINK_TTL_MINUTES", "15"))
    except ValueError:
        minutes = 15

    return min(max(minutes, 1), 60) * 60


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _demo_claim_is_active(session: dict, ttl_seconds: int) -> bool:
    claimed_at = _parse_iso_timestamp(session.get("demo_claimed_at"))

    if claimed_at is None:
        return False

    age = (datetime.now(timezone.utc) - claimed_at).total_seconds()
    return 0 <= age < ttl_seconds


def _find_demo_session(slug: str) -> dict | None:
    """Find an unclaimed session in the deployment tenant's demo pool."""

    candidates = [
        session
        for session in read_model.list_sessions(
            tenant_id=current_tenant_id()
        )
        if session.get("demo_slug") == slug
    ]

    ttl_seconds = _demo_ttl_seconds()
    eligible = [
        session
        for session in candidates
        if can_start_interview(session)
        and not session.get("interview_started_at")
        and not _demo_claim_is_active(session, ttl_seconds)
    ]
    eligible.sort(
        key=lambda session: str(
            session.get("created_at") or session.get("session_id") or ""
        )
    )

    return eligible[0] if eligible else None


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a request ID to logs and propagate it to the caller."""

    request_id = request.headers.get("x-request-id") or new_request_id()
    token = set_request_id(request_id)
    started_at = time.perf_counter()

    try:
        content_length = request.headers.get("content-length")

        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = None

            if (
                declared_length is not None
                and declared_length > abuse_limits.MAX_HTTP_BODY_BYTES
            ):
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "Request body is too large."},
                )
                response.headers["X-Request-ID"] = request_id
                logger.warning(
                    "request_body_rejected",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "content_length": declared_length,
                    },
                )
                return response

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        logger.info(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(
                    (time.perf_counter() - started_at) * 1000,
                    2,
                ),
            },
        )
        return response

    except Exception:
        logger.exception(
            "request_failed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round(
                    (time.perf_counter() - started_at) * 1000,
                    2,
                ),
            },
        )
        raise

    finally:
        reset_request_id(token)


class SessionCache(dict):
    """Process-local cache normalized to ``(tenant_id, session_id)`` keys.

    Callers may still use a bare session ID for legacy test fixtures; writes
    from the API always use the tenant-qualified key so two researchers cannot
    share a cached session across tenants.
    """

    def _key(self, key):
        if isinstance(key, tuple) and len(key) == 2:
            tenant_id, session_id = key
            return (normalize_tenant_id(tenant_id), session_id)

        session_id = key
        for candidate in super().keys():
            if candidate[1] == session_id:
                return candidate

        return (current_tenant_id(), session_id)

    def __getitem__(self, key):
        return super().__getitem__(self._key(key))

    def __setitem__(self, key, value):
        super().__setitem__(self._key(key), value)

    def __delitem__(self, key):
        super().__delitem__(self._key(key))

    def __contains__(self, key):
        return super().__contains__(self._key(key))

    def get(self, key, default=None):
        return super().get(self._key(key), default)

    def pop(self, key, *args):
        return super().pop(self._key(key), *args)


# The active interview graph uses the same durable repository as the read model.
checkpointer = RepositoryCheckpointer(repository=repository)
graph = build_interview_graph(checkpointer=checkpointer)

sessions = SessionCache()
pending_interrupts = {}

# Background response processing
response_executor = ThreadPoolExecutor(max_workers=4)

# Independent contextual engagement processing
engagement_executor = ThreadPoolExecutor(max_workers=4)

response_jobs = {}
session_locks = {}

# Session-specific outbound voice messages. The queue is the legacy handoff
# from background workers; voice_output_events is the readable, ack-tracked
# outbox for offline mode.
voice_output_queues: dict[str, Queue] = {}
voice_output_events: dict[str, list[dict]] = {}

# Number of contextual engagement tasks still running per session.
pending_engagement_tasks: dict[str, int] = {}

# Protect queue operations, outbox reads, and pending-task counts together.
engagement_state_lock = Lock()

# Per-process request budgets for participant traffic.
rate_limiter = abuse_limits.RateLimiter()
active_voice_streams: dict[str, int] = {}

# Worker processes use a durable lease so more than one replica can run. A
# configured CVI_WORKER_ID is only a label; the suffix is unique per process.
_SESSION_LEASE_OWNER = job_service.worker_identity(
    os.getenv("CVI_WORKER_ID", "api")
)


def _session_lease_ttl_seconds() -> float:
    try:
        return max(30.0, float(
            os.getenv("CVI_SESSION_LEASE_TTL_SECONDS", "300")
        ))
    except ValueError:
        return 300.0


@contextmanager
def session_processing_lock(session_id: str):
    """Serialize work for one session without blocking other sessions."""

    if not WORKER_ENABLED:
        lock = session_locks.setdefault(session_id, Lock())
        with lock:
            yield True
        return

    owner_id = _SESSION_LEASE_OWNER
    ttl_seconds = _session_lease_ttl_seconds()
    tenant_id = _tenant_for_runtime(session_id)

    acquired = repository.acquire_session_lease(
        session_id=session_id,
        owner_id=owner_id,
        ttl_seconds=ttl_seconds,
        tenant_id=tenant_id,
    )

    if not acquired:
        logger.warning(
            "session_lease_contended",
            extra={"session_id": session_id},
        )
        yield False
        return

    stop_renewal = Event()

    def renew_lease():
        interval = max(1.0, ttl_seconds / 3)

        while not stop_renewal.wait(interval):
            try:
                repository.renew_session_lease(
                    session_id=session_id,
                    owner_id=owner_id,
                    ttl_seconds=ttl_seconds,
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.exception(
                    "session_lease_renewal_failed",
                    extra={"session_id": session_id},
                )
                return

    renewer = Thread(
        target=renew_lease,
        name=f"lease-renewer-{session_id}",
        daemon=True,
    )
    renewer.start()

    try:
        yield True
    finally:
        stop_renewal.set()
        renewer.join(timeout=1)
        try:
            repository.release_session_lease(
                session_id=session_id,
                owner_id=owner_id,
                tenant_id=tenant_id,
            )
        except Exception:
            logger.exception(
                "session_lease_release_failed",
                extra={"session_id": session_id},
            )


class CreateSessionRequest(BaseModel):
    company_id: str = Field(min_length=1)
    company_name: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    product_category: str = Field(min_length=1)
    research_goal: str = Field(min_length=1)
    start_date: date = Field(default_factory=date.today)
    end_date: Optional[date] = None
    target_audience: Optional[str] = None
    customer_segment: Optional[str] = None
    participant_id: Optional[str] = None
    interview_constraints: list[str] = Field(default_factory=list)
    # "none" (default), a preset (1h, 3h, 1d), or a timezone-aware
    # ISO-8601 timestamp such as 2026-11-22T15:00:00+05:30.
    link_expiry: Optional[str] = None
    # Optional public demo pool name. Sessions sharing a slug form a pool;
    # the public broker issues a short-lived capability for one unclaimed
    # session and never returns a permanent invite URL.
    demo_slug: Optional[str] = None


class VoiceResponseRequest(BaseModel):
    transcript: Optional[str] = Field(
        default=None,
        max_length=abuse_limits.MAX_TRANSCRIPT_CHARS,
    )
    speech_duration_ms: int = Field(
        default=0,
        ge=0,
        le=abuse_limits.MAX_SPEECH_DURATION_MS,
    )
    audio_issue: bool = False


class TimingRecordRequest(BaseModel):
    turn: int = Field(default=0, ge=0)
    span_name: str = Field(
        min_length=1,
        max_length=abuse_limits.MAX_SPAN_NAME_CHARS,
    )
    started_ms: float = Field(ge=0)
    ended_ms: float = Field(ge=0)
    parent_span_id: Optional[str] = Field(
        default=None,
        max_length=abuse_limits.MAX_EVENT_ID_CHARS,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return abuse_limits.validate_bounded_mapping(
            value,
            field_name="metadata",
            max_keys=abuse_limits.MAX_METADATA_KEYS,
            max_key_chars=abuse_limits.MAX_METADATA_KEY_CHARS,
            max_value_chars=abuse_limits.MAX_METADATA_VALUE_CHARS,
        )


class VoiceEventAckRequest(BaseModel):
    event_ids: list[str] = Field(default_factory=list, max_length=1000)

    @field_validator("event_ids")
    @classmethod
    def validate_event_ids(cls, value: list[str]) -> list[str]:
        for event_id in value:
            if not event_id or len(event_id) > abuse_limits.MAX_EVENT_ID_CHARS:
                raise ValueError(
                    "event_ids entries must be non-empty and at most "
                    f"{abuse_limits.MAX_EVENT_ID_CHARS} characters."
                )
        return value


class VoiceSynthesisRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=abuse_limits.MAX_TTS_TEXT_CHARS,
    )
    delivery: dict[str, Any] = Field(default_factory=dict)

    @field_validator("delivery")
    @classmethod
    def validate_delivery(cls, value: dict[str, Any]) -> dict[str, Any]:
        return abuse_limits.validate_bounded_mapping(
            value,
            field_name="delivery",
            max_keys=abuse_limits.MAX_DELIVERY_KEYS,
            max_key_chars=abuse_limits.MAX_METADATA_KEY_CHARS,
            max_value_chars=abuse_limits.MAX_DELIVERY_VALUE_CHARS,
        )


class VoiceTranscriptionRequest(BaseModel):
    audio_base64: str = Field(
        min_length=1,
        max_length=abuse_limits.MAX_AUDIO_BASE64_CHARS,
    )
    filename: str = Field(
        default="response.wav",
        min_length=1,
        max_length=abuse_limits.MAX_FILENAME_CHARS,
    )
    turn: int = Field(default=0, ge=0)
    language: Optional[str] = Field(
        default=None,
        max_length=abuse_limits.MAX_LANGUAGE_CHARS,
    )
    prompt: Optional[str] = Field(
        default=None,
        max_length=abuse_limits.MAX_STT_PROMPT_CHARS,
    )

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if not abuse_limits.is_safe_filename(value):
            raise ValueError("filename contains unsupported characters.")
        return value


def thread_config(session_id: str):
    return {
        "configurable": {
            "thread_id": session_id,
            "tenant_id": read_model.tenant_for_session(session_id),
        }
    }


def resolve_session(
    session_id: str,
    tenant_id: str | None = None,
) -> dict | None:
    """Find a session in the local cache or hydrate it from durable storage.

    The cache is an optimization only. Sessions written by another API replica
    are found through the configured repository, and cache keys are tenant-
    qualified so identical session IDs cannot cross tenant boundaries.
    """

    resolved_tenant = tenant_id or read_model.tenant_for_session(session_id)
    resolved_tenant = normalize_tenant_id(resolved_tenant)
    key = (resolved_tenant, session_id)

    session = sessions.get(key)

    if session is None:
        session = read_model.get_session(
            session_id,
            tenant_id=resolved_tenant,
        )
        if session is not None:
            sessions[key] = session

    return session


def _tenant_for_runtime(session_id: str) -> str:
    return read_model.tenant_for_session(session_id)


def get_pending_interrupt(session_id: str) -> dict | None:
    """Read the current interrupt from the active runtime boundary."""

    if WORKER_ENABLED:
        return repository.get_pending_action(
            session_id=session_id,
            tenant_id=_tenant_for_runtime(session_id),
        )

    return pending_interrupts.get(session_id)


def save_pending_interrupt(session_id: str, payload: dict) -> None:
    """Store the current interrupt in the active runtime boundary."""

    pending_interrupts[session_id] = payload

    if WORKER_ENABLED:
        repository.save_pending_action(
            session_id=session_id,
            payload=payload,
            tenant_id=_tenant_for_runtime(session_id),
        )


def clear_pending_interrupt(session_id: str) -> None:
    """Remove the current interrupt from the active runtime boundary."""

    pending_interrupts.pop(session_id, None)

    if WORKER_ENABLED:
        repository.clear_pending_action(
            session_id=session_id,
            tenant_id=_tenant_for_runtime(session_id),
        )


def has_processing_response_job(session_id: str) -> bool:
    """Return whether a response job is already queued or running."""

    job = response_jobs.get(session_id)

    if job and job.get("status") == "processing":
        return True

    if WORKER_ENABLED:
        return repository.has_active_job(
            session_id=session_id,
            job_type=job_service.PROCESS_RESPONSE,
            tenant_id=_tenant_for_runtime(session_id),
        )

    return False


def current_response_job(session_id: str) -> dict | None:
    """Return the latest response job from the active runtime boundary."""

    if not WORKER_ENABLED:
        return response_jobs.get(session_id)

    job = repository.latest_job(
        session_id=session_id,
        job_type=job_service.PROCESS_RESPONSE,
        tenant_id=_tenant_for_runtime(session_id),
    )

    if job is None:
        return None

    status = job.get("status")
    result = job.get("result")

    if status == "completed" and isinstance(result, dict):
        nested = result.get("result")

        if result.get("status") == "completed" and isinstance(nested, dict):
            return {"status": "completed", "result": nested}

        if result.get("status") == "failed":
            return {
                "status": "failed",
                "error": result.get("error"),
            }

        return {"status": "completed", "result": result}

    if status == "failed":
        return {"status": "failed", "error": job.get("error")}

    if status in {"queued", "processing"}:
        return {"status": "processing"}

    return None


def _latest_raw_response_job(session_id: str) -> dict | None:
    """Return the raw response job, including its original payload."""

    if WORKER_ENABLED:
        return repository.latest_job(
            session_id=session_id,
            job_type=job_service.PROCESS_RESPONSE,
            tenant_id=_tenant_for_runtime(session_id),
        )

    return response_jobs.get(session_id)


def _is_failed_response_job(job: dict | None) -> bool:
    """Recognize both native and worker-wrapped response failures."""

    if not job:
        return False

    if job.get("status") == "failed":
        return True

    result = job.get("result")
    return isinstance(result, dict) and result.get("status") == "failed"


def _graph_waiting_for_response(session_id: str, payload: dict) -> bool:
    """Return whether the graph is still parked on this exact response turn."""

    try:
        snapshot = graph.get_state(thread_config(session_id))
    except Exception:
        return False

    for interrupt in getattr(snapshot, "interrupts", ()) or ():
        value = getattr(interrupt, "value", None)

        if not isinstance(value, dict):
            continue

        if value.get("type") != "await_consumer_response":
            continue

        try:
            same_turn = int(value.get("turn") or 0) == int(
                payload.get("turn") or 0
            )
        except (TypeError, ValueError):
            same_turn = False

        if same_turn and value.get("question") == payload.get("question"):
            return True

    return False


EXPIRED_LINK_MESSAGE = (
    "This interview link has expired. Please ask for a fresh interview link."
)


def _record_expired_link_event(session_id: str, session: dict) -> None:
    read_model.record_event(
        session_id,
        "participant_link_expired",
        {"link_expires_at": session.get("link_expires_at")},
    )


def reject_if_link_expired(session_id: str, session: dict) -> None:
    """Block new interviews after a link's deadline.

    Sessions already started are exempt, so an in-progress interview can be
    finished even if the deadline passes.
    """

    if not is_link_expired(session):
        return

    _record_expired_link_event(session_id, session)
    raise HTTPException(status_code=410, detail=EXPIRED_LINK_MESSAGE)


async def reject_websocket_if_link_expired(
    websocket: WebSocket,
    session_id: str,
    session: dict,
) -> bool:
    """Apply the same expiry policy to a voice websocket."""

    if not is_link_expired(session):
        return False

    _record_expired_link_event(session_id, session)
    await websocket.send_json({
        "type": "error",
        "message": EXPIRED_LINK_MESSAGE,
    })
    await websocket.close(code=4410)
    return True


async def reject_websocket_if_rate_limited(
    websocket: WebSocket,
    session_id: str,
) -> bool:
    """Apply websocket-specific budgets before opening upstream work."""

    client_host = getattr(getattr(websocket, "client", None), "host", None)

    allowed_session = rate_limiter.allow(
        "ws_session",
        session_id,
        rate_limiter.websocket_limit,
    )
    allowed_ip = rate_limiter.allow(
        "ws_ip",
        abuse_limits.client_ip_key(client_host),
        rate_limiter.ip_limit,
    )

    if allowed_session and allowed_ip:
        return False

    await websocket.send_json({
        "type": "error",
        "message": "Too many voice connections. Please slow down.",
    })
    await websocket.close(code=4429)
    return True


async def reject_websocket_if_stream_crowded(
    websocket: WebSocket,
    session_id: str,
) -> bool:
    """Limit simultaneous microphone streams for one session."""

    with engagement_state_lock:
        active = active_voice_streams.get(session_id, 0)

        if active >= abuse_limits.WS_MAX_CONCURRENT_PER_SESSION:
            crowded = True
        else:
            active_voice_streams[session_id] = active + 1
            crowded = False

    if not crowded:
        return False

    await websocket.send_json({
        "type": "error",
        "message": "This interview already has an active voice connection.",
    })
    await websocket.close(code=4429)
    return True


def release_voice_stream(session_id: str) -> None:
    with engagement_state_lock:
        active = active_voice_streams.get(session_id, 0)

        if active <= 1:
            active_voice_streams.pop(session_id, None)
        elif active:
            active_voice_streams[session_id] = active - 1


async def reject_voice_stream_limit(
    websocket: WebSocket,
    violation: str,
) -> None:
    await websocket.send_json({
        "type": "error",
        "message": "Voice stream limit exceeded.",
        "limit": violation,
    })
    await websocket.close(code=4409)


def _record_timing(
    session_id: str,
    turn: int,
    span_name: str,
    started_ms: float,
    **kwargs: Any,
) -> dict:
    """Persist a completed backend timing span."""

    return timing.record(
        session_id,
        turn,
        span_name,
        started_ms,
        ended_ms=timing.now_ms(),
        **kwargs,
    )


def extract_next_action(session_id: str, result: dict):
    interrupts = result.get("__interrupt__", [])

    if interrupts:
        payload = interrupts[0].value
        save_pending_interrupt(session_id, payload)
        _apply_interview_transition(
            session_id,
            mark_interview_in_progress,
        )

        return {
            "status": "waiting",
            "session_id": session_id,
            "action": payload,
        }

    clear_pending_interrupt(session_id)

    # The decision policy may end the interview directly without
    # passing through the executive controller. In that case
    # interview_complete is still False, but next_action and the
    # termination reason identify a legitimate completion.
    if (
        result.get("interview_complete")
        or result.get("next_action") == "stop"
        or result.get("termination_reason")
    ):
        termination_reason = result.get("termination_reason")
        _apply_interview_transition(
            session_id,
            complete_interview,
            termination_reason=termination_reason,
        )

        return {
            "status": "completed",
            "session_id": session_id,
            "termination_reason": termination_reason,
        }

    # A graph return without an interrupt is also terminal. Treating it as
    # in-progress would allow a later /start call to reinitialize the thread.
    _apply_interview_transition(
        session_id,
        complete_interview,
        termination_reason="graph_finished_without_interrupt",
    )

    return {
        "status": "finished",
        "session_id": session_id,
        "message": "The graph returned without another interrupt.",
    }


def persist_graph_state(
    session_id: str,
    state: dict,
    source: str,
):
    """Project LangGraph state into the durable read model."""

    session = resolve_session(session_id) or {
        "session_id": session_id,
        "status": "unknown",
    }

    activity = get_session_activity(
        session_id,
        session,
        state,
    )

    # Reports are rebuilt on every persisted graph write. Incomplete
    # interviews keep an "unfinished" report so their accepted evidence
    # remains analyzable; completion replaces it with the final report.
    try:
        report = analysis_report.build_session_report(
            session,
            state,
        )
        report = {
            **report,
            "tenant_id": read_model.tenant_for_session(session_id),
            "created_by": "system",
            "updated_by": "system",
        }

        read_model.record_audit(
            action="report.generated",
            actor="system",
            resource_type="report",
            resource_id=report["report_id"],
            tenant_id=report["tenant_id"],
            metadata={"session_id": session_id},
        )

        if state.get("interview_complete"):
            state = {
                **state,
                "final_insights": report["summary"],
            }

        read_model.store_report(report)
        report_dimensions = report["dimensions"]
        read_model.record_event(
            session_id,
            "analysis_report_stored",
            {
                "report_id": report["report_id"],
                "status_label": report_dimensions["status_label"],
                "response_pattern_label": (
                    report_dimensions["response_pattern_label"]
                ),
            },
        )

    except ValueError as exc:
        read_model.record_event(
            session_id,
            "analysis_report_rejected",
            {"error": str(exc)},
        )

    snapshot = read_model.record_snapshot(
        session_id,
        state,
        source,
        activity,
    )

    read_model.record_event(
        session_id,
        "graph_state_saved",
        {
            "source": source,
            "snapshot_id": snapshot["snapshot_id"],
            "iteration_count": state.get("iteration_count", 0),
        },
    )

    return snapshot


def process_contextual_engagement(
    session_id: str,
    interview_id: str,
    decomposition: dict,
    turn: int = 0,
    queued_at_ms: float | None = None,
):
    """
    Generate contextual engagement independently of the
    research graph and place it in the session's output queue.
    """

    output_event = None
    generation_started_ms = timing.now_ms()
    generation_failed = False

    try:
        spoken_text = generate_contextual_engagement(
            decomposition
        )

        if spoken_text:
            output_event = create_voice_output_event(
                session_id=session_id,
                interview_id=interview_id,
                event_type="contextual_engagement",
                text=spoken_text,
            )

            read_model.record_event(
                session_id,
                "contextual_engagement",
                {
                    "interview_id": interview_id,
                    "text": spoken_text,
                },
            )

    except Exception:
        logger.exception(
            "contextual_engagement_failed",
            extra={"session_id": session_id},
        )
        generation_failed = True

    finally:
        _record_timing(
            session_id,
            turn,
            "contextual_engagement_generation",
            generation_started_ms,
            source="voice_chain",
            parent_span_id="response_processing",
            metadata={
                "status": "failed" if generation_failed else "completed",
                "emitted": bool(output_event),
            },
        )

        if queued_at_ms is not None:
            _record_timing(
                session_id,
                turn,
                "contextual_engagement_emitted",
                generation_started_ms,
                source="voice_chain",
                parent_span_id="response_processing",
                metadata={
                    "status": "failed" if generation_failed else "completed",
                    "emitted": bool(output_event),
                    "queued_at_ms": queued_at_ms,
                },
            )

        # Publish the event and mark the task complete atomically.
        if output_event is not None:
            if WORKER_ENABLED:
                repository.enqueue_voice_event(
                    event_id=f"voice_{uuid4().hex}",
                    session_id=session_id,
                    event=output_event,
                    tenant_id=_tenant_for_runtime(session_id),
                )
            else:
                _publish_offline_voice_event(session_id, output_event)

        with engagement_state_lock:
            pending = pending_engagement_tasks.get(
                session_id, 0
            )

            if pending > 1:
                pending_engagement_tasks[session_id] = pending - 1
            else:
                pending_engagement_tasks.pop(session_id, None)


def create_speech_end_acknowledgement_message(
    session_id: str,
) -> dict:
    """Build the immediate voice-plane message for end of speech.

    This is deliberately synchronous and model-free. The contextual
    engagement message is generated separately after decomposition.
    """

    event = create_voice_output_event(
        session_id=session_id,
        interview_id=session_id,
        event_type="immediate_acknowledgement",
        text=select_acknowledgement(),
    )

    return {
        "type": "voice_output",
        "event": event,
    }


def local_stream_url() -> str:
    """Resolve the local speech service's streaming endpoint."""

    explicit = os.getenv("CVI_SPEECH_STREAM_URL", "").strip()

    if explicit:
        return explicit

    base = os.getenv(
        "CVI_SPEECH_URL",
        "http://localhost:9000",
    ).strip().rstrip("/")

    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/stream"

    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/stream"

    return "ws://" + base + "/stream"


@asynccontextmanager
async def connect_local_speech_stream(url: str):
    """Single mockable boundary for the local speech WebSocket."""

    async with websockets.connect(url, max_size=None) as socket:
        yield socket


def create_vad_speech_end_messages(
    session_id: str,
    utterance_idx,
    *,
    turn: int = 0,
    started_ms: float | None = None,
    speech_ms: float = 0,
    pause_ms: float = 700,
) -> list[dict]:
    """Return the browser messages for an end-of-speech event."""

    started = timing.now_ms() if started_ms is None else started_ms

    messages = [
        {
            "type": "vad_speech_end",
            "utterance_idx": utterance_idx,
        },
    ]

    if should_acknowledge(int(speech_ms), int(pause_ms)):
        messages.append(
            create_speech_end_acknowledgement_message(session_id)
        )
        _record_timing(
            session_id,
            turn,
            "speech_end_acknowledgement_emitted",
            started,
            source="voice_chain",
            metadata={
                "utterance_idx": utterance_idx,
                "speech_ms": speech_ms,
            },
        )

    return messages


def process_response_in_background(
    session_id: str,
    response_event: dict,
):
    """Resume LangGraph and handle custom voice events."""

    turn = int(response_event.get("turn") or 0)
    submitted_ms = response_event.get("submitted_monotonic_ms")

    if submitted_ms is None:
        submitted_ms = timing.now_ms()

    try:
        config = thread_config(session_id)

        with session_processing_lock(session_id) as acquired:

            if not acquired:
                skipped = {
                    "status": "skipped",
                    "error": "Another worker owns this session.",
                }
                response_jobs[session_id] = skipped
                return skipped

            final_state = None

            if response_event.get("retry_mode") == "continue":
                # The graph already accepted this response before failing.
                # `None` resumes from the latest checkpoint without replaying
                # the interrupt and appending a duplicate response record.
                stream_input = None
            else:
                stream_input = Command(resume=response_event)

            stream = graph.stream(
                stream_input,
                config=config,
                stream_mode=["custom", "values"],
            )

            for mode, data in stream:

                # -------------------------------------------------
                # CONTEXTUAL ENGAGEMENT EVENT
                # -------------------------------------------------

                if mode == "custom":

                    if (
                        isinstance(data, dict)
                        and data.get("event")
                        == "contextual_engagement_input"
                    ):

                        event_session_id = data.get(
                            "session_id"
                        )

                        event_interview_id = data.get(
                            "interview_id"
                        )

                        decomposition = data.get(
                            "decomposition"
                        )

                        # Enforce session isolation.
                        if (
                            event_session_id == session_id
                            and isinstance(decomposition, dict)
                        ):

                            # Register the task before submitting it.
                            with engagement_state_lock:
                                pending_engagement_tasks[session_id] = (
                                    pending_engagement_tasks.get(session_id, 0) + 1
                                )

                            try:
                                if WORKER_ENABLED:
                                    job_service_job_id = job_service.new_job_id(
                                        job_service.CONTEXTUAL_ENGAGEMENT
                                    )
                                    repository.enqueue_job(
                                        job_id=job_service_job_id,
                                        job_type=job_service.CONTEXTUAL_ENGAGEMENT,
                                        session_id=session_id,
                                        payload={
                                            "session_id": session_id,
                                            "interview_id": (
                                                event_interview_id or session_id
                                            ),
                                            "decomposition": decomposition,
                                            "turn": turn,
                                            "queued_at_ms": submitted_ms,
                                        },
                                        tenant_id=_tenant_for_runtime(session_id),
                                    )
                                else:
                                    engagement_executor.submit(
                                        process_contextual_engagement,
                                        session_id,
                                        event_interview_id or session_id,
                                        decomposition,
                                        turn,
                                        submitted_ms,
                                    )

                                _record_timing(
                                    session_id,
                                    turn,
                                    "contextual_engagement_queued",
                                    submitted_ms,
                                    source="voice_chain",
                                    parent_span_id="response_processing",
                                    metadata={
                                        "phase": (
                                            "durable_enqueued"
                                            if WORKER_ENABLED
                                            else "task_submitted"
                                        )
                                    },
                                )

                            except Exception:
                                # A scheduling failure must not fail research processing.
                                logger.exception(
                                    "engagement_scheduling_failed",
                                    extra={"session_id": session_id},
                                )

                                with engagement_state_lock:
                                    pending = pending_engagement_tasks.get(
                                        session_id, 0
                                    )

                                    if pending > 1:
                                        pending_engagement_tasks[session_id] = pending - 1
                                    else:
                                        pending_engagement_tasks.pop(session_id, None)

                # -------------------------------------------------
                # TRACK THE LATEST GRAPH STATE
                # -------------------------------------------------

                elif mode == "values":

                    final_state = data

            # -----------------------------------------------------
            # RECOVER FINAL STATE IF NO VALUES WERE STREAMED
            # -----------------------------------------------------

            if final_state is None:

                snapshot = graph.get_state(config)

                final_state = snapshot.values

            persist_graph_state(
                session_id,
                final_state,
                "response_processed",
            )

            # -----------------------------------------------------
            # PRESERVE EXISTING INTERVIEW FLOW
            # -----------------------------------------------------

            next_action = extract_next_action(
                session_id,
                final_state,
            )

        response_jobs[session_id] = {
            "status": "completed",
            "result": next_action,
        }

        _record_timing(
            session_id,
            turn,
            "response_processing",
            submitted_ms,
            source="runtime",
            metadata={"status": "completed"},
        )

        if next_action.get("action", {}).get("type") == (
            "deliver_question"
        ):
            _record_timing(
                session_id,
                turn + 1,
                "next_question_delivered",
                submitted_ms,
                source="runtime",
                metadata={"from_turn": turn},
            )

        return response_jobs[session_id]

    except Exception as exc:
        logger.exception(
            "response_processing_failed",
            extra={"session_id": session_id, "turn": turn},
        )

        read_model.record_event(
            session_id,
            "response_failed",
            {"error_type": type(exc).__name__},
        )

        error_detail = str(exc).strip()

        response_jobs[session_id] = {
            "status": "failed",
            "error": (
                f"Response processing failed: "
                f"{type(exc).__name__}"
                + (f" — {error_detail[:240]}" if error_detail else "")
            ),
            "payload": response_event,
        }

        _record_timing(
            session_id,
            turn,
            "response_processing",
            submitted_ms,
            source="runtime",
            metadata={
                "status": "failed",
                "error_type": type(exc).__name__,
            },
        )

        return response_jobs[session_id]

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "customer-voice-interviewer",
    }


@app.get("/health/live")
def liveness_check():
    return {"status": "ok"}


def _readiness_checks() -> tuple[dict[str, str], list[str]]:
    checks: dict[str, str] = {
        "persistence_backend": persistence_config.backend,
    }
    failures: list[str] = []

    try:
        database_status, database_detail = repository.health_check()
        checks["database"] = database_status
        if database_status != "ok":
            failures.append(
                f"Database unavailable: {database_detail or 'unknown repository error'}"
            )
    except Exception as exc:
        checks["database"] = "unavailable"
        failures.append(f"Database unavailable: {exc}")

    provider = os.getenv("CVI_LLM_PROVIDER", "groq").strip().lower()

    if provider == "groq":
        if not os.getenv("GROQ_API_KEY"):
            checks["model_provider"] = "not_configured"
            failures.append("GROQ_API_KEY is required for the groq provider.")
        else:
            checks["model_provider"] = "groq"
    elif provider == "ollama":
        checks["model_provider"] = "ollama"
    else:
        checks["model_provider"] = "invalid"
        failures.append(
            "CVI_LLM_PROVIDER must be one of: groq, ollama."
        )

    return checks, failures


@app.get("/health/ready")
def readiness_check():
    checks, failures = _readiness_checks()

    if failures:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "detail": " ".join(failures),
                "checks": checks,
            },
        )

    return {
        "status": "ok",
        "service": "customer-voice-interviewer",
        "checks": checks,
    }


@app.post("/demo/invites/{slug}", status_code=201)
def issue_demo_invite(slug: str, http_request: Request = None):
    """Issue a short-lived capability for a pre-created public demo session.

    The published page URL contains only ``/?demo=<slug>``. The raw capability
    is returned once in this response body and is held by the browser in
    memory/session storage; it is never placed back into the address bar.
    """

    if not _valid_demo_slug(slug):
        raise HTTPException(
            status_code=422,
            detail=(
                "Demo slug must be 3–64 letters, digits, underscores, "
                "or hyphens."
            ),
        )

    client_host = getattr(getattr(http_request, "client", None), "host", None)

    if not rate_limiter.allow(
        "demo_ip",
        abuse_limits.client_ip_key(client_host),
        rate_limiter.demo_limit,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many demo requests from this network. Please slow down.",
        )

    if not rate_limiter.allow(
        "demo_slug",
        slug,
        rate_limiter.demo_limit * 3,
    ):
        raise HTTPException(
            status_code=429,
            detail="This demo is busy right now. Please try again shortly.",
        )

    candidate = _find_demo_session(slug)

    if candidate is None:
        raise HTTPException(
            status_code=503,
            detail="No demo interview is available right now. Please try again shortly.",
        )

    session_id = candidate["session_id"]
    ttl_seconds = _demo_ttl_seconds()

    with session_processing_lock(session_id) as acquired:
        if not acquired:
            raise HTTPException(
                status_code=409,
                detail="This demo interview is being claimed. Please try again.",
            )

        session = read_model.get_session(
            session_id,
            tenant_id=current_tenant_id(),
        )

        if (
            session is None
            or session.get("demo_slug") != slug
            or session.get("interview_started_at")
            or not can_start_interview(session)
            or _demo_claim_is_active(session, ttl_seconds)
        ):
            raise HTTPException(
                status_code=503,
                detail="No demo interview is available right now. Please try again shortly.",
            )

        token = new_participant_token()
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
        issued_at_text = issued_at.isoformat(timespec="milliseconds")
        expires_at_text = expires_at.isoformat(timespec="milliseconds")

        session["participant_token_hash"] = hash_participant_token(token)
        session["participant_token_issued_at"] = issued_at_text
        session["link_expiry"] = "demo"
        session["link_expires_at"] = expires_at_text
        session["demo_claimed_at"] = issued_at_text
        session["updated_by"] = "demo-broker"
        _save_session(session)

        tenant = current_tenant_id()
        read_model.record_event(
            session_id,
            "demo_invite_issued",
            {
                "demo_slug": slug,
                "link_expires_at": expires_at_text,
                "ttl_seconds": ttl_seconds,
            },
            actor="participant",
            action="demo.invite_issued",
            resource="session",
            request_id=get_request_id(),
            tenant_id=tenant,
        )
        read_model.record_audit(
            action="demo.invite_issued",
            actor="participant",
            resource_type="session",
            resource_id=session_id,
            tenant_id=tenant,
            request_id=get_request_id(),
            metadata={"demo_slug": slug, "ttl_seconds": ttl_seconds},
        )

    return {
        "session_id": session_id,
        "participant_token": token,
        "demo_slug": slug,
        "link_expires_at": expires_at_text,
    }


@app.get("/sessions/{session_id}/invitation")
def get_participant_invitation(
    session_id: str,
    http_request: Request = None,
):
    """Minimal session details for a valid participant invitation link."""

    session = _require_participant_session(
        session_id,
        http_request=http_request,
        not_found_detail=(
            "This interview link is no longer valid. "
            "Please ask for a fresh interview link."
        ),
    )

    company_name = session.get("company_name") or "the research team"
    product_name = session.get("product_name")

    if product_name:
        welcome_message = (
            f"Welcome to a short voice interview about {product_name}. "
            "Your responses will help the research team understand real "
            "customer experience."
        )
    else:
        welcome_message = (
            "Welcome to a short voice interview. Your responses will help "
            "the research team understand real customer experience."
        )

    read_model.record_event(
        session_id,
        "participant_invitation_opened",
        {"status": session.get("status")},
    )

    return {
        "session_id": session_id,
        "company_name": company_name,
        "product_name": product_name,
        "welcome_message": welcome_message,
        "status": session.get("status"),
        "voice_profile": get_voice_profile().to_public_dict(),
    }


@app.post("/sessions/{session_id}/voice/transcribe")
def transcribe_voice(
    session_id: str,
    request: VoiceTranscriptionRequest,
    http_request: Request = None,
):
    """Transcribe recorded speech with the configured STT provider chain."""

    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    try:
        audio_bytes = base64.b64decode(
            request.audio_base64,
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="audio_base64 must be valid base64 data.",
        ) from exc

    if not audio_bytes:
        raise HTTPException(
            status_code=422,
            detail="Audio upload cannot be empty.",
        )

    if len(audio_bytes) > abuse_limits.MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio upload is too large.",
        )

    profile = get_voice_profile()

    try:
        result, failures = transcribe_with_fallback(
            audio_bytes,
            primary=profile.stt_provider,
            fallbacks=profile.stt_fallbacks,
            filename=request.filename,
            language=request.language or profile.language,
            prompt=request.prompt,
        )
    except STTProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    transcript = (result.text or "").strip()
    failure_detail = "; ".join(
        f"{item.get('provider')}: {item.get('reason')}"
        for item in failures
        if item.get("reason")
    )[:300] or None

    acknowledgement = None

    # Emit the same model-free acknowledgement used by the realtime chain,
    # but only when there is a transcript to accept.
    if transcript:
        speech_end_messages = create_vad_speech_end_messages(
            session_id,
            utterance_idx=0,
            turn=request.turn,
            speech_ms=float(getattr(request, "speech_duration_ms", 0) or 0),
        )
        if len(speech_end_messages) > 1:
            acknowledgement = speech_end_messages[1]

    return {
        "session_id": session_id,
        "transcript": transcript,
        "provider": result.provider,
        "language": result.language,
        "confidence": result.confidence,
        "metadata": result.metadata or {},
        "failures": failures,
        "failure_detail": failure_detail,
        "audio_issue": result.provider == "typed" or not transcript,
        "acknowledgement": acknowledgement,
    }


@app.post("/sessions/{session_id}/voice/synthesize")
def synthesize_voice(
    session_id: str,
    request: VoiceSynthesisRequest,
    http_request: Request = None,
):
    """Synthesize interviewer speech with the configured provider chain.

    Browser playback is returned as metadata. Cloud and local providers return
    audio bytes so the participant can use the same typed event contract.
    """

    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    profile = get_voice_profile()
    requested_delivery = request.delivery or {}
    kind = str(requested_delivery.get("kind") or "system")

    try:
        prosody = profile.prosody_for(kind).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    delivery = {
        "kind": kind,
        "language": profile.language,
        "provider": profile.tts_provider,
        "voice": profile.voice,
        **prosody,
        **requested_delivery,
    }

    try:
        result, failures = synthesize_with_fallback(
            request.text,
            primary=profile.tts_provider,
            fallbacks=profile.tts_fallbacks,
            delivery=delivery,
        )
    except TTSProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    headers = {
        "X-Voice-Provider": result.provider,
        "X-Voice-Playback": result.playback,
        "X-Voice-Fallbacks": json.dumps(failures),
    }

    if result.playback == "browser":
        return JSONResponse(
            {
                "status": "browser_playback",
                "session_id": session_id,
                "playback": "browser",
                "provider": result.provider,
                "delivery": delivery,
                "failures": failures,
            },
            headers=headers,
        )

    return Response(
        content=result.audio_bytes,
        media_type=result.content_type,
        headers=headers,
    )


@app.post("/sessions/{session_id}/timings", status_code=202)
def record_participant_timing(
    session_id: str,
    request: TimingRecordRequest,
    http_request: Request = None,
):
    """Store browser-side latency spans from the participant flow."""

    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    if request.ended_ms < request.started_ms:
        raise HTTPException(
            status_code=422,
            detail="ended_ms cannot be earlier than started_ms.",
        )

    timing.record(
        session_id,
        request.turn,
        request.span_name,
        request.started_ms,
        ended_ms=request.ended_ms,
        source="frontend",
        parent_span_id=request.parent_span_id,
        metadata={
            **request.metadata,
            "browser_clock": "performance.now",
        },
    )

    return {"status": "recorded", "session_id": session_id}


@app.post("/sessions", status_code=201)
def create_session(
    request: CreateSessionRequest,
    http_request: Request,
    context: Annotated[
        ResearcherContext | None,
        Depends(require_researcher_if_configured),
    ] = None,
):
    demo_slug = request.demo_slug.strip() if request.demo_slug else None

    if demo_slug and not _valid_demo_slug(demo_slug):
        raise HTTPException(
            status_code=422,
            detail=(
                "demo_slug must be 3–64 letters, digits, underscores, "
                "or hyphens."
            ),
        )

    try:
        session = create_research_session(
            company_id=request.company_id,
            company_name=request.company_name,
            product_id=request.product_id,
            product_name=request.product_name,
            product_category=request.product_category,
            research_goal=request.research_goal,
            start_date=request.start_date,
            end_date=request.end_date,
            target_audience=request.target_audience,
            customer_segment=request.customer_segment,
            participant_id=request.participant_id,
            interview_constraints=request.interview_constraints,
            link_expiry=request.link_expiry,
        )
        session = activate_research_session(session)
        tenant = _tenant(context)
        session["tenant_id"] = tenant
        session["demo_slug"] = demo_slug
        participant_token = new_participant_token()
        session["participant_token_hash"] = hash_participant_token(
            participant_token
        )
        session["participant_token_issued_at"] = (
            datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        )
        session["created_by"] = _actor(context)
        session["updated_by"] = _actor(context)
        actor = _actor(context)
        request_id = _request_id(context)

        # Cache only; the repository write below is the source of truth.
        _cache_session(session)

        read_model.upsert_session(session)
        read_model.record_snapshot(
            session["session_id"],
            {},
            "session_created",
            session["status"],
            tenant_id=tenant,
        )

        read_model.record_event(
            session["session_id"],
            "session_created",
            {
                "company_id": session["company_id"],
                "product_id": session["product_id"],
                "research_goal": session["research_goal"],
            },
            actor=actor,
            action="session.created",
            resource="session",
            request_id=request_id,
            tenant_id=tenant,
        )
        read_model.record_audit(
            action="session.created",
            actor=actor,
            resource_type="session",
            resource_id=session["session_id"],
            tenant_id=tenant,
            request_id=request_id,
            metadata={"invitation": "issued"},
        )

        return {
            "session_id": session["session_id"],
            "status": session["status"],
            "company_name": session["company_name"],
            "product_name": session["product_name"],
            "research_goal": session["research_goal"],
            "start_date": session["start_date"],
            "end_date": session["end_date"],
            "link_expiry": session.get("link_expiry"),
            "link_expires_at": session.get("link_expires_at"),
            "demo_slug": demo_slug,
            "participant_url": (
                "/?"
                + urlencode(
                    {
                        "session_id": session["session_id"],
                        "participant_token": participant_token,
                    }
                )
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@app.post("/sessions/{session_id}/start")
def start_interview(
    session_id: str,
    http_request: Request = None,
):
    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    # Claim the one-time start under the same lock used by background work.
    # The durable lifecycle is written before the first graph call, so two
    # concurrent requests cannot reinitialize the same LangGraph thread.
    with session_processing_lock(session_id) as acquired:
        if not acquired:
            raise HTTPException(
                status_code=409,
                detail="This interview is already being processed.",
            )

        session = resolve_session(session_id)

        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")

        if get_pending_interrupt(session_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="This interview has already started.",
            )

        if not can_start_interview(session):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This interview has already started, completed, "
                    "or failed. Ask a researcher for a fresh link."
                ),
            )

        reject_if_link_expired(session_id, session)

        tenant = read_model.tenant_for_session(session_id)
        read_model.record_event(
            session_id,
            "interview_started",
            actor="participant",
            action="session.started",
            resource="session",
            tenant_id=tenant,
        )
        read_model.record_audit(
            action="session.started",
            actor="participant",
            resource_type="session",
            resource_id=session_id,
            tenant_id=tenant,
            metadata={"phase": "interview_start"},
        )

        try:
            started_session = begin_interview(session)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc

        # Persist the claim before releasing the lock.
        started_session = _save_session(started_session)

    try:
        started_ms = timing.now_ms()

        result = graph.invoke(
            create_initial_state(started_session),
            config=thread_config(session_id),
        )

        persist_graph_state(
            session_id,
            result,
            "interview_started",
        )

        next_action = extract_next_action(session_id, result)

        if next_action.get("action", {}).get("type") == (
            "deliver_question"
        ):
            _record_timing(
                session_id,
                1,
                "first_question_delivered",
                started_ms,
                source="runtime",
                metadata={"phase": "interview_start"},
            )

        return next_action

    except Exception as exc:
        current = resolve_session(session_id)

        if (
            current is not None
            and normalize_interview_status(current)
            not in {"completed", "failed"}
        ):
            _apply_interview_transition(
                session_id,
                fail_interview,
                error_type=type(exc).__name__,
            )

        read_model.record_event(
            session_id,
            "interview_failed",
            {"error_type": type(exc).__name__},
        )

        logger.exception(
            "interview_start_failed",
            extra={"session_id": session_id},
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not start interview ({type(exc).__name__}). "
                "Check the server logs for details."
            ),
        ) from exc



@app.post("/sessions/{session_id}/advance")
def advance_interview(
    session_id: str,
    http_request: Request = None,
):
    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    pending = get_pending_interrupt(session_id)
    if pending is None:
        raise HTTPException(
            status_code=409,
            detail="No pending interview action to acknowledge.",
        )

    if pending.get("type") not in {"deliver_question", "response_recovery"}:
        raise HTTPException(
            status_code=409,
            detail="Submit a response before advancing this interview.",
        )

    try:
        started_ms = timing.now_ms()

        result = graph.invoke(
            Command(resume=True),
            config=thread_config(session_id),
        )

        persist_graph_state(
            session_id,
            result,
            "interview_advanced",
        )

        next_action = extract_next_action(session_id, result)

        if next_action.get("action", {}).get("type") == (
            "await_consumer_response"
        ):
            _record_timing(
                session_id,
                int(pending.get("turn") or 0),
                "question_advanced_to_listening",
                started_ms,
                source="runtime",
                metadata={"phase": "question_delivery"},
            )

        return next_action
    except Exception as exc:
    	logger.exception(
    	    "interview_advance_failed",
    	    extra={"session_id": session_id},
    	)
    	raise HTTPException(
            status_code=500,
            detail=f"Could not advance interview ({type(exc).__name__}). Check the server logs for details."
    	) from exc

def schedule_response_submission(
    session_id: str,
    request: VoiceResponseRequest,
    http_request: Optional[Request] = None,
    participant_token: Optional[str] = None,
):
    """Validate and schedule a consumer response without global serialization.

    Duplicate protection lives in the durable queue's unique active-job index
    for worker mode. The process-local check remains only for offline tests.
    """

    submission_started_ms = timing.now_ms()

    session = _require_participant_session(
        session_id,
        http_request=http_request,
        participant_token=participant_token,
    )

    if has_processing_response_job(session_id):
        raise HTTPException(
            status_code=409,
            detail="A response is already being processed.",
        )

    pending = get_pending_interrupt(session_id)

    if not pending or pending.get("type") != "await_consumer_response":
        raise HTTPException(
            status_code=409,
            detail="The interview is not currently waiting for a response.",
        )

    turn = int(pending.get("turn") or 0)

    transcript = request.transcript
    transcript = transcript.strip() if transcript else None

    response_event = {
        "session_id": session_id,
        "interview_id": session_id,
        "response_id": f"resp_{uuid4().hex}",
        "captured_at": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "participant_id": participant_id_for_session(session),
        "turn": turn,
        "submitted_monotonic_ms": submission_started_ms,
        "question": pending["question"],
        "interaction_mode": pending["interaction_mode"],
        "transcript": transcript,
        "speech_detected": bool(transcript),
        "speech_duration_ms": request.speech_duration_ms,
        "audio_issue": request.audio_issue,
        "timestamp": time.time(),
    }

    read_model.record_event(
        session_id,
        "response_submitted",
        response_event,
    )

    response_jobs[session_id] = {
        "status": "processing",
        "payload": response_event,
    }

    voice_output_queues.setdefault(
        session_id,
        Queue(),
    )

    scheduled_job_id = None

    try:
        if WORKER_ENABLED:
            scheduled_job_id = job_service.new_job_id(
                job_service.PROCESS_RESPONSE
            )
            repository.enqueue_job(
                job_id=scheduled_job_id,
                job_type=job_service.PROCESS_RESPONSE,
                session_id=session_id,
                payload=response_event,
                tenant_id=_tenant_for_runtime(session_id),
                max_attempts=1,
                unique_active=True,
            )
            response_jobs[session_id]["job_id"] = scheduled_job_id
        else:
            response_executor.submit(
                process_response_in_background,
                session_id,
                response_event,
            )

    except DuplicateJobError as exc:
        response_jobs[session_id] = {
            "status": "failed",
            "error": type(exc).__name__,
            "payload": response_event,
        }
        raise HTTPException(
            status_code=409,
            detail="A response is already being processed.",
        ) from exc

    except Exception as exc:
        response_jobs[session_id] = {
            "status": "failed",
            "error": type(exc).__name__,
            "payload": response_event,
        }

        raise HTTPException(
            status_code=500,
            detail="Could not schedule response processing.",
        ) from exc

    _record_timing(
        session_id,
        turn,
        "response_submission_scheduled",
        submission_started_ms,
        source="runtime",
        metadata={
            "response_id": response_event["response_id"],
            "job_id": scheduled_job_id,
        },
    )

    return {
        "status": "processing",
        "session_id": session_id,
        "message": "Your response is being processed.",
    }

@app.post("/sessions/{session_id}/responses", status_code=202)
def submit_response(
    session_id: str,
    request: VoiceResponseRequest,
    http_request: Request = None,
):
    return schedule_response_submission(
        session_id,
        request,
        http_request=http_request,
    )


@app.post("/sessions/{session_id}/responses/retry", status_code=202)
def retry_response_processing(
    session_id: str,
    http_request: Request = None,
):
    """Re-run a failed response without recording a duplicate answer."""

    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    if has_processing_response_job(session_id):
        raise HTTPException(
            status_code=409,
            detail="A response is already being processed.",
        )

    raw_job = _latest_raw_response_job(session_id)

    if not _is_failed_response_job(raw_job):
        raise HTTPException(
            status_code=409,
            detail="There is no failed response to retry.",
        )

    payload = dict(raw_job.get("payload") or {})
    response_id = payload.get("response_id")

    if not response_id:
        raise HTTPException(
            status_code=409,
            detail="The failed response cannot be retried safely.",
        )

    retry_payload = dict(payload)
    retry_payload["retry_mode"] = (
        "resume"
        if _graph_waiting_for_response(session_id, payload)
        else "continue"
    )
    retry_payload["retry_of_job_id"] = raw_job.get("job_id")

    job_id = job_service.new_job_id(job_service.PROCESS_RESPONSE)

    response_jobs[session_id] = {
        "status": "processing",
        "payload": retry_payload,
        "job_id": job_id,
    }

    try:
        if WORKER_ENABLED:
            repository.enqueue_job(
                job_id=job_id,
                job_type=job_service.PROCESS_RESPONSE,
                session_id=session_id,
                payload=retry_payload,
                tenant_id=_tenant_for_runtime(session_id),
                max_attempts=1,
                unique_active=True,
            )
        else:
            response_executor.submit(
                process_response_in_background,
                session_id,
                retry_payload,
            )

    except DuplicateJobError as exc:
        response_jobs[session_id] = {
            "status": "failed",
            "error": type(exc).__name__,
            "payload": payload,
        }
        raise HTTPException(
            status_code=409,
            detail="A response is already being processed.",
        ) from exc

    except Exception as exc:
        response_jobs[session_id] = {
            "status": "failed",
            "error": type(exc).__name__,
            "payload": payload,
        }
        raise HTTPException(
            status_code=500,
            detail="Could not schedule response retry.",
        ) from exc

    read_model.record_event(
        session_id,
        "response_processing_retry_requested",
        {
            "response_id": response_id,
            "job_id": job_id,
            "retry_of_job_id": raw_job.get("job_id"),
            "mode": retry_payload["retry_mode"],
        },
    )

    return {
        "status": "processing",
        "session_id": session_id,
        "response_id": response_id,
        "job_id": job_id,
        "mode": retry_payload["retry_mode"],
    }


@app.get("/sessions/{session_id}/status")
def get_response_status(
    session_id: str,
    http_request: Request = None,
):
    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    job = current_response_job(session_id)

    if not job:
        pending = get_pending_interrupt(session_id)
        if pending:
            return {
                "status": "completed",
                "session_id": session_id,
                "result": {
                    "status": "waiting",
                    "session_id": session_id,
                    "action": pending,
                },
            }
        return {
            "status": "idle",
            "session_id": session_id,
        }

    if job["status"] == "processing":
        return {
            "status": "processing",
            "session_id": session_id,
        }

    if job["status"] == "completed":
        return {
            "status": "completed",
            "session_id": session_id,
            "result": job["result"],
        }

    if job["status"] == "failed":
        raw = _latest_raw_response_job(session_id)
        payload = (raw or {}).get("payload") or {}
        can_retry = bool(payload.get("response_id")) and not (
            has_processing_response_job(session_id)
        )

        return {
            "status": "failed",
            "session_id": session_id,
            "error": job.get("error"),
            "can_retry": can_retry,
        }

    return {
        "status": "unknown",
        "session_id": session_id,
    }


def _publish_offline_voice_event(session_id: str, event: dict) -> str:
    """Append an outbound voice event to the offline ack-tracked outbox."""

    event_id = f"voice_{uuid4().hex}"

    with engagement_state_lock:
        voice_output_events.setdefault(session_id, []).append(
            {"event_id": event_id, "event": event}
        )

    return event_id


def _adopt_legacy_voice_queues(session_id: str) -> None:
    """Move legacy queue entries into the ack-tracked outbox on first read."""

    with engagement_state_lock:
        output_queue = voice_output_queues.get(session_id)

        if not output_queue:
            return

        records = voice_output_events.setdefault(session_id, [])

        while not output_queue.empty():
            try:
                event = output_queue.get_nowait()
            except Exception:
                break

            records.append(
                {
                    "event_id": f"voice_{uuid4().hex}",
                    "event": event,
                }
            )


def _list_offline_voice_events(
    session_id: str,
    limit: int = 100,
) -> list[dict]:
    _adopt_legacy_voice_queues(session_id)

    with engagement_state_lock:
        records = list(voice_output_events.get(session_id, []))

    return [
        {**record["event"], "event_id": record["event_id"]}
        for record in records[:limit]
    ]


def _acknowledge_offline_voice_events(
    session_id: str,
    event_ids: list[str],
) -> int:
    with engagement_state_lock:
        records = voice_output_events.get(session_id, [])
        ids = {str(event_id) for event_id in event_ids if event_id}
        remaining = [
            record
            for record in records
            if record["event_id"] not in ids
        ]
        acknowledged = len(records) - len(remaining)

        if remaining:
            voice_output_events[session_id] = remaining
        else:
            voice_output_events.pop(session_id, None)

    return acknowledged


@app.get("/sessions/{session_id}/voice-events")
def get_voice_events(
    session_id: str,
    http_request: Request = None,
):

    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    if WORKER_ENABLED:
        records = repository.list_voice_events(
            session_id=session_id,
            tenant_id=_tenant_for_runtime(session_id),
            limit=100,
        )
        events = [
            {**record["event"], "event_id": record["event_id"]}
            for record in records
        ]
        pending = repository.count_pending_jobs(
            session_id=session_id,
            job_type=job_service.CONTEXTUAL_ENGAGEMENT,
            tenant_id=_tenant_for_runtime(session_id),
        )

        return {
            "session_id": session_id,
            "events": events,
            "pending": pending,
        }

    events = _list_offline_voice_events(session_id)

    with engagement_state_lock:
        pending = pending_engagement_tasks.get(
            session_id, 0
        )

    return {
        "session_id": session_id,
        "events": events,
        "pending": pending,
    }


@app.post("/sessions/{session_id}/voice-events/ack")
def acknowledge_voice_events(
    session_id: str,
    request: VoiceEventAckRequest,
    http_request: Request = None,
):
    """Acknowledge delivered voice events for one authorized session."""

    _require_participant_session(
        session_id,
        http_request=http_request,
    )

    event_ids = request.event_ids

    if WORKER_ENABLED:
        acknowledged = repository.acknowledge_voice_events(
            session_id=session_id,
            tenant_id=_tenant_for_runtime(session_id),
            event_ids=event_ids,
        )
    else:
        acknowledged = _acknowledge_offline_voice_events(
            session_id,
            event_ids,
        )

    with engagement_state_lock:
        pending = pending_engagement_tasks.get(session_id, 0)

    return {
        "session_id": session_id,
        "acknowledged": acknowledged,
        "pending": pending,
    }


@app.websocket("/sessions/{session_id}/voice")
async def sarvam_voice_websocket(
    websocket: WebSocket,
    session_id: str,
):
    await websocket.accept()

    participant_token = _participant_token_from_request(websocket)
    session = resolve_session(session_id)

    if session is None:
        await websocket.send_json({
            "type": "error",
            "message": "Session not found.",
        })
        await websocket.close(code=4404)
        return

    # Validate the participant capability before opening upstream work.
    if not participant_token_is_valid(session, participant_token):
        await websocket.send_json({
            "type": "error",
            "message": (
                "A valid participant link is required. "
                "Please open the interview from your invitation link."
            ),
        })
        await websocket.close(code=4401)
        return

    if await reject_websocket_if_link_expired(
        websocket,
        session_id,
        session,
    ):
        return

    if await reject_websocket_if_rate_limited(websocket, session_id):
        return

    pending = get_pending_interrupt(session_id)

    if not pending or pending.get("type") != "await_consumer_response":
        await websocket.send_json({
            "type": "error",
            "message": "The interview is not currently waiting for a response.",
        })
        await websocket.close(code=4409)
        return

    if await reject_websocket_if_stream_crowded(websocket, session_id):
        return

    stream_budget = abuse_limits.VoiceStreamBudget()

    try:
        client = create_sarvam_client()

        async with client.speech_to_text_realtime_streaming.connect(
            language_code=STT_LANGUAGE_CODE,
            model=STT_MODEL,
            stream_type=STT_STREAM_TYPE,
            mode=STT_MODE,
            endpointing=STT_ENDPOINTING,
            encoding=STT_ENCODING,
            sample_rate=STT_SAMPLE_RATE,
        ) as sarvam_ws:

            await websocket.send_json({
                "type": "stt_ready",
                "provider": "sarvam",
                "language_code": STT_LANGUAGE_CODE,
            })

            async def forward_browser_audio():
                """
                Forward binary PCM16 frames and handle recording controls.
                """

                stop_sent = False

                while True:
                    message = await websocket.receive()

                    if message["type"] == "websocket.disconnect":
                        return

                    audio_bytes = message.get("bytes")

                    if audio_bytes:
                        violation = stream_budget.check_frame(
                            len(audio_bytes)
                        )

                        if violation:
                            await reject_voice_stream_limit(
                                websocket,
                                violation,
                            )
                            return

                        if stop_sent:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "Recording has already been stopped.",
                            })
                            continue

                        audio_input = create_realtime_audio_input(
                            audio_bytes
                        )

                        await sarvam_ws.send_realtime_audio_input(
                            audio_input
                        )

                    elif message.get("text"):
                        violation = stream_budget.check_control(
                            message["text"]
                        )

                        if violation:
                            await reject_voice_stream_limit(
                                websocket,
                                violation,
                            )
                            return

                        try:
                            control = json.loads(message["text"])
                        except json.JSONDecodeError:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "Invalid control message.",
                            })
                            continue

                        if control.get("type") == "stop":
                            if not stop_sent:
                                stop_sent = True

                                await sarvam_ws.send_realtime_end(
                                    create_realtime_end()
                                )

                                await websocket.send_json({
                                    "type": "recording_stopped",
                                })

                            # Important:
                            # Do not return here. Keep the browser receiver
                            # alive while Sarvam produces transcript.final.
                            continue

                        await websocket.send_json({
                            "type": "warning",
                            "message": "Unsupported control message.",
                        })


            async def receive_sarvam_events():
                """
                Forward STT events and submit only final transcripts.
                """

                while True:
                    if stream_budget.check_duration():
                        await reject_voice_stream_limit(
                            websocket,
                            "duration",
                        )
                        return

                    event = await sarvam_ws.recv()

                    event_type = getattr(event, "event", None)

                    # Interim transcript: browser display only.
                    if event_type == "transcript.partial":
                        await websocket.send_json({
                            "type": "transcript_partial",
                            "text": event.text,
                            "utterance_idx": event.utterance_idx,
                        })

                    # End of speech: acknowledgement may now be played.
                    elif event_type == "vad.speech_end":
                        speech_end_started_ms = timing.now_ms()

                        for message in create_vad_speech_end_messages(
                            session_id,
                            getattr(event, "utterance_idx", None),
                            turn=int(pending.get("turn") or 0),
                            started_ms=speech_end_started_ms,
                            speech_ms=float(
                                getattr(event, "speech_duration_ms", 0) or 0
                            ),
                        ):
                            await websocket.send_json(message)

                    # Final transcript: submit exactly once.
                    elif event_type == "transcript.final":
                        transcript = (event.text or "").strip()

                        if not transcript:
                            continue

                        try:
                            submission_kwargs = (
                                {"participant_token": participant_token}
                                if participant_token
                                else {}
                            )
                            result = schedule_response_submission(
                                session_id,
                                VoiceResponseRequest(
                                    transcript=transcript,
                                    speech_duration_ms=0,
                                    audio_issue=False,
                                ),
                                **submission_kwargs,
                            )

                        except HTTPException as exc:
                            await websocket.send_json({
                                "type": "error",
                                "message": exc.detail,
                                "status_code": exc.status_code,
                            })
                            return

                        await websocket.send_json({
                            "type": "transcript_final",
                            "text": transcript,
                            "utterance_idx": event.utterance_idx,
                        })

                        await websocket.send_json({
                            "type": "response_submitted",
                            "status": result["status"],
                        })

                        return

                    elif event_type == "error":
                        await websocket.send_json({
                            "type": "error",
                            "message": str(
                                getattr(event, "message", "Sarvam STT error.")
                            ),
                        })
                        return

            audio_task = asyncio.create_task(
                forward_browser_audio()
            )

            events_task = asyncio.create_task(
                receive_sarvam_events()
            )

            try:
                # Finish when either the browser disconnects or
                # Sarvam delivers the final transcript.
                done, pending_tasks = await asyncio.wait(
                    {audio_task, events_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

            finally:
                for task in (audio_task, events_task):
                    if not task.done():
                        task.cancel()

                await asyncio.gather(
                    audio_task,
                    events_task,
                    return_exceptions=True,
                )

    except WebSocketDisconnect:
        pass

    except Exception as exc:
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Voice connection failed.",
                "error_type": type(exc).__name__,
            })
        except Exception:
            pass

    finally:
        release_voice_stream(session_id)
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/sessions/{session_id}/voice/local")
async def local_voice_websocket(
    websocket: WebSocket,
    session_id: str,
):
    """Bridge one browser mic stream to the server-side VAD speech service."""

    await websocket.accept()

    participant_token = _participant_token_from_request(websocket)
    session = resolve_session(session_id)

    if session is None:
        await websocket.send_json({
            "type": "error",
            "message": "Session not found.",
        })
        await websocket.close(code=4404)
        return

    # Validate the participant capability before opening upstream work.
    if not participant_token_is_valid(session, participant_token):
        await websocket.send_json({
            "type": "error",
            "message": (
                "A valid participant link is required. "
                "Please open the interview from your invitation link."
            ),
        })
        await websocket.close(code=4401)
        return

    if await reject_websocket_if_link_expired(
        websocket,
        session_id,
        session,
    ):
        return

    if await reject_websocket_if_rate_limited(websocket, session_id):
        return

    pending = get_pending_interrupt(session_id)

    if not pending or pending.get("type") != "await_consumer_response":
        await websocket.send_json({
            "type": "error",
            "message": "The interview is not currently waiting for a response.",
        })
        await websocket.close(code=4409)
        return

    if await reject_websocket_if_stream_crowded(websocket, session_id):
        return

    stream_budget = abuse_limits.VoiceStreamBudget()

    submitted = False

    try:
        async with connect_local_speech_stream(local_stream_url()) as upstream:
            async def forward_browser_audio():
                while True:
                    message = await websocket.receive()

                    if message["type"] == "websocket.disconnect":
                        return

                    audio = message.get("bytes")

                    if audio:
                        violation = stream_budget.check_frame(len(audio))

                        if violation:
                            await reject_voice_stream_limit(
                                websocket,
                                violation,
                            )
                            return

                        await upstream.send(audio)
                        continue

                    text = message.get("text")

                    if text:
                        violation = stream_budget.check_control(text)

                        if violation:
                            await reject_voice_stream_limit(
                                websocket,
                                violation,
                            )
                            return

                        await upstream.send(text)

            async def receive_stream_events():
                nonlocal submitted

                while True:
                    if stream_budget.check_duration():
                        await reject_voice_stream_limit(
                            websocket,
                            "duration",
                        )
                        return

                    raw = await upstream.recv()

                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if not isinstance(event, dict):
                        continue

                    event_type = event.get("type")

                    if event_type == "stream_ready":
                        await websocket.send_json({
                            "type": "stt_ready",
                            "provider": "local",
                            "vad": event.get("vad"),
                        })

                    elif event_type == "speech_start":
                        await websocket.send_json({
                            "type": "speech_start",
                            "utterance_idx": event.get("utterance_idx"),
                        })

                    elif event_type == "speech_discarded":
                        await websocket.send_json({
                            "type": "speech_discarded",
                            "reason": event.get("reason"),
                        })

                    elif event_type == "speech_end":
                        for message in create_vad_speech_end_messages(
                            session_id,
                            event.get("utterance_idx"),
                            turn=int(pending.get("turn") or 0),
                            speech_ms=float(event.get("speech_ms") or 0),
                        ):
                            await websocket.send_json(message)

                    elif event_type == "transcript_final":
                        transcript = (
                            event.get("text") or ""
                        ).strip()

                        if not transcript:
                            continue

                        speech_ms = int(event.get("speech_ms") or 0)
                        normalized = " ".join(
                            transcript.lower().split()
                        ).rstrip(".!?")
                        word_count = len([
                            w for w in normalized.split(" ") if w
                        ])

                        # Ignore very short fragments and likely speaker
                        # echoes of system prompts/acknowledgements.
                        known_echoes = {
                            "mm hmm",
                            "mhm",
                            "i see",
                            "right",
                            "got it",
                            "okay",
                            "understood",
                            "let's try that question again",
                            "lets try that question again",
                        }
                        question_text = " ".join(
                            str(pending.get("question") or "").lower().split()
                        ).rstrip(".!?")
                        if question_text:
                            known_echoes.add(question_text)

                        if normalized in known_echoes:
                            await websocket.send_json({
                                "type": "warning",
                                "message": "Ignored system audio echo.",
                            })
                            continue

                        # Keep the floor low: energy VAD on 512MB hosts can
                        # end segments late, and over-filtering drops valid
                        # short answers. The frontend still allows manual stop.
                        if speech_ms < 350 or word_count < 1:
                            await websocket.send_json({
                                "type": "warning",
                                "message": (
                                    "I didn't catch that clearly. "
                                    "Please answer when you're ready."
                                ),
                            })
                            continue

                        try:
                            submission_kwargs = (
                                {"participant_token": participant_token}
                                if participant_token
                                else {}
                            )
                            result = schedule_response_submission(
                                session_id,
                                VoiceResponseRequest(
                                    transcript=transcript,
                                    speech_duration_ms=speech_ms,
                                    audio_issue=False,
                                ),
                                **submission_kwargs,
                            )
                        except HTTPException as exc:
                            await websocket.send_json({
                                "type": "error",
                                "message": exc.detail,
                                "status_code": exc.status_code,
                            })
                            return

                        await websocket.send_json({
                            "type": "transcript_final",
                            "text": transcript,
                            "utterance_idx": event.get("utterance_idx"),
                        })
                        await websocket.send_json({
                            "type": "response_submitted",
                            "status": result["status"],
                        })
                        submitted = True
                        return

                    elif event_type == "error":
                        await websocket.send_json({
                            "type": "error",
                            "message": event.get(
                                "message",
                                "Local speech service error.",
                            ),
                        })
                        return

                    elif event_type == "warning":
                        await websocket.send_json({
                            "type": "warning",
                            "message": event.get("message", ""),
                        })

            audio_task = asyncio.create_task(
                forward_browser_audio()
            )
            events_task = asyncio.create_task(
                receive_stream_events()
            )

            try:
                done, pending_tasks = await asyncio.wait(
                    {audio_task, events_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in done:
                    exc = task.exception()

                    if exc is not None:
                        raise exc

            finally:
                for task in (audio_task, events_task):
                    if not task.done():
                        task.cancel()

                await asyncio.gather(
                    audio_task,
                    events_task,
                    return_exceptions=True,
                )

    except WebSocketDisconnect:
        pass

    except Exception as exc:
        if not submitted:
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": "Local voice stream failed.",
                    "error_type": type(exc).__name__,
                })
            except Exception:
                pass

    finally:
        release_voice_stream(session_id)
        try:
            await websocket.close()
        except Exception:
            pass


def get_session_activity(
    session_id: str,
    session: dict,
    state: dict,
) -> str:
    """Derive a researcher-facing phase from live API state."""

    job = current_response_job(session_id)

    if job:

        if job.get("status") == "processing":
            return "processing_response"

        if job.get("status") == "failed":
            return "failed"

        result = job.get("result") or {}

        if result.get("status") == "completed":
            return "completed"

        if result.get("status") == "waiting":
            return "waiting"

    pending = get_pending_interrupt(session_id)

    if pending:

        pending_type = pending.get("type")

        if pending_type == "await_consumer_response":
            return "awaiting_response"

        if pending_type == "deliver_question":
            return "awaiting_question_delivery"

        if pending_type == "response_recovery":
            return "awaiting_response_recovery"

    if state.get("interview_complete"):
        return "completed"

    return session.get("status", "idle")


@app.get("/researcher/sessions")
def list_researcher_sessions(
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """List sessions from the durable read model for this tenant."""

    results = []

    for session in read_model.list_sessions(tenant_id=_tenant(context)):
        if not _can_access_session(context, session):
            continue

        session_id = session["session_id"]
        projection = (
            read_model.get_latest_snapshot(
                session_id,
                tenant_id=_tenant(context),
            )
            or read_model.build_projection(
                session,
                {},
                session.get("status", "idle"),
            )
        )

        results.append({
            "session_id": session_id,
            "activity": projection["session"]["activity"],
            "company_name": session.get("company_name"),
            "product_name": session.get("product_name"),
            "product_category": session.get("product_category"),
            "research_goal": session.get("research_goal"),
            "customer_segment": session.get("customer_segment"),
            "start_date": session.get("start_date"),
            "created_at": read_model.get_session_created_at(
                session_id,
                tenant_id=_tenant(context),
            ),
            "link_expiry": session.get("link_expiry"),
            "link_expires_at": session.get("link_expires_at"),
            "iteration_count": projection["iteration_count"],
            "questions_asked_count": (
                projection["questions_asked_count"]
            ),
            "responses_count": projection["responses_count"],
            "evidence_count": projection["evidence_count"],
            "unresolved_gaps_count": (
                projection["unresolved_gaps_count"]
            ),
            "has_final_insights": projection["has_final_insights"],
        })

    return {"sessions": results}


@app.get("/researcher/sessions/{session_id}")
def get_researcher_session(
    session_id: str,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """Return a durable structured research view for one session."""

    session = _require_session_access(context, session_id)

    projection = (
        read_model.get_latest_snapshot(
            session_id,
            tenant_id=_tenant(context),
        )
        or read_model.build_projection(
            session,
            {},
            session.get("status", "idle"),
        )
    )

    projection["snapshots"] = read_model.list_snapshot_metadata(
        session_id,
        tenant_id=_tenant(context),
    )
    projection["events"] = read_model.list_events(
        session_id,
        tenant_id=_tenant(context),
    )
    projection["timings"] = read_model.list_timings(
        session_id,
        tenant_id=_tenant(context),
    )

    return projection


@app.get("/researcher/sessions/{session_id}/timings")
def get_session_timings(
    session_id: str,
    span_name: Optional[str] = None,
    source: Optional[str] = None,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """Return conversation latency spans for one session."""

    _require_session_access(context, session_id)

    return {
        "session_id": session_id,
        "timings": read_model.list_timings(
            session_id,
            span_name=span_name,
            source=source,
            tenant_id=_tenant(context),
        ),
    }


def _report_filters(
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    customer_segment: Optional[str] = None,
    product_category: Optional[str] = None,
    research_goal: Optional[str] = None,
    session_id: Optional[str] = None,
    status_label: Optional[str] = None,
    response_pattern_label: Optional[str] = None,
    tag: Optional[str] = None,
) -> dict:
    return {
        "period_start": (
            period_start.isoformat() if period_start else None
        ),
        "period_end": (
            period_end.isoformat() if period_end else None
        ),
        "customer_segment": customer_segment,
        "product_category": product_category,
        "research_goal": research_goal,
        "session_id": session_id,
        "status_label": status_label,
        "response_pattern_label": response_pattern_label,
        "tag": tag,
    }


def _matching_reports(filters: dict, context) -> list[dict]:
    reports = read_model.list_reports(
        period_start=filters["period_start"],
        period_end=filters["period_end"],
        customer_segment=filters["customer_segment"],
        product_category=filters["product_category"],
        research_goal=filters["research_goal"],
        session_id=filters["session_id"],
        status_label=filters["status_label"],
        response_pattern_label=filters["response_pattern_label"],
        tag=filters["tag"],
        tenant_id=_tenant(context),
    )

    if _context_or_default(context).role == "admin":
        return reports

    visible_session_ids = _visible_session_ids(context)

    return [
        report
        for report in reports
        if report.get("session_id") in visible_session_ids
    ]


@app.get("/researcher/evidence")
def list_researcher_evidence(
    q: Optional[str] = None,
    session_id: Optional[str] = None,
    evidence_id: Optional[str] = None,
    response_id: Optional[str] = None,
    turn: Optional[int] = None,
    category: Optional[str] = None,
    parameter: Optional[str] = None,
    include_unverified: bool = False,
    limit: int = 100,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """Search citation-ready evidence from authoritative research memory."""

    if limit < 0 or limit > 500:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 0 and 500.",
        )

    filters = {
        "q": q or None,
        "session_id": session_id,
        "evidence_id": evidence_id,
        "response_id": response_id,
        "turn": turn,
        "category": category,
        "parameter": parameter,
        "include_unverified": include_unverified,
        "limit": limit,
    }

    resolved = _context_or_default(context)
    visible_session_ids = (
        None
        if resolved.role == "admin"
        else _visible_session_ids(context)
    )

    if (
        session_id
        and visible_session_ids is not None
        and session_id not in visible_session_ids
    ):
        raise HTTPException(
            status_code=404,
            detail="Session not found.",
        )

    common = {
        "tenant_id": _tenant(context),
        "session_id": session_id,
        "evidence_id": evidence_id,
        "response_id": response_id,
        "turn": turn,
        "category": category,
        "parameter": parameter,
        "include_unverified": include_unverified,
    }

    # Retrieve every candidate before ownership filtering so one researcher's
    # records cannot consume another researcher's result budget.
    retrieval_limit = limit if visible_session_ids is None else None

    if q and q.strip():
        items = evidence_retrieval.search_evidence(
            **common,
            query=q,
            limit=retrieval_limit,
        )
    else:
        items = evidence_retrieval.retrieve_evidence(
            **common,
            limit=retrieval_limit,
        )

    if visible_session_ids is not None:
        items = [
            item
            for item in items
            if item.get("session_id") in visible_session_ids
        ][:limit]

    read_model.record_audit(
        action="evidence.retrieved",
        actor=_actor(context),
        resource_type="evidence",
        resource_id=session_id or "all",
        tenant_id=_tenant(context),
        request_id=_request_id(context),
        metadata={
            "filters": filters,
            "result_count": len(items),
            "ranking": "lexical" if q and q.strip() else "deterministic",
        },
    )

    return {
        "filters": filters,
        "count": len(items),
        "items": items,
    }


@app.get("/researcher/analysis/reports")
def list_analysis_reports(
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    customer_segment: Optional[str] = None,
    product_category: Optional[str] = None,
    research_goal: Optional[str] = None,
    session_id: Optional[str] = None,
    status_label: Optional[str] = None,
    response_pattern_label: Optional[str] = None,
    tag: Optional[str] = None,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """List and aggregate grounded reports by reporting dimensions."""

    filters = _report_filters(
        period_start,
        period_end,
        customer_segment,
        product_category,
        research_goal,
        session_id,
        status_label,
        response_pattern_label,
        tag,
    )
    reports = _matching_reports(filters, context)

    return {
        "filters": filters,
        "aggregate": analysis_report.aggregate_reports(reports),
        "reports": reports,
    }


@app.get("/researcher/analysis/reports/export.csv")
def export_analysis_reports(
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    customer_segment: Optional[str] = None,
    product_category: Optional[str] = None,
    research_goal: Optional[str] = None,
    session_id: Optional[str] = None,
    status_label: Optional[str] = None,
    response_pattern_label: Optional[str] = None,
    tag: Optional[str] = None,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """Export grounded claims as an auditable CSV ledger."""

    filters = _report_filters(
        period_start,
        period_end,
        customer_segment,
        product_category,
        research_goal,
        session_id,
        status_label,
        response_pattern_label,
        tag,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "report_id",
        "session_id",
        "period_start",
        "period_end",
        "customer_segment",
        "product_category",
        "research_goal",
        "status_label",
        "response_pattern_label",
        "tags",
        "claim_id",
        "category",
        "parameter",
        "statement",
        "evidence_id",
        "response_id",
        "turn",
        "source_quote",
        "sentiment",
        "confidence",
        "verification_status",
    ])

    matching_reports = _matching_reports(filters, context)

    read_model.record_audit(
        action="report.exported",
        actor=_actor(context),
        resource_type="report",
        resource_id=filters.get("session_id") or "all",
        tenant_id=_tenant(context),
        request_id=_request_id(context),
        metadata={
            "report_count": len(matching_reports),
            "filters": filters,
        },
    )

    for report in matching_reports:
        dimensions = report.get("dimensions") or {}

        for claim in report.get("claims", []):
            for evidence in claim.get("evidence", []):
                writer.writerow([
                    report.get("report_id"),
                    report.get("session_id"),
                    dimensions.get("period_start"),
                    dimensions.get("period_end"),
                    dimensions.get("customer_segment"),
                    dimensions.get("product_category"),
                    dimensions.get("research_goal"),
                    dimensions.get("status_label"),
                    dimensions.get("response_pattern_label"),
                    "|".join(dimensions.get("tags", [])),
                    claim.get("claim_id"),
                    claim.get("category"),
                    claim.get("parameter"),
                    claim.get("statement"),
                    evidence.get("evidence_id"),
                    evidence.get("response_id"),
                    evidence.get("turn"),
                    evidence.get("source_quote"),
                    evidence.get("sentiment"),
                    evidence.get("confidence"),
                    claim.get("verification_status"),
                ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                'attachment; filename="cvi-analysis-reports.csv"'
            )
        },
    )


@app.post("/researcher/analysis/reports/{session_id}/generate")
def generate_analysis_report(
    session_id: str,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """Build and store a grounded report from the latest stored state."""

    session = _require_session_access(context, session_id)

    try:
        report = analysis_report.build_session_report_from_memory(
            session_id,
            tenant_id=_tenant(context),
            session=session,
        )
        report = {
            **report,
            "tenant_id": _tenant(context),
            "created_by": _actor(context),
            "updated_by": _actor(context),
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    read_model.store_report(report)
    read_model.record_audit(
        action="report.generated",
        actor=_actor(context),
        resource_type="report",
        resource_id=report["report_id"],
        tenant_id=_tenant(context),
        request_id=_request_id(context),
        metadata={"session_id": session_id},
    )

    return report


@app.get("/researcher/analysis/reports/{report_id}")
def get_analysis_report(
    report_id: str,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """Return one full grounded report with its evidence ledger."""

    report = read_model.get_report(
        report_id,
        tenant_id=_tenant(context),
    )

    if report is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis report not found.",
        )

    if report.get("session_id"):
        _require_session_access(context, report["session_id"])

    return report


@app.get("/researcher/audit")
def list_audit_entries(
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    limit: int = 100,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    """Return tenant-scoped audit entries for researcher review."""

    entries = read_model.list_audit(
        tenant_id=_tenant(context),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        limit=limit,
    )

    if _context_or_default(context).role != "admin":
        visible_session_ids = _visible_session_ids(context)
        entries = [
            entry
            for entry in entries
            if entry.get("resource_id") in visible_session_ids
        ]

    return {"entries": entries}


@app.get("/researcher", include_in_schema=False)
def researcher_dashboard(
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    return FileResponse(
        Path(__file__).parent / "frontend" / "researcher.html"
    )


@app.get("/researcher.html", include_in_schema=False)
def researcher_dashboard_html(
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    return FileResponse(
        Path(__file__).parent / "frontend" / "researcher.html"
    )


@app.get("/researcher.js", include_in_schema=False)
def researcher_dashboard_script(
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    return FileResponse(
        Path(__file__).parent / "frontend" / "researcher.js"
    )


@app.get("/debug/session-state/{session_id}")
def debug_session_state(
    session_id: str,
    context: Annotated[ResearcherContext, Depends(require_researcher)] = None,
):
    _require_session_access(context, session_id)

    snapshot = graph.get_state(
        thread_config(session_id)
    )

    if not snapshot or not snapshot.values:
        raise HTTPException(
            status_code=404,
            detail="No checkpoint state found for this session."
        )

    state = snapshot.values

    unresolved_gaps = state.get("unresolved_gaps", [])

    
    knowledge_fields = [
        "product_relationship",
        "product_awareness",
        "purchase_intent",
        "overall_sentiment",
        "product_perception",
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
    ]

    knowledge_items = {
        field: state.get(field)
        for field in knowledge_fields
        if state.get(field)
    }

    return {
        "session_id": session_id,
        "checkpoint_found": True,

        # Conversation processing
        "questions_asked_count": len(state.get("questions_asked", [])),
        "responses_count": len(state.get("responses", [])),
        "latest_response_exists": bool(state.get("latest_response")),
        "latest_decomposition_exists": bool(state.get("latest_decomposition")),

        # Knowledge extraction
        "knowledge_items": knowledge_items,

        # State quality
        "information_sufficiency": state.get("information_sufficiency", {}),
        "confidence": state.get("confidence", {}),
        "verification_status": state.get("verification_status", {}),

        # Gap analysis
        "unresolved_gaps_count": len(unresolved_gaps),
        "unresolved_gaps": [
            str(gap) for gap in unresolved_gaps[:3]
        ],
    }


app.mount(
    "/",
    StaticFiles(
        directory=Path(__file__).parent / "frontend",
        html=True,
    ),
    name="frontend",
)




