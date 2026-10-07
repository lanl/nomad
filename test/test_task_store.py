from __future__ import annotations

import time

import pytest

from nomad import task_store as store_module
from nomad.task_store import (
    MIN_TASK_CHARGE_BYTES,
    SQLiteTaskStore,
    TaskCapacityError,
)


@pytest.mark.asyncio()
async def test_sqlite_store_persists_input_and_terminal_json(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    input_json = b'{"method":"tools/call","params":{"arguments":{"x":1}}}'
    terminal_json = b'{"resultType":"complete","status":"completed"}'
    owner_key = b"owner"
    store = SQLiteTaskStore(path, ttl_seconds=60, max_bytes=4096)

    record, evicted = await store.create(
        task_id="task",
        owner_key=owner_key,
        method="tools/call",
        input_json=input_json,
        poll_interval_ms=20,
    )
    changed, terminal_evicted = await store.set_terminal(
        record.task_id,
        status="completed",
        updated_at="2026-10-06T00:00:01+00:00",
        terminal_json=terminal_json,
    )
    await store.close()

    assert evicted == ()
    assert changed is True
    assert terminal_evicted == ()

    reopened = SQLiteTaskStore(path, ttl_seconds=60, max_bytes=4096)
    persisted = await reopened.get("task", owner_key)
    assert persisted is not None
    assert persisted.input_json == input_json
    assert persisted.input_bytes == len(input_json)
    assert persisted.terminal_json == terminal_json
    assert persisted.terminal_bytes == len(terminal_json)
    assert persisted.status == "completed"
    await reopened.close()


@pytest.mark.asyncio()
async def test_byte_budget_evicts_oldest_terminal_task():
    input_json = b"{}"
    charge = len(input_json) + MIN_TASK_CHARGE_BYTES
    store = SQLiteTaskStore(
        ":memory:",
        ttl_seconds=60,
        max_bytes=2 * charge,
    )

    first, _ = await store.create(
        task_id="first",
        owner_key=b"owner",
        method="tools/call",
        input_json=input_json,
        poll_interval_ms=20,
    )
    await store.set_terminal(
        first.task_id,
        status="completed",
        updated_at="2026-10-06T00:00:01+00:00",
        terminal_json=b"{}",
    )
    await store.create(
        task_id="second",
        owner_key=b"owner",
        method="tools/call",
        input_json=input_json,
        poll_interval_ms=20,
    )

    _, evicted = await store.create(
        task_id="third",
        owner_key=b"owner",
        method="tools/call",
        input_json=input_json,
        poll_interval_ms=20,
    )

    assert evicted == ("first",)
    assert await store.get_any("first") is None
    assert store.active_count == 2
    assert store.retained_bytes == 2 * charge
    await store.close()


@pytest.mark.asyncio()
async def test_terminal_reservation_allows_compact_failure_after_large_result():
    input_json = b"{}"
    store = SQLiteTaskStore(
        ":memory:",
        ttl_seconds=60,
        max_bytes=len(input_json) + MIN_TASK_CHARGE_BYTES,
    )
    record, _ = await store.create(
        task_id="large",
        owner_key=b"owner",
        method="tools/call",
        input_json=input_json,
        poll_interval_ms=20,
    )

    with pytest.raises(TaskCapacityError):
        await store.set_terminal(
            record.task_id,
            status="completed",
            updated_at="2026-10-06T00:00:01+00:00",
            terminal_json=b"x" * 4096,
        )

    changed, _ = await store.set_terminal(
        record.task_id,
        status="failed",
        updated_at="2026-10-06T00:00:02+00:00",
        terminal_json=b'{"status":"failed"}',
    )
    failed = await store.get_any(record.task_id)
    assert changed is True
    assert failed is not None
    assert failed.status == "failed"
    assert store.retained_bytes == len(input_json) + MIN_TASK_CHARGE_BYTES
    await store.close()


@pytest.mark.asyncio()
async def test_store_prunes_expired_rows():
    store = SQLiteTaskStore(":memory:", ttl_seconds=60, max_bytes=4096)
    await store.create(
        task_id="expired",
        owner_key=b"owner",
        method="tools/call",
        input_json=b"{}",
        poll_interval_ms=20,
    )

    removed = await store.prune_expired(now=time.time() + 61)

    assert removed == ("expired",)
    assert store.active_count == 0
    assert store.retained_bytes == 0
    await store.close()


@pytest.mark.asyncio()
async def test_store_records_lifecycle_metrics(monkeypatch):
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        store_module.nomad_metrics,
        "record_task_created",
        lambda: events.append(("created", None)),
    )
    monkeypatch.setattr(
        store_module.nomad_metrics,
        "record_task_rejection",
        lambda reason: events.append(("rejection", reason)),
    )
    monkeypatch.setattr(
        store_module.nomad_metrics,
        "record_task_removal",
        lambda reason: events.append(("removal", reason)),
    )
    store = SQLiteTaskStore(":memory:", ttl_seconds=60, max_bytes=1024)

    with pytest.raises(TaskCapacityError):
        await store.create(
            task_id="too-large",
            owner_key=b"owner",
            method="tools/call",
            input_json=b"x",
            poll_interval_ms=20,
        )

    record, _ = await store.create(
        task_id="task",
        owner_key=b"owner",
        method="tools/call",
        input_json=b"",
        poll_interval_ms=20,
    )
    await store.delete(record.task_id, reason="delivered")

    assert ("rejection", "capacity") in events
    assert ("created", None) in events
    assert ("removal", "delivered") in events
    await store.close()
