"""Participant invitation capability helpers.

The session UUID is a public identifier. New sessions also carry a separate
high-entropy capability that is delivered only in the participant URL. The
raw token is never stored; only a SHA-256 digest is persisted with the session.

Legacy sessions issued before this capability existed remain usable so an
in-progress interview is not broken. Once ``participant_token_hash`` exists,
every participant endpoint must present the matching token.
"""

import hashlib
from secrets import compare_digest, token_urlsafe


PARTICIPANT_TOKEN_HEADER = "x-cvi-participant-token"
PARTICIPANT_TOKEN_QUERY_PARAM = "participant_token"
PARTICIPANT_TOKEN_LENGTH_BYTES = 32


def new_participant_token() -> str:
    """Create a URL-safe participant capability."""

    return token_urlsafe(PARTICIPANT_TOKEN_LENGTH_BYTES)


def hash_participant_token(token: str) -> str:
    """Hash a raw capability for durable storage."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def participant_token_required(session: dict) -> bool:
    """Return True when a session has an issued participant capability."""

    return bool(session.get("participant_token_hash"))


def participant_token_is_valid(
    session: dict,
    token: str | None,
) -> bool:
    """Validate a presented token against the stored digest."""

    expected = session.get("participant_token_hash")

    # Pre-capability sessions keep their original UUID-link behavior.
    if not expected:
        return True

    if not token:
        return False

    return compare_digest(
        hash_participant_token(token),
        str(expected),
    )
