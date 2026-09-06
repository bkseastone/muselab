import asyncio
from types import SimpleNamespace

import pytest

from backend import chat_runtime as runtime, runtime_buffer as buffers


def test_queue_budget_preserves_messages_and_eof_and_exposes_numbers_only():
    eof = object()
    queue = buffers.RuntimeMessageQueue(lane='test', eof=eof, max_events=2, max_bytes=2048)
    first, second = {'content': 'synthetic-one'}, {'content': 'synthetic-two'}
    queue.put_nowait(first)
    queue.put_nowait(second)
    with pytest.raises(buffers.RuntimeBufferExceeded):
        queue.put_nowait({'content': 'synthetic-three'})
    queue.put_nowait(eof)
    assert queue.get_nowait() is first
    assert queue.get_nowait() is second
    assert queue.get_nowait() is eof
    assert queue.estimated_bytes == 0
    snapshot = buffers.diagnostics()
    assert snapshot['overflows'] >= 1
    assert all(isinstance(value, int) for value in snapshot.values())
    assert 'synthetic' not in str(snapshot)


def test_single_large_message_is_rejected_before_retention():
    queue = buffers.RuntimeMessageQueue(lane='test', max_bytes=1024)
    with pytest.raises(buffers.RuntimeBufferExceeded):
        queue.put_nowait({'content': 'x' * 2048})
    assert queue.qsize() == 0 and queue.estimated_bytes == 0


def test_overflow_interrupts_exact_client_and_closes_without_control_deadlock(monkeypatch):
    monkeypatch.setattr(buffers, 'MAX_EVENTS', 2)
    async def run():
        interrupted = asyncio.Event()
        disconnected = []
        notifications = []
        class Client:
            async def receive_messages(self):
                for i in range(3):
                    yield {'sequence': i}
                await asyncio.Event().wait()
            async def interrupt(self):
                # This SDK control can complete without the application's
                # consumer reading its queued data messages.
                await asyncio.sleep(0)
                interrupted.set()
        client = Client()
        key = ('budget-test', 'model', '', '')
        async def disconnect(_sid, clients=()):
            disconnected.extend(clients)
            return True
        hooks = SimpleNamespace(observe_stream_message=lambda *_: False,
                                evict_failed_session_stream=runtime.evict_failed_session_stream,
                                session_runtime_disconnected=notifications.append,
                                join_session_disconnects=disconnect)
        monkeypatch.setattr(runtime, '_hooks', hooks)
        monkeypatch.setattr(runtime, 'CLIENTS', {key: client})
        monkeypatch.setattr(runtime, 'SESSION_STREAMS', {})
        stream = runtime.SessionStream(key, client)
        runtime.SESSION_STREAMS[key] = stream
        queue = stream.attach_turn()
        await asyncio.wait_for(stream.task, timeout=0.5)
        assert interrupted.is_set() and disconnected == [client]
        assert notifications == ['budget-test']
        assert isinstance(stream._failure, buffers.RuntimeBufferExceeded)
        assert [await queue.get(), await queue.get()] == [{'sequence': 0}, {'sequence': 1}]
        assert await queue.get() is runtime.STREAM_EOF
        assert key not in runtime.CLIENTS
    asyncio.run(run())


def test_old_stream_failure_cannot_reset_replacement_runtime(monkeypatch):
    async def run():
        key = ('same-session', 'model', '', '')
        old, replacement = object(), object()
        notifications, disconnected = [], []
        async def disconnect(_sid, clients=()):
            disconnected.extend(clients)
            return True
        hooks = SimpleNamespace(session_runtime_disconnected=notifications.append,
                                join_session_disconnects=disconnect)
        monkeypatch.setattr(runtime, '_hooks', hooks)
        monkeypatch.setattr(runtime, 'CLIENTS', {key: replacement})
        replacement_stream = object()
        monkeypatch.setattr(runtime, 'SESSION_STREAMS', {key: replacement_stream})
        old_stream = SimpleNamespace(key=key, client=old, _failure=buffers.RuntimeBufferExceeded())
        await runtime.evict_failed_session_stream(old_stream)
        assert runtime.CLIENTS[key] is replacement
        assert runtime.SESSION_STREAMS[key] is replacement_stream
        assert notifications == [] and disconnected == [old]
    asyncio.run(run())


def test_service_status_exposes_only_numeric_buffer_diagnostics(client, auth):
    response = client.get('/api/settings/service', headers=auth)
    assert response.status_code == 200
    metrics = response.json()['diagnostics']['runtime_buffers']
    assert all(isinstance(value, int) for value in metrics.values())
    assert {'depth', 'estimated_bytes', 'oldest_ms', 'overflows'} <= metrics.keys()


def test_orphan_overflow_before_pump_first_tick_still_disconnects(monkeypatch):
    monkeypatch.setattr(runtime.SessionStream, '_ORPHAN_MAX', 1)
    async def run():
        cleanups, disconnected, interrupted = [], [], []
        class Client:
            async def receive_messages(self):
                await asyncio.Event().wait()
                yield
            async def interrupt(self):
                interrupted.append(self)
        client = Client()
        key = ('parked-budget', 'model', '', '')
        async def disconnect(_sid, clients=()):
            disconnected.extend(clients)
            return True
        hooks = SimpleNamespace(observe_stream_message=lambda *_: False,
                                evict_failed_session_stream=runtime.evict_failed_session_stream,
                                session_runtime_disconnected=lambda _: None,
                                join_session_disconnects=disconnect,
                                retain_detached_cleanup=cleanups.append)
        monkeypatch.setattr(runtime, '_hooks', hooks)
        monkeypatch.setattr(runtime, 'CLIENTS', {key: client})
        monkeypatch.setattr(runtime, 'SESSION_STREAMS', {})
        stream = runtime.SessionStream(key, client)
        runtime.SESSION_STREAMS[key] = stream
        queue = stream.attach_turn()
        with pytest.raises(buffers.RuntimeBufferExceeded):
            stream.park_messages(['first', 'second'])
        assert await queue.get() is runtime.STREAM_EOF
        await asyncio.gather(stream.task, return_exceptions=True)
        await asyncio.gather(*cleanups)
        assert disconnected == [client] and interrupted == [client]
        assert list(stream._orphans) == ['first']
    asyncio.run(run())


def test_racing_failed_stream_cleanup_interrupts_only_once(monkeypatch):
    async def run():
        interrupted, disconnected = [], []
        class Client:
            async def interrupt(self):
                interrupted.append(self)
                await asyncio.sleep(0)
        client = Client()
        key = ('cleanup-once', 'model', '', '')
        async def disconnect(_sid, clients=()):
            disconnected.extend(clients)
            return True
        monkeypatch.setattr(runtime, '_hooks', SimpleNamespace(
            session_runtime_disconnected=lambda _: None, join_session_disconnects=disconnect))
        monkeypatch.setattr(runtime, 'CLIENTS', {key: client})
        monkeypatch.setattr(runtime, 'SESSION_STREAMS', {})
        stream = SimpleNamespace(key=key, client=client, _failure=buffers.RuntimeBufferExceeded())
        await asyncio.gather(*(runtime.evict_failed_session_stream(stream) for _ in range(3)))
        assert interrupted == [client] and disconnected == [client]
    asyncio.run(run())
