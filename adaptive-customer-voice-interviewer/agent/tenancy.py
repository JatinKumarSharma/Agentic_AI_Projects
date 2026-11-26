"""Deployment-scoped tenant identity.

CVI is deployed once per research team. Every deployment has a stable tenant
ID; all durable records are stamped with it and every read is filtered by it.
Separate deployments (and separate databases) therefore cannot see each
other's sessions, snapshots, timings, or reports.
"""

import os
import re

from .observability import get_request_id


DEFAULT_TENANT_ID = "default"
TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def normalize_tenant_id(value=None) -> str:
    """Validate a tenant ID or fall back to the configured/default tenant."""

    if value is None or str(value).strip() == "":
        value = os.getenv("CVI_TENANT_ID", DEFAULT_TENANT_ID)

    candidate = str(value).strip()

    if not TENANT_ID_PATTERN.fullmatch(candidate):
        raise ValueError("Invalid tenant ID.")

    return candidate


def current_tenant_id() -> str:
    return normalize_tenant_id(None)


def audit_request_id() -> str:
    return get_request_id() or ""
