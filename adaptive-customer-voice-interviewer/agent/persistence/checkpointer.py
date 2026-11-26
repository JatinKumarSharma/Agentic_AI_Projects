"""Durable LangGraph checkpointer backed by research-memory storage.

This keeps the interview graph unchanged while replacing the in-memory
checkpointer with tenant-scoped, restart-safe checkpoint rows. Checkpoint blobs
are serialized through LangGraph's serializer so internal channel values remain
provider-independent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import WRITES_IDX_MAP

from agent.persistence import get_repository
from agent.tenancy import current_tenant_id, normalize_tenant_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RepositoryCheckpointer(BaseCheckpointSaver):
    """Tenant-scoped LangGraph checkpointer using the repository boundary."""

    def __init__(self, repository: Any = None, *, serde: Any = None) -> None:
        super().__init__(serde=serde)
        self.repository = repository or get_repository()

    def _parts(self, config: Any) -> tuple[str, str, str]:
        configurable = (config or {}).get("configurable") or {}
        thread_id = configurable.get("thread_id")

        if not thread_id:
            raise ValueError("A thread_id is required for checkpoint access.")

        checkpoint_ns = configurable.get("checkpoint_ns") or ""
        tenant_id = configurable.get("tenant_id") or self.repository.tenant_for_session(
            thread_id
        )

        return normalize_tenant_id(tenant_id), thread_id, checkpoint_ns

    def _make_config(
        self,
        tenant_id: str,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> dict:
        return {
            "configurable": {
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def _deserialize(self, type_name: str, blob: bytes) -> Any:
        return self.serde.loads_typed((type_name, bytes(blob)))

    def _load_channel_values(
        self,
        *,
        tenant_id: str,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint: dict,
    ) -> None:
        """Rebuild channel values from versioned blobs, like LangGraph does."""

        versions = checkpoint.get("channel_versions") or {}
        keys = [(channel, str(version)) for channel, version in versions.items()]
        rows = self.repository.get_checkpoint_blobs(
            tenant_id=tenant_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            keys=keys,
        )
        blobs = {
            (row["channel"], str(row["version"])): (
                row["value_type"],
                row["value_blob"],
            )
            for row in rows
        }

        channel_values = {}

        for channel, version in versions.items():
            serialized = blobs.get((channel, str(version)))

            if serialized is None or serialized[0] == "empty":
                continue

            channel_values[channel] = self._deserialize(*serialized)

        checkpoint["channel_values"] = channel_values

    def _to_tuple(self, row: dict) -> CheckpointTuple:
        tenant_id = normalize_tenant_id(row["tenant_id"])
        thread_id = row["thread_id"]
        checkpoint_ns = row["checkpoint_ns"]
        checkpoint_id = row["checkpoint_id"]

        parent_config = None
        if row["parent_checkpoint_id"]:
            parent_config = self._make_config(
                tenant_id,
                thread_id,
                checkpoint_ns,
                row["parent_checkpoint_id"],
            )

        writes = self.repository.get_checkpoint_writes(
            tenant_id=tenant_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
        )
        pending_writes = [
            (
                write["task_id"],
                write["channel"],
                self._deserialize(write["value_type"], write["value_blob"]),
            )
            for write in writes
        ]

        checkpoint = self._deserialize(
            row["checkpoint_type"],
            row["checkpoint_blob"],
        )
        self._load_channel_values(
            tenant_id=tenant_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint=checkpoint,
        )
        metadata = self._deserialize(
            row["metadata_type"],
            row["metadata_blob"],
        )

        return CheckpointTuple(
            config=self._make_config(
                tenant_id,
                thread_id,
                checkpoint_ns,
                checkpoint_id,
            ),
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def get_tuple(self, config: Any) -> CheckpointTuple | None:
        tenant_id, thread_id, checkpoint_ns = self._parts(config)
        row = self.repository.get_checkpoint(
            tenant_id=tenant_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=get_checkpoint_id(config),
        )

        return self._to_tuple(row) if row else None

    def list(
        self,
        config: Any,
        *,
        filter: dict[str, Any] | None = None,
        before: Any = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        tenant_id, thread_id, checkpoint_ns = self._parts(config)
        before_checkpoint_id = get_checkpoint_id(before) if before else None

        rows = self.repository.list_checkpoints(
            tenant_id=tenant_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            before_checkpoint_id=before_checkpoint_id,
            limit=limit,
        )

        for row in rows:
            result = self._to_tuple(row)

            if filter and any(
                result.metadata.get(key) != value
                for key, value in filter.items()
            ):
                continue

            yield result

    def put(
        self,
        config: Any,
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> dict:
        tenant_id, thread_id, checkpoint_ns = self._parts(config)
        stored_checkpoint = dict(checkpoint)
        channel_values = stored_checkpoint.pop("channel_values", {})
        new_versions = new_versions or {}

        blobs = []

        for channel, version in new_versions.items():
            if channel in channel_values:
                value_type, value_blob = self.serde.dumps_typed(
                    channel_values[channel]
                )
            else:
                value_type, value_blob = "empty", b""

            blobs.append((channel, str(version), value_type, value_blob))

        self.repository.save_checkpoint_blobs(
            tenant_id=tenant_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            blobs=blobs,
        )

        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(
            stored_checkpoint
        )
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        parent_checkpoint_id = get_checkpoint_id(config)

        self.repository.save_checkpoint(
            tenant_id=tenant_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint["id"],
            parent_checkpoint_id=parent_checkpoint_id,
            checkpoint_type=checkpoint_type,
            checkpoint_blob=checkpoint_blob,
            metadata_type=metadata_type,
            metadata_blob=metadata_blob,
            created_at=checkpoint.get("ts") or _utc_now(),
        )

        return self._make_config(
            tenant_id,
            thread_id,
            checkpoint_ns,
            checkpoint["id"],
        )

    def put_writes(
        self,
        config: Any,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        tenant_id, thread_id, checkpoint_ns = self._parts(config)
        checkpoint_id = get_checkpoint_id(config)

        if not checkpoint_id:
            raise ValueError("A checkpoint_id is required to store writes.")

        serialized = []

        for index, (channel, value) in enumerate(writes):
            value_type, value_blob = self.serde.dumps_typed(value)
            serialized.append((
                task_id,
                task_path,
                channel,
                WRITES_IDX_MAP.get(channel, index),
                value_type,
                value_blob,
            ))

        self.repository.save_checkpoint_writes(
            tenant_id=tenant_id,
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            writes=serialized,
        )

    def delete_thread(self, thread_id: str) -> None:
        self.repository.delete_checkpoint_thread(
            tenant_id=current_tenant_id(),
            thread_id=thread_id,
        )

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        """Keep run checkpoints for audit instead of silently deleting them."""

        return None


__all__ = ["RepositoryCheckpointer"]
