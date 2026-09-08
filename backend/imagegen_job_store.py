"""Durable, serialized persistence for image-generation job metadata."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from .private_storage import ensure_private_directory
from .settings import atomic_write_text


_T = TypeVar("_T")
Job = dict[str, Any]
Jobs = dict[str, Job]
Writer = Callable[[Path, str], None]


def _default_writer(path: Path, payload: str) -> None:
    ensure_private_directory(path.parent)
    atomic_write_text(path, payload, mode=0o600)


def _created_at(item: tuple[str, Job]) -> float:
    try:
        return float(item[1].get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


async def _run_owned(call: Callable[[], _T]) -> _T:
    """Join a mutating worker even when its asyncio owner is cancelled.

    Cancelling ``asyncio.to_thread`` only abandons the awaiter; the thread and
    its filesystem commit keep running.  Shielding and joining here means an
    API cancellation cannot leave an unobserved writer racing a later save.
    """
    task = asyncio.create_task(asyncio.to_thread(call))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        # The caller's cancellation remains authoritative.  Retrieve a worker
        # failure so asyncio does not report it as an unhandled task exception.
        try:
            task.result()
        except BaseException:
            pass
        raise cancelled


class ImagegenJobStore:
    """In-memory job registry with revisioned, atomic JSON persistence.

    State mutation and deep-copy snapshots are the only work done under the
    state lock.  Ordering, JSON encoding, directory preparation, fsync and
    replace happen outside it.  A separate writer lock serializes commits.
    Revision checks make a late caller skip or refresh an old snapshot, so an
    older state can never replace a newer state on disk.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_jobs: int = 200,
        clock: Callable[[], float] = time.time,
        writer: Writer | None = None,
    ) -> None:
        self.path = Path(path)
        self.max_jobs = max(1, max_jobs)
        self.archive_dir = self.path.with_name(f"{self.path.stem}-archive")
        self._clock = clock
        self._writer = writer or _default_writer
        self._state_lock = threading.RLock()
        self._load_lock = threading.Lock()
        self._writer_lock = threading.Lock()
        self._jobs: Jobs | None = None
        self._revision = 0
        self._persisted_revision = 0

    def _read_disk(self) -> tuple[Jobs, bool]:
        jobs: Jobs = {}
        dirty = False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            raw_jobs = raw.get("jobs", {}) if isinstance(raw, dict) else {}
            if isinstance(raw_jobs, dict):
                for job_id, raw_job in raw_jobs.items():
                    if not isinstance(job_id, str) or not isinstance(raw_job, dict):
                        continue
                    job = copy.deepcopy(raw_job)
                    if job.get("status") in {"queued", "running"}:
                        job.update({
                            "status": "failed",
                            "error": (
                                "image generation was interrupted by "
                                "backend restart"
                            ),
                            "updated_at": self._clock(),
                        })
                        dirty = True
                    jobs[job_id] = job
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(
                f"[muselab] failed to load imagegen jobs: {exc}",
                file=sys.stderr,
                flush=True,
            )
        if len(jobs) > self.max_jobs:
            dirty = True
        return jobs, dirty

    def _ensure_loaded(self) -> None:
        with self._state_lock:
            if self._jobs is not None:
                return
        with self._load_lock:
            with self._state_lock:
                if self._jobs is not None:
                    return
            jobs, dirty = self._read_disk()
            with self._state_lock:
                if self._jobs is None:
                    self._jobs = jobs
                    self._revision = 1 if dirty else 0
                    self._persisted_revision = 0
                    revision = self._revision
                    snapshot = copy.deepcopy(jobs)
                else:
                    return
            if dirty:
                try:
                    self._persist_snapshot(revision, snapshot)
                except Exception as exc:
                    # Loading remains available from the recovered in-memory
                    # state.  The next mutation or explicit flush retries the
                    # latest revision without publishing an older snapshot.
                    print(
                        f"[muselab] failed to recover imagegen jobs: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )

    def _archive_path(self, job_id: str) -> Path:
        # IDs originate in API routes as well as legacy metadata. Hashing keeps
        # archived lookup within its private directory for every possible ID.
        name = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
        return self.archive_dir / f"{name}.json"

    def _read_archive(self, job_id: str) -> Job | None:
        try:
            job = json.loads(self._archive_path(job_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise ValueError("invalid archived image generation job")
        return job

    def _prepared(self, snapshot: Jobs) -> tuple[Jobs, str]:
        rows = sorted(snapshot.items(), key=_created_at, reverse=True)
        # max_jobs limits the hot history window, never the lifetime of a
        # user's result. Active/unknown states are retained until terminal.
        ordered = {
            job_id: job for index, (job_id, job) in enumerate(rows)
            if index < self.max_jobs
            or job.get("status") not in {"succeeded", "failed", "cancelled"}
        }
        payload = json.dumps(
            {"jobs": ordered}, ensure_ascii=False, separators=(",", ":"),
        )
        return ordered, payload

    def _persist_snapshot(self, revision: int, snapshot: Jobs) -> None:
        """Persist at least ``revision`` without allowing stale overwrite."""
        with self._writer_lock:
            with self._state_lock:
                if self._persisted_revision >= revision:
                    return
                # A newer mutation may have reached memory before this older
                # caller obtained the writer.  Coalesce directly to latest.
                if self._revision != revision:
                    revision = self._revision
                    snapshot = copy.deepcopy(self._jobs or {})
            ordered, payload = self._prepared(snapshot)
            # Archive first. If any archive/main write fails, the old hot file
            # and in-memory rows still own every result; a later flush retries.
            for job_id, job in snapshot.items():
                if job_id not in ordered:
                    self._writer(self._archive_path(job_id), json.dumps(
                        job, ensure_ascii=False, separators=(",", ":")))
            self._writer(self.path, payload)
            with self._state_lock:
                self._persisted_revision = max(
                    self._persisted_revision, revision
                )
                # Only publish pruning back into memory if no mutation landed
                # while the payload was encoded or written.  Otherwise that
                # newer caller owns the next commit and cleanup pass.
                if self._revision == revision:
                    self._jobs = copy.deepcopy(ordered)

    def snapshot(self) -> Jobs:
        self._ensure_loaded()
        with self._state_lock:
            return copy.deepcopy(self._jobs or {})

    def get(self, job_id: str) -> Job | None:
        self._ensure_loaded()
        with self._state_lock:
            job = (self._jobs or {}).get(job_id)
            if job is not None:
                return copy.deepcopy(job)
        return self._read_archive(job_id)

    def list(self, limit: int) -> list[Job]:
        self._ensure_loaded()
        with self._state_lock:
            jobs = copy.deepcopy(list((self._jobs or {}).values()))
        jobs.sort(
            key=lambda job: _created_at(("", job)),
            reverse=True,
        )
        return jobs[: max(1, min(limit, 100))]

    def put(self, job: Job) -> Job:
        job_id = str(job.get("id") or "")
        if not job_id:
            raise ValueError("image generation job id is required")
        self._ensure_loaded()
        stored = copy.deepcopy(job)
        with self._state_lock:
            assert self._jobs is not None
            self._jobs[job_id] = stored
            self._revision += 1
            revision = self._revision
            snapshot = copy.deepcopy(self._jobs)
        self._persist_snapshot(revision, snapshot)
        return copy.deepcopy(stored)

    def update(self, job_id: str, **patch: Any) -> Job | None:
        existing = self.get(job_id)
        if existing is None:
            return None
        with self._state_lock:
            assert self._jobs is not None
            job = self._jobs.setdefault(job_id, existing)
            job.update(copy.deepcopy(patch))
            job["updated_at"] = self._clock()
            self._revision += 1
            revision = self._revision
            snapshot = copy.deepcopy(self._jobs)
            result = copy.deepcopy(job)
        self._persist_snapshot(revision, snapshot)
        return result

    def cleanup(self) -> bool:
        """Prune over-budget history and durably publish the latest state."""
        self._ensure_loaded()
        while True:
            with self._state_lock:
                assert self._jobs is not None
                base_revision = self._revision
                snapshot = copy.deepcopy(self._jobs)
            ordered, _payload = self._prepared(snapshot)
            with self._state_lock:
                if self._revision != base_revision:
                    continue
                changed = ordered != self._jobs
                if changed:
                    # Persist the full snapshot so _persist_snapshot can archive
                    # displaced rows before publishing the smaller hot window.
                    self._revision += 1
                revision = self._revision
                current = copy.deepcopy(self._jobs)
                break
        self._persist_snapshot(revision, current)
        return changed

    def flush(self) -> None:
        self._ensure_loaded()
        with self._state_lock:
            revision = self._revision
            snapshot = copy.deepcopy(self._jobs or {})
        self._persist_snapshot(revision, snapshot)

    async def put_async(self, job: Job) -> Job:
        return await _run_owned(lambda: self.put(job))

    async def update_async(self, job_id: str, **patch: Any) -> Job | None:
        return await _run_owned(lambda: self.update(job_id, **patch))

    async def cleanup_async(self) -> bool:
        return await _run_owned(self.cleanup)

    async def flush_async(self) -> None:
        await _run_owned(self.flush)
