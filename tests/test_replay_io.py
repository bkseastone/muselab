"""Slow-disk regressions for live delivery, replay order and cancellation."""
import asyncio
import json
import threading

import pytest

from .test_chat_stream import stream_env as stream_env


@pytest.mark.asyncio
async def test_slow_write_keeps_loop_responsive_and_done_behind_record(stream_env, monkeypatch):
    broadcast = stream_env.TurnBroadcast("slow-write")
    reader = broadcast.subscribe()
    entered, release = threading.Event(), threading.Event()
    write = broadcast.events._write_blob

    def delayed(blob):
        entered.set()
        assert release.wait(3), "disk remained blocked while the event loop could not release it"
        write(blob)

    monkeypatch.setattr(broadcast.events, "_write_blob", delayed)
    try:
        broadcast.publish({"event": "tool_result", "data": '{"id":"first"}'})
        broadcast.publish({"event": "done", "data": "{}"})
        broadcast.finish()
        assert await asyncio.to_thread(entered.wait, 1)
        pending = asyncio.create_task(reader.get())
        await asyncio.sleep(0.02)
        assert not pending.done(), "terminal delivery must wait for preceding disk writes"
        release.set()
        first = await asyncio.wait_for(pending, 2)
        assert first["event"] == "tool_result"
        assert (await reader.get())["event"] == "done"
        assert await reader.get() is None
    finally:
        release.set()
        broadcast.close()
        await broadcast.events.flush_async()


@pytest.mark.asyncio
async def test_cancelled_slow_read_is_reused_without_losing_record(stream_env, monkeypatch):
    broadcast = stream_env.TurnBroadcast("slow-read")
    broadcast.publish({"event": "tool_result", "data": '{"id":"first"}'})
    broadcast.publish({"event": "done", "data": "{}"})
    broadcast.finish()
    await broadcast.events.flush_async()
    reader = broadcast.subscribe()
    entered, release = threading.Event(), threading.Event()
    read = reader._replay.readline

    def delayed():
        if not entered.is_set():
            entered.set()
            assert release.wait(3), "disk read blocked the event loop"
        return read()

    monkeypatch.setattr(reader._replay, "readline", delayed)
    try:
        pending = asyncio.create_task(reader.get())
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        release.set()
        first = await asyncio.wait_for(reader.get(), 2)
        assert json.loads(first["data"])["id"] == "first"
        assert (await reader.get())["event"] == "done"
        assert await reader.get() is None
    finally:
        release.set()
        broadcast.close()
        await broadcast.events.flush_async()


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", [0, 1])
async def test_disk_failure_resyncs_once_without_false_done(stream_env, monkeypatch, cursor):
    broadcast = stream_env.TurnBroadcast("disk-error")
    release = threading.Event()
    def broken(_blob):
        assert release.wait(3)
        raise OSError(28, "private disk failure detail")
    monkeypatch.setattr(broadcast.events, "_write_blob", broken)
    broadcast.publish({"event": "tool_result", "data": "{}"})
    broadcast.publish({"event": "done", "data": "{}"})
    broadcast.finish()
    reader = broadcast.subscribe(last_event_seq=cursor)
    release.set()
    try:
        first = await asyncio.wait_for(reader.get(), 2)
        assert first["event"] == "resync"
        assert json.loads(first["data"])["reason"] == "replay_storage_error"
        assert "private" not in first["data"]
        assert await reader.get() is None
    finally:
        broadcast.close()


def test_pending_write_budget_is_shared_and_rejects_overflow(monkeypatch):
    from backend import replay_io
    entered, release = threading.Event(), threading.Event()
    first, second = replay_io.ReplayIO(), replay_io.ReplayIO()
    monkeypatch.setattr(replay_io, "MAX_PENDING_BYTES", 100)
    def blocked():
        entered.set()
        assert release.wait(3)
    future = first.submit(blocked, size=80)
    try:
        assert entered.wait(1)
        with pytest.raises(replay_io.ReplayBacklogExceeded):
            second.submit(lambda: None, size=80)
    finally:
        release.set()
        future.result(timeout=2)


@pytest.mark.asyncio
async def test_close_during_pending_write_does_not_reuse_or_leak_descriptor(stream_env, monkeypatch):
    broadcast = stream_env.TurnBroadcast("pending-close")
    entered, release = threading.Event(), threading.Event()
    write = broadcast.events._write_blob
    def delayed(blob):
        entered.set()
        assert release.wait(3)
        write(blob)
    monkeypatch.setattr(broadcast.events, "_write_blob", delayed)
    path = broadcast.events.path
    try:
        broadcast.publish({"event": "done", "data": "{}"})
        assert await asyncio.to_thread(entered.wait, 1)
        broadcast.close()
        assert path.exists()
        release.set()
        await asyncio.wait_for(broadcast.events.flush_async(), 2)
        assert not path.exists()
    finally:
        release.set()
        broadcast.close()


@pytest.mark.asyncio
async def test_closed_cursor_reader_does_not_spin_on_initial_events(stream_env):
    broadcast = stream_env.TurnBroadcast("closed-cursor")
    broadcast.publish({"event": "tool_result", "data": "{}"})
    broadcast.publish({"event": "done", "data": "{}"})
    broadcast.finish()
    await broadcast.events.flush_async()
    reader = broadcast.subscribe(last_event_seq=1)
    reader.close_reader()
    assert await reader.get() is None
    broadcast.close()
    await broadcast.events.flush_async()


@pytest.mark.asyncio
async def test_reader_does_not_decode_an_in_progress_trailing_record(stream_env, monkeypatch):
    import os
    broadcast = stream_env.TurnBroadcast("partial-write-race")
    broadcast.publish({"event": "tool_result", "data": '{"id":"first"}'})
    reader = broadcast.subscribe()
    assert (await reader.get())["event"] == "tool_result"
    entered_read, release_read = threading.Event(), threading.Event()
    entered_write, release_write = threading.Event(), threading.Event()
    read = reader._replay.readline
    write = os.write

    def delayed_read():
        if not entered_read.is_set():
            entered_read.set()
            assert release_read.wait(3)
        return read()

    def partial_write(fd, data):
        if fd == broadcast.events._fd and not entered_write.is_set():
            count = write(fd, data[:5])
            entered_write.set()
            assert release_write.wait(3)
            return count
        return write(fd, data)

    monkeypatch.setattr(reader._replay, "readline", delayed_read)
    monkeypatch.setattr(os, "write", partial_write)
    pending = asyncio.create_task(reader.get())
    try:
        assert await asyncio.to_thread(entered_read.wait, 1)
        broadcast.publish({"event": "tool_result", "data": '{"id":"second"}'})
        assert await asyncio.to_thread(entered_write.wait, 1)
        release_read.set()
        await asyncio.sleep(0.02)
        assert not pending.done(), "partial trailing data must not cause a corruption resync"
        release_write.set()
        event = await asyncio.wait_for(pending, 2)
        assert event["event"] == "tool_result"
        assert json.loads(event["data"])["id"] == "second"
    finally:
        release_read.set()
        release_write.set()
        broadcast.close()
        await broadcast.events.flush_async()
