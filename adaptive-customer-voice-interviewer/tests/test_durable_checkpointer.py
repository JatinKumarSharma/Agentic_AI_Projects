from typing import TypedDict

import pytest
from langgraph.checkpoint.base import CheckpointMetadata
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent import read_model
from agent.persistence.checkpointer import RepositoryCheckpointer


TENANT_A = "checkpoint-tenant-a"
TENANT_B = "checkpoint-tenant-b"


@pytest.fixture(autouse=True)
def checkpoint_db(tmp_path, monkeypatch):
    read_model.configure(tmp_path / "checkpointer.sqlite3")
    monkeypatch.setenv("CVI_TENANT_ID", TENANT_A)

    yield

    read_model.configure()


class _State(TypedDict):
    value: str


class _MultiStepState(TypedDict):
    first: str
    second: str


def _build_interrupt_graph(checkpointer):
    def wait_for_runtime(state: _State):
        return {"value": interrupt({"prompt": "continue"})}

    builder = StateGraph(_State)
    builder.add_node("wait", wait_for_runtime)
    builder.add_edge(START, "wait")
    builder.add_edge("wait", END)

    return builder.compile(checkpointer=checkpointer)


def _build_multi_step_interrupt_graph(checkpointer):
    def set_first(state: _MultiStepState):
        return {"first": "one"}

    def wait_for_runtime(state: _MultiStepState):
        return {"second": interrupt({"prompt": "continue"})}

    builder = StateGraph(_MultiStepState)
    builder.add_node("first", set_first)
    builder.add_node("second", wait_for_runtime)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)

    return builder.compile(checkpointer=checkpointer)


def _config(thread_id, tenant_id=TENANT_A):
    return {
        "configurable": {
            "thread_id": thread_id,
            "tenant_id": tenant_id,
        }
    }


def test_checkpoint_and_writes_survive_a_new_saver_instance():
    saver = RepositoryCheckpointer()
    config = _config("direct-thread")

    saved_config = saver.put(
        config,
        {
            "v": 1,
            "id": "checkpoint-1",
            "ts": "2026-01-12T10:00:00.000+00:00",
            "channel_values": {"value": "before"},
            "channel_versions": {"value": 1},
            "versions_seen": {},
            "updated_channels": ["value"],
        },
        CheckpointMetadata(
            source="input",
            step=0,
            parents={},
            run_id="run-1",
        ),
        {"value": 1},
    )
    saver.put_writes(
        saved_config,
        [("value", "intermediate")],
        "task-1",
    )

    restarted = RepositoryCheckpointer()
    loaded = restarted.get_tuple(saved_config)

    assert loaded is not None
    assert loaded.checkpoint["channel_values"]["value"] == "before"
    assert loaded.pending_writes == [("task-1", "value", "intermediate")]


def test_delete_thread_removes_checkpoints_blobs_and_writes():
    saver = RepositoryCheckpointer()
    config = _config("delete-thread")

    saved_config = saver.put(
        config,
        {
            "v": 1,
            "id": "checkpoint-1",
            "ts": "2026-01-12T10:00:00.000+00:00",
            "channel_values": {"value": "before"},
            "channel_versions": {"value": 1},
            "versions_seen": {},
            "updated_channels": ["value"],
        },
        CheckpointMetadata(
            source="input",
            step=0,
            parents={},
            run_id="run-1",
        ),
        {"value": 1},
    )
    saver.put_writes(saved_config, [("value", "intermediate")], "task-1")

    saver.delete_thread("delete-thread")

    assert saver.get_tuple(saved_config) is None
    assert read_model.get_checkpoint_blobs(
        tenant_id=TENANT_A,
        thread_id="delete-thread",
        checkpoint_ns="",
        keys=[("value", "1")],
    ) == []


def test_graph_interrupt_can_be_resumed_after_restart():
    first_graph = _build_interrupt_graph(RepositoryCheckpointer())
    config = _config("resume-thread")

    paused = first_graph.invoke({"value": "before"}, config=config)

    assert paused["__interrupt__"]

    restarted_graph = _build_interrupt_graph(RepositoryCheckpointer())
    resumed = restarted_graph.invoke(
        Command(resume="after"),
        config=config,
    )

    assert resumed["value"] == "after"


def test_earlier_node_state_survives_restart():
    first_graph = _build_multi_step_interrupt_graph(RepositoryCheckpointer())
    config = _config("multi-step-thread")

    paused = first_graph.invoke({"first": "", "second": ""}, config=config)

    assert paused["__interrupt__"]
    assert paused["first"] == "one"

    restarted_graph = _build_multi_step_interrupt_graph(
        RepositoryCheckpointer()
    )
    resumed = restarted_graph.invoke(
        Command(resume="two"),
        config=config,
    )

    assert resumed["first"] == "one"
    assert resumed["second"] == "two"


def test_checkpoints_are_tenant_scoped():
    saver = RepositoryCheckpointer()
    config = _config("tenant-thread", TENANT_A)

    saver.put(
        config,
        {
            "v": 1,
            "id": "checkpoint-1",
            "ts": "2026-01-12T10:00:00.000+00:00",
            "channel_values": {"value": "tenant-a"},
            "channel_versions": {"value": 1},
            "versions_seen": {},
            "updated_channels": ["value"],
        },
        CheckpointMetadata(
            source="input",
            step=0,
            parents={},
            run_id="run-1",
        ),
        {"value": 1},
    )

    assert RepositoryCheckpointer().get_tuple(config) is not None
    assert RepositoryCheckpointer().get_tuple(
        _config("tenant-thread", TENANT_B)
    ) is None
