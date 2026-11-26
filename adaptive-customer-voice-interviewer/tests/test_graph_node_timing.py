from agent import read_model
from agent.graph import timed_node


def test_timed_node_records_first_question_subspan(tmp_path):
    read_model.configure(tmp_path / "graph-timing.sqlite3")

    def fake_meta_planner(state):
        return {"meta_plan": {}}

    state = {
        "iteration_count": 0,
        "research_session": {"session_id": "timing-session"},
        "questions_asked": [],
    }

    result = timed_node("meta_planner", fake_meta_planner)(state)

    spans = read_model.list_timings("timing-session")

    assert result == {"meta_plan": {}}
    assert [span["span_name"] for span in spans] == [
        "first_question_meta_planner"
    ]
    assert spans[0]["source"] == "runtime"
    assert spans[0]["turn"] == 1

    read_model.configure()
