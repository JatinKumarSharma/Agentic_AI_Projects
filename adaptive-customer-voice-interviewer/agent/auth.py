"""Researcher authentication and request-scoped authorization context.

The documented HTTP Basic contract (user ``researcher``, password from
``RESEARCHER_ACCESS_CODE``) remains available for compatibility. Named accounts
can be supplied through ``CVI_RESEARCHER_ACCOUNTS`` so simultaneous researchers
have distinct actors, roles, and optional tenant assignments.
"""

import base64
import json
import os
from dataclasses import dataclass
from secrets import compare_digest

from fastapi import HTTPException, Request

from .observability import get_request_id
from .tenancy import current_tenant_id, normalize_tenant_id


RESEARCHER_USERNAME = "researcher"
RESEARCHER_AUTH_REALM = "CVI Researcher"
RESEARCHER_ACCOUNTS_ENV = "CVI_RESEARCHER_ACCOUNTS"
LOCAL_UNAUTHENTICATED_ENV = "CVI_ALLOW_UNAUTHENTICATED_LOCAL"

WWW_AUTHENTICATE = (
    f'Basic realm="{RESEARCHER_AUTH_REALM}", charset="UTF-8"'
)


@dataclass(frozen=True)
class ResearcherContext:
    username: str
    tenant_id: str
    request_id: str = ""
    role: str = "researcher"


def basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    scheme, _, encoded = header.partition(" ")

    if scheme.lower() != "basic" or not encoded:
        return None

    try:
        decoded = base64.b64decode(
            encoded,
            validate=True,
        ).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return None

    return username, password


def _configured_accounts() -> dict[str, dict]:
    """Parse optional named researcher accounts from the environment."""

    raw = os.getenv(RESEARCHER_ACCOUNTS_ENV)
    if not raw or not raw.strip():
        return {}

    try:
        configured = json.loads(raw)
    except (TypeError, ValueError):
        return {}

    if not isinstance(configured, dict):
        return {}

    accounts: dict[str, dict] = {}

    for username, spec in configured.items():
        if not isinstance(username, str) or not username:
            continue

        if isinstance(spec, str):
            password = spec
            role = "researcher"
            tenant_id = None
        elif isinstance(spec, dict):
            password = str(spec.get("password") or "")
            role = str(spec.get("role") or "researcher").strip().lower()
            tenant_id = spec.get("tenant_id")
        else:
            continue

        if not password:
            continue

        accounts[username] = {
            "password": password,
            "role": "admin" if role == "admin" else "researcher",
            "tenant_id": (
                normalize_tenant_id(tenant_id)
                if tenant_id
                else None
            ),
        }

    return accounts


def researcher_auth_configured() -> bool:
    return bool(
        os.getenv("RESEARCHER_ACCESS_CODE")
        or _configured_accounts()
    )


def local_unauthenticated_allowed() -> bool:
    """Only an explicit opt-in may bypass researcher auth on local sessions."""

    return os.getenv(LOCAL_UNAUTHENTICATED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def authenticate_researcher(request: Request) -> ResearcherContext | None:
    """Return an authenticated context, or None when access is denied."""

    credentials = basic_credentials(request)

    if credentials is None:
        return None

    username, password = credentials
    accounts = _configured_accounts()
    account = accounts.get(username)

    if account and compare_digest(
        password.encode("utf-8"),
        account["password"].encode("utf-8"),
    ):
        tenant_id = account["tenant_id"] or current_tenant_id()

        return ResearcherContext(
            username=username,
            tenant_id=tenant_id,
            request_id=get_request_id() or "",
            role=account["role"],
        )

    access_code = os.getenv("RESEARCHER_ACCESS_CODE")

    if (
        access_code
        and username == RESEARCHER_USERNAME
        and compare_digest(
            password.encode("utf-8"),
            access_code.encode("utf-8"),
        )
    ):
        return ResearcherContext(
            username=username,
            tenant_id=current_tenant_id(),
            request_id=get_request_id() or "",
            role="admin",
        )

    return None


def deny_unconfigured() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Researcher access is not configured.",
    )


def deny_unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Researcher access required.",
        headers={"WWW-Authenticate": WWW_AUTHENTICATE},
    )


async def require_researcher(request: Request) -> ResearcherContext:
    """FastAPI dependency for researcher-only endpoints."""

    if not researcher_auth_configured():
        raise deny_unconfigured()

    context = authenticate_researcher(request)

    if context is None:
        raise deny_unauthorized()

    return context


async def require_researcher_if_configured(request: Request):
    """Protect session creation unless local unauthenticated mode is explicit."""

    if not researcher_auth_configured():
        if local_unauthenticated_allowed():
            return None

        raise deny_unconfigured()

    return await require_researcher(request)
