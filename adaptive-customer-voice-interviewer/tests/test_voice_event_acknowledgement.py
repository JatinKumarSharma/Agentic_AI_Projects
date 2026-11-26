from fastapi.testclient import TestClient

import api


SESSION_ID = "voice-event-ack-session"


def setup_function():
    api.sessions[SESSION_ID] = {"session_id": SESSION_ID, "status": "active"}
    api.voice_output_queues.clear()
    api.voice_output_events.clear()
    api.pending_engagement_tasks.clear()


def teardown_function():
    api.sessions.pop(SESSION_ID, None)
    api.voice_output_queues.clear()
    api.voice_output_events.clear()
    api.pending_engagement_tasks.clear()


def test_voice_events_remain_visible_until_acknowledged():
    api.voice_output_queues[SESSION_ID] = api.Queue()
    api.voice_output_queues[SESSION_ID].put(
        {
            "event_type": "contextual_engagement",
            "text": "That is helpful to hear.",
        }
    )

    first = api.get_voice_events(SESSION_ID)
    assert len(first["events"]) == 1

    event_id = first["events"][0]["event_id"]

    second = api.get_voice_events(SESSION_ID)
    assert [event["event_id"] for event in second["events"]] == [event_id]

    client = TestClient(api.app)
    ack = client.post(
        f"/sessions/{SESSION_ID}/voice-events/ack",
        json={"event_ids": [event_id]},
    )

    assert ack.status_code == 200
    assert ack.json()["acknowledged"] == 1

    third = api.get_voice_events(SESSION_ID)
    assert third["events"] == []


def test_voice_event_ack_is_session_scoped():
    other_session = "other-voice-event-ack-session"

    api.sessions[other_session] = {
        "session_id": other_session,
        "status": "active",
    }
    api.voice_output_events[SESSION_ID] = [
        {"event_id": "event-a", "event": {"event_type": "one"}}
    ]
    api.voice_output_events[other_session] = [
        {"event_id": "event-b", "event": {"event_type": "two"}}
    ]

    client = TestClient(api.app)
    response = client.post(
        f"/sessions/{SESSION_ID}/voice-events/ack",
        json={"event_ids": ["event-b"]},
    )

    assert response.status_code == 200
    assert response.json()["acknowledged"] == 0

    remaining = api.get_voice_events(SESSION_ID)
    assert [event["event_id"] for event in remaining["events"]] == ["event-a"]

    api.sessions.pop(other_session, None)
