from datetime import date, timedelta

from agent import read_model
from agent.session import create_research_session
from agent.session_validator import validate_research_session


def _session(start_date, end_date=None, status="active"):
    return {
        "status": status,
        "start_date": start_date,
        "end_date": end_date,
    }


def test_validate_accepts_iso_date_strings_from_persisted_state():
    result = validate_research_session(
        {
            "research_session": _session(
                (date.today() - timedelta(days=1)).isoformat(),
                (date.today() + timedelta(days=1)).isoformat(),
            )
        }
    )

    assert result == {"interview_complete": False, "next_action": None}


def test_validate_respects_future_iso_start_date():
    result = validate_research_session(
        {
            "research_session": _session(
                (date.today() + timedelta(days=1)).isoformat()
            )
        }
    )

    assert result == {
        "interview_complete": True,
        "next_action": "stop",
        "termination_reason": "research_session_not_started",
    }


def test_get_session_restores_date_fields(tmp_path):
    read_model.configure(tmp_path / "session-validator.sqlite3")

    session = create_research_session(
        company_id="acme",
        company_name="Acme Technologies",
        product_id="insight-kit",
        product_name="Insight Kit",
        product_category="Analytics",
        research_goal="Understand setup friction.",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=7),
    )
    read_model.upsert_session(session)

    hydrated = read_model.get_session(session["session_id"])

    assert hydrated["start_date"] == date.today()
    assert isinstance(hydrated["start_date"], date)
    assert hydrated["end_date"] == date.today() + timedelta(days=7)
    assert isinstance(hydrated["end_date"], date)

    read_model.configure()
