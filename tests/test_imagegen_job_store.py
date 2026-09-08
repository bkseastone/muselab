"""Concurrency and durability tests for image-generation job persistence."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.fixture
def store_tools(monkeypatch, tmp_path):
    # backend.settings validates the service token at import time.  Keep this
    # focused unit suite hermetic without leaking a module-level env mutation.
    monkeypatch.setenv(
        "MUSELAB_TOKEN", "test-token-1234567890abcdef-secure-min-32")
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    monkeypatch.setenv("MUSELAB_ROOT", str(root))
    from backend.imagegen_job_store import ImagegenJobStore
    from backend.settings import atomic_write_text

    return ImagegenJobStore, atomic_write_text


def _job(job_id: str, created_at: float, *, status: str = "queued") -> dict:
    return {
        "id": job_id,
        "status": status,
        "prompt": f"prompt-{job_id}",
        "images": [],
        "created_at": created_at,
        "updated_at": created_at,
    }


def _read_jobs(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["jobs"]


def test_late_old_snapshot_cannot_overwrite_newer_revision(tmp_path, store_tools):
    ImagegenJobStore, _atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    store = ImagegenJobStore(path)
    store.put(_job("job-1", 1.0, status="running"))
    with store._state_lock:
        old_revision = store._revision
        old_snapshot = copy.deepcopy(store._jobs)

    store.update("job-1", status="succeeded", error="")
    latest_payload = path.read_text(encoding="utf-8")
    store._persist_snapshot(old_revision, old_snapshot or {})

    assert path.read_text(encoding="utf-8") == latest_payload
    assert _read_jobs(path)["job-1"]["status"] == "succeeded"


def test_concurrent_save_update_and_cleanup_converge(tmp_path, store_tools):
    ImagegenJobStore, _atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    store = ImagegenJobStore(path, max_jobs=3)
    for index in range(1, 4):
        store.put(_job(f"job-{index}", float(index), status="succeeded"))

    barrier = threading.Barrier(3)

    def update_latest() -> None:
        barrier.wait(timeout=5)
        store.update("job-3", status="succeeded", result="latest")

    def add_newest() -> None:
        barrier.wait(timeout=5)
        store.put(_job("job-4", 4.0, status="succeeded"))

    def cleanup() -> None:
        barrier.wait(timeout=5)
        store.cleanup()

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(update_latest),
            pool.submit(add_newest),
            pool.submit(cleanup),
        ]
        for future in futures:
            future.result(timeout=10)

    store.flush()
    memory = store.snapshot()
    disk = _read_jobs(path)
    assert disk == memory
    assert list(disk) == ["job-4", "job-3", "job-2"]
    assert disk["job-3"]["status"] == "succeeded"
    assert disk["job-3"]["result"] == "latest"


def test_failed_write_keeps_old_file_and_next_flush_recovers(tmp_path, store_tools):
    ImagegenJobStore, atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    fail_next = False

    def flaky_writer(target: Path, payload: str) -> None:
        nonlocal fail_next
        if fail_next:
            fail_next = False
            raise OSError("injected persistence failure")
        atomic_write_text(target, payload, mode=0o600)

    store = ImagegenJobStore(path, writer=flaky_writer)
    store.put(_job("job-1", 1.0, status="queued"))
    old_payload = path.read_text(encoding="utf-8")

    fail_next = True
    with pytest.raises(OSError, match="injected persistence failure"):
        store.update("job-1", status="running")
    assert path.read_text(encoding="utf-8") == old_payload
    assert store.snapshot()["job-1"]["status"] == "running"

    store.flush()
    assert _read_jobs(path)["job-1"]["status"] == "running"


def test_restart_recovers_interrupted_jobs_and_prunes_history(tmp_path, store_tools):
    ImagegenJobStore, _atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    path.parent.mkdir()
    jobs = {
        f"job-{index}": _job(
            f"job-{index}",
            float(index),
            status="running" if index == 4 else "succeeded",
        )
        for index in range(1, 5)
    }
    path.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")

    store = ImagegenJobStore(path, max_jobs=3, clock=lambda: 99.0)
    recovered = store.snapshot()
    disk = _read_jobs(path)

    assert list(recovered) == ["job-4", "job-3", "job-2"]
    assert recovered == disk
    assert recovered["job-4"]["status"] == "failed"
    assert recovered["job-4"]["updated_at"] == 99.0
    assert "backend restart" in recovered["job-4"]["error"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_async_cancel_joins_writer_without_blocking_event_loop(
    tmp_path, store_tools,
):
    ImagegenJobStore, atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_writer(target: Path, payload: str) -> None:
        entered.set()
        try:
            assert release.wait(timeout=5)
            atomic_write_text(target, payload, mode=0o600)
        finally:
            finished.set()

    store = ImagegenJobStore(path, writer=blocking_writer)
    owner = asyncio.create_task(store.put_async(_job("job-1", 1.0)))
    assert await asyncio.to_thread(entered.wait, 5)

    # The writer holds only the persistence lock: both the event loop and a
    # concurrent state snapshot remain responsive while filesystem I/O stalls.
    await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
    snapshot = await asyncio.wait_for(
        asyncio.to_thread(store.snapshot), timeout=0.2)
    assert snapshot["job-1"]["status"] == "queued"

    owner.cancel()
    await asyncio.sleep(0)
    assert not owner.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(owner, timeout=5)
    assert finished.is_set()
    assert _read_jobs(path)["job-1"]["status"] == "queued"


def test_writer_serialization_publishes_newest_concurrent_mutation(
    tmp_path, store_tools,
):
    ImagegenJobStore, atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    entered = threading.Event()
    release = threading.Event()
    payloads: list[str] = []
    calls_lock = threading.Lock()

    def ordered_writer(target: Path, payload: str) -> None:
        with calls_lock:
            call = len(payloads)
            payloads.append(payload)
        if call == 0:
            entered.set()
            assert release.wait(timeout=5)
        atomic_write_text(target, payload, mode=0o600)

    store = ImagegenJobStore(path, writer=ordered_writer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            store.put, _job("job-1", 1.0, status="queued"))
        assert entered.wait(timeout=5)
        second = pool.submit(
            store.update, "job-1", status="succeeded")
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert len(payloads) == 2
    assert (
        json.loads(payloads[0])["jobs"]["job-1"]["status"]
        == "queued"
    )
    assert (
        json.loads(payloads[1])["jobs"]["job-1"]["status"]
        == "succeeded"
    )
    assert _read_jobs(path)["job-1"]["status"] == "succeeded"


def test_201st_job_archives_history_and_keeps_old_result_accessible(tmp_path, store_tools):
    ImagegenJobStore, _atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    store = ImagegenJobStore(path, max_jobs=200)
    result = path.parent / "files" / "job-0" / "image-1.png"
    result.parent.mkdir(parents=True)
    result.write_bytes(b"synthetic result")
    first = _job("job-0", 0.0, status="succeeded")
    first["images"] = [{"file": "image-1.png"}]
    store.put(first)
    for index in range(1, 201):
        store.put(_job(f"job-{index}", float(index), status="succeeded"))
    assert len(store.snapshot()) == 200
    assert "job-0" not in _read_jobs(path)
    assert store.get("job-0") == first
    assert ImagegenJobStore(path).get("job-0") == first
    assert result.read_bytes() == b"synthetic result"
    archived = store._archive_path("job-0")
    assert archived.stat().st_mode & 0o777 == 0o600
    assert archived.parent.stat().st_mode & 0o777 == 0o700


def test_active_jobs_are_never_removed_by_history_budget(tmp_path, store_tools):
    ImagegenJobStore, _atomic_write_text = store_tools
    store = ImagegenJobStore(tmp_path / "imagegen" / "jobs.json", max_jobs=2)
    for index in range(4):
        store.put(_job(f"job-{index}", float(index), status="running"))
    store.cleanup()
    assert len(store.snapshot()) == 4
    assert len(_read_jobs(store.path)) == 4
    assert not store.archive_dir.exists()
    store.update("job-0", status="succeeded")
    assert len(store.snapshot()) == 3
    assert store.get("job-0")["status"] == "succeeded"


def test_archive_failure_does_not_discard_hot_metadata(tmp_path, store_tools):
    ImagegenJobStore, atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    fail_archive = True

    def writer(target, payload):
        if fail_archive and target.parent.name == "jobs-archive":
            raise OSError("injected archive failure")
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, payload, mode=0o600)

    store = ImagegenJobStore(path, max_jobs=1, writer=writer)
    store.put(_job("old", 0., status="succeeded"))
    with pytest.raises(OSError, match="injected archive failure"):
        store.put(_job("new", 1., status="succeeded"))
    assert store.get("old") is not None
    assert "old" in _read_jobs(path)
    assert "old" in store.snapshot()
    fail_archive = False
    store.flush()
    assert list(store.snapshot()) == ["new"]
    assert ImagegenJobStore(path).get("old")["status"] == "succeeded"


def test_archived_job_update_is_durable_and_can_become_active(tmp_path, store_tools):
    ImagegenJobStore, _atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    store = ImagegenJobStore(path, max_jobs=1)
    store.put(_job("old", 0., status="succeeded"))
    store.put(_job("new", 1., status="succeeded"))
    assert store.update("old", error="corrected")["error"] == "corrected"
    assert ImagegenJobStore(path).get("old")["error"] == "corrected"
    store.update("old", status="running")
    assert "old" in store.snapshot()
    assert store.get("old")["status"] == "running"
    store.update("old", status="failed", error="cancelled")
    assert ImagegenJobStore(path).get("old")["error"] == "cancelled"


def test_cleanup_archives_every_displaced_row_before_trimming(tmp_path, store_tools):
    ImagegenJobStore, _atomic_write_text = store_tools
    path = tmp_path / "imagegen" / "jobs.json"
    store = ImagegenJobStore(path, max_jobs=3)
    for index in range(3):
        store.put(_job(f"job-{index}", float(index), status="succeeded"))
    store.max_jobs = 1
    assert store.cleanup()
    assert len(store.snapshot()) == 1
    reopened = ImagegenJobStore(path, max_jobs=1)
    assert all(reopened.get(f"job-{index}") is not None for index in range(3))


def test_archived_image_remains_available_through_existing_api(client, auth, monkeypatch):
    from backend import chat
    from backend.imagegen_job_store import ImagegenJobStore

    store = ImagegenJobStore(chat._IMAGEGEN_JOBS_PATH, max_jobs=1)
    monkeypatch.setattr(chat, "_imagegen_job_store", store)
    image_dir = chat._IMAGEGEN_FILES / "old"
    image_dir.mkdir(parents=True)
    (image_dir / "image-1.png").write_bytes(b"synthetic image bytes")
    old = _job("old", 0., status="succeeded")
    old["images"] = [{
        "image_id": "old-image", "file": "image-1.png", "mime": "image/png",
        "name": "image-1.png", "bytes": 21,
    }]
    store.put(old)
    store.put(_job("new", 1., status="succeeded"))
    response = client.get("/api/chat/image-generate/jobs/old", headers=auth)
    assert response.status_code == 200
    assert response.json()["job"]["id"] == "old"
    image_url = response.json()["job"]["images"][0]["url"]
    result = client.get(image_url, headers=auth)
    assert result.status_code == 200
    assert result.content == b"synthetic image bytes"
