from datetime import datetime, timedelta, timezone

import pytest

from agent.session import is_link_expired, resolve_link_expiry


NOW = datetime(2026, 11, 21, 10, 0, 0, tzinfo=timezone.utc)


def test_no_expiry_by_default():
    assert resolve_link_expiry(None, now=NOW) == (None, None)
    assert resolve_link_expiry("", now=NOW) == (None, None)
    assert resolve_link_expiry("none", now=NOW) == (None, None)


@pytest.mark.parametrize(
    "value, expected_label, expected_delta",
    [
        ("1h", "1h", timedelta(hours=1)),
        ("3h", "3h", timedelta(hours=3)),
        ("1d", "1d", timedelta(days=1)),
        ("1 hour", "1 hour", timedelta(hours=1)),
        ("3 hours", "3 hours", timedelta(hours=3)),
        ("1 day", "1 day", timedelta(days=1)),
    ],
)
def test_preset_expiries_resolve_from_now(value, expected_label, expected_delta):
    label, expires_at = resolve_link_expiry(value, now=NOW)

    assert label == expected_label
    assert expires_at == (NOW + expected_delta).isoformat(timespec="milliseconds")


def test_custom_timezone_is_converted_to_utc():
    label, expires_at = resolve_link_expiry(
        "2026-11-22T15:00:00+05:30",
        now=NOW,
    )

    assert label == "custom"
    assert expires_at == "2026-11-22T09:30:00.000+00:00"


def test_custom_utc_z_suffix_is_accepted():
    _, expires_at = resolve_link_expiry(
        "2026-11-22T09:30:00Z",
        now=NOW,
    )

    assert expires_at == "2026-11-22T09:30:00.000+00:00"


def test_custom_datetime_requires_a_timezone():
    with pytest.raises(ValueError, match="timezone"):
        resolve_link_expiry("2026-11-22T15:00:00", now=NOW)


def test_unknown_expiry_value_is_rejected():
    with pytest.raises(ValueError, match="link_expiry"):
        resolve_link_expiry("sometime", now=NOW)


def test_expiry_boundary_is_enforced():
    session = {"link_expires_at": "2026-11-21T11:00:00.000+00:00"}

    assert is_link_expired(session, now=NOW) is False
    assert is_link_expired(session, now=NOW + timedelta(hours=1)) is True
    assert is_link_expired(session, now=NOW + timedelta(minutes=59)) is False


def test_started_interview_is_never_blocked_by_expiry():
    session = {
        "link_expires_at": "2026-11-21T11:00:00.000+00:00",
        "interview_started_at": "2026-11-21T10:05:00.000+00:00",
    }

    assert is_link_expired(session, now=NOW + timedelta(days=1)) is False


def test_sessions_without_expiry_are_never_expired():
    assert is_link_expired({}, now=NOW) is False
    assert is_link_expired({"link_expires_at": None}, now=NOW) is False
