"""Local abuse bounds for participant inputs and streams.

These limits are deliberately dependency-free so hosted mode stays
self-contained. Production replicas enforce the same caps; operators can
raise or lower the request budgets through environment variables.
"""

import hashlib
import os
import re
import time
from collections import deque
from threading import Lock
from typing import Any, Mapping


# Participant payload bounds.
MAX_TRANSCRIPT_CHARS = 12_000
MAX_SPEECH_DURATION_MS = 900_000
MAX_STT_PROMPT_CHARS = 500
MAX_LANGUAGE_CHARS = 32
MAX_FILENAME_CHARS = 120
MAX_AUDIO_BASE64_CHARS = 8_000_000
MAX_AUDIO_BYTES = 6_000_000
MAX_HTTP_BODY_BYTES = MAX_AUDIO_BASE64_CHARS + 64_000
MAX_TTS_TEXT_CHARS = 2_000
MAX_EVENT_ID_CHARS = 128

# Timing/analytics metadata bounds.
MAX_SPAN_NAME_CHARS = 80
MAX_METADATA_KEYS = 50
MAX_METADATA_KEY_CHARS = 80
MAX_METADATA_VALUE_CHARS = 500
MAX_DELIVERY_KEYS = 30
MAX_DELIVERY_VALUE_CHARS = 500

# Voice websocket stream bounds.
WS_MAX_FRAME_BYTES = 256_000
WS_MAX_TOTAL_BYTES = 12_000_000
WS_MAX_DURATION_SECONDS = 300
WS_MAX_CONTROL_CHARS = 4_096
WS_MAX_CONCURRENT_PER_SESSION = 2

_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)

    if raw is None:
        return default

    try:
        value = int(raw)
    except ValueError:
        return default

    return value if value > 0 else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)

    if raw is None:
        return default

    return raw.strip().lower() in {"1", "true", "yes", "on"}


class RateLimiter:
    """Thread-safe fixed-window counter with independent named buckets."""

    def __init__(self) -> None:
        self.enabled = _env_bool("CVI_RATE_LIMIT_ENABLED", True)
        self.window_seconds = _env_int(
            "CVI_RATE_LIMIT_WINDOW_SECONDS",
            60,
        )
        self.session_limit = _env_int(
            "CVI_RATE_LIMIT_SESSION_PER_MINUTE",
            300,
        )
        self.ip_limit = _env_int(
            "CVI_RATE_LIMIT_IP_PER_MINUTE",
            1_000,
        )
        self.websocket_limit = _env_int(
            "CVI_RATE_LIMIT_WEBSOCKET_PER_MINUTE",
            30,
        )
        self.demo_limit = _env_int(
            "CVI_RATE_LIMIT_DEMO_PER_MINUTE",
            6,
        )
        self._events: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(
        self,
        bucket: str,
        key: str,
        limit: int | None = None,
    ) -> bool:
        """Record one event and return whether it is within budget."""

        if not self.enabled:
            return True

        budget = limit if limit is not None else self.session_limit
        now = time.monotonic()
        cutoff = now - self.window_seconds
        bucket_key = f"{bucket}:{key}"

        with self._lock:
            if len(self._events) >= 10_000:
                # Arbitrary session IDs arrive before authorization; purge
                # expired buckets so their empty deques cannot accumulate.
                for key in [
                    key
                    for key, existing in self._events.items()
                    if not existing
                ]:
                    self._events.pop(key, None)

            events = self._events.setdefault(bucket_key, deque())

            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= budget:
                return False

            events.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


def client_ip_key(host: str | None) -> str:
    """Hash a client IP so rate-limit state does not retain raw addresses."""

    value = host or "unknown"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


class VoiceStreamBudget:
    """Track size and duration limits for one microphone stream."""

    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.total_bytes = 0

    def check_duration(self) -> bool:
        return time.monotonic() - self.started_at > WS_MAX_DURATION_SECONDS

    def check_frame(self, size: int) -> str | None:
        if self.check_duration():
            return "duration"

        if size > WS_MAX_FRAME_BYTES:
            return "frame"

        self.total_bytes += size

        if self.total_bytes > WS_MAX_TOTAL_BYTES:
            return "total"

        return None

    def check_control(self, text: str) -> str | None:
        if self.check_duration():
            return "duration"

        if len(text) > WS_MAX_CONTROL_CHARS:
            return "control"

        return None


def is_safe_filename(name: str) -> bool:
    return (
        0 < len(name) <= MAX_FILENAME_CHARS
        and name not in {".", ".."}
        and bool(_FILENAME_RE.fullmatch(name))
    )


def validate_bounded_mapping(
    value: Mapping[str, Any] | None,
    *,
    field_name: str,
    max_keys: int,
    max_key_chars: int,
    max_value_chars: int,
) -> dict[str, Any]:
    """Return a shallow copy of a bounded string-keyed metadata mapping."""

    if value is None:
        return {}

    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object.")

    if len(value) > max_keys:
        raise ValueError(
            f"{field_name} cannot contain more than {max_keys} entries."
        )

    result: dict[str, Any] = {}

    for key, item in value.items():
        key_text = str(key)

        if not key_text or len(key_text) > max_key_chars:
            raise ValueError(
                f"{field_name} keys must be 1-{max_key_chars} characters."
            )

        if isinstance(item, str):
            if len(item) > max_value_chars:
                raise ValueError(
                    f"{field_name} values cannot exceed "
                    f"{max_value_chars} characters."
                )
        elif isinstance(item, (int, float, bool)) or item is None:
            pass
        elif isinstance(item, (list, dict)):
            rendered = repr(item)

            if len(rendered) > max_value_chars:
                raise ValueError(
                    f"{field_name} values cannot exceed "
                    f"{max_value_chars} characters."
                )
        else:
            raise ValueError(f"{field_name} contains an unsupported value.")

        result[key_text] = item

    return result
