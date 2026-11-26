import pytest

import api


@pytest.fixture(autouse=True)
def reset_abuse_limits(monkeypatch):
    """Keep offline tests deterministic; opt in per rate-limit test."""

    api.rate_limiter.reset()
    monkeypatch.setattr(api.rate_limiter, "enabled", False)
    api.active_voice_streams.clear()

    yield

    api.rate_limiter.reset()
    api.active_voice_streams.clear()
