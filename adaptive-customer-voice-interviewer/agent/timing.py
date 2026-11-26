import time
from typing import Any, Optional

from agent import read_model


def now_ms() -> float:
    """Monotonic millisecond clock for latency measurement."""

    return time.perf_counter_ns() / 1_000_000.0


def record(
    session_id: str,
    turn: int,
    span_name: str,
    started_ms: float,
    *,
    ended_ms: Optional[float] = None,
    source: str = "runtime",
    parent_span_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """Persist one completed timing span."""

    end = now_ms() if ended_ms is None else float(ended_ms)
    start = float(started_ms)

    return read_model.record_timing(
        session_id=session_id,
        turn=int(turn),
        span_name=span_name,
        started_ms=start,
        ended_ms=end,
        duration_ms=max(0.0, end - start),
        source=source,
        parent_span_id=parent_span_id,
        metadata=metadata or {},
    )
