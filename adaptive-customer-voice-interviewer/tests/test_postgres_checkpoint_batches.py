"""Offline regression tests for psycopg 3 batched checkpoint writes."""

from __future__ import annotations

import contextlib

from agent.persistence import postgres


class _FakeCursor:
    def __init__(self, batches: list[tuple[str, list[tuple]]]) -> None:
        self.batches = batches

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self.batches.append((sql, list(rows)))


class _FakeConnection:
    """Mimics psycopg 3: executemany lives on Cursor, not Connection."""

    def __init__(self) -> None:
        self.batches: list[tuple[str, list[tuple]]] = []
        self._cursor = _FakeCursor(self.batches)

    def cursor(self) -> _FakeCursor:
        return self._cursor


def _patch_connect(monkeypatch, connection: _FakeConnection) -> None:
    @contextlib.contextmanager
    def fake_connect():
        yield connection

    monkeypatch.setattr(postgres, "_connect", fake_connect)


def test_save_checkpoint_blobs_uses_cursor_executemany(monkeypatch):
    connection = _FakeConnection()
    _patch_connect(monkeypatch, connection)

    postgres.save_checkpoint_blobs(
        tenant_id="tenant-1",
        thread_id="thread-1",
        checkpoint_ns="",
        blobs=[("channel", "version", "json", b"{}")],
    )

    assert len(connection.batches) == 1
    sql, rows = connection.batches[0]
    assert "langgraph_checkpoint_blobs" in sql
    assert len(rows) == 1
    assert rows[0][0] == "tenant-1"


def test_save_checkpoint_writes_uses_cursor_executemany_for_both_sets(monkeypatch):
    connection = _FakeConnection()
    _patch_connect(monkeypatch, connection)

    postgres.save_checkpoint_writes(
        tenant_id="tenant-1",
        thread_id="thread-1",
        checkpoint_ns="",
        checkpoint_id="checkpoint-1",
        writes=[
            ("task-1", "path", "channel", 0, "json", b"a"),
            ("task-2", "path", "__root__", -1, "json", b"b"),
        ],
    )

    assert len(connection.batches) == 2
    assert all("langgraph_checkpoint_writes" in sql for sql, _ in connection.batches)
    assert [len(rows) for _, rows in connection.batches] == [1, 1]
