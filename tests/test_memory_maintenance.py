"""Durable maintenance deduplication, progress and content-free diagnostics."""
import json
from concurrent.futures import ThreadPoolExecutor

from backend.memory_store import MemoryStore


def test_concurrent_reindex_reuses_operation_through_partial_completion_and_restart(tmp_path):
    path = tmp_path / "memory.sqlite3"
    a, b = MemoryStore(path), MemoryStore(path)
    for i in range(5):
        a.create_memory("u", "fact", f"synthetic {i}")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.enqueue_reindex_batches("u", batch_size=2), [a, b]))
    assert sorted(results) == [0, 5]
    job = a.claim_job()
    a.finish_job(job["id"])
    assert MemoryStore(path).enqueue_reindex_batches("u", batch_size=2) == 0
    progress = a.stats("u")["reindex_progress"]
    assert progress == {"total_batches": 3, "done_batches": 1, "failed_batches": 0, "pending_batches": 2}
    while job := a.claim_job():
        a.finish_job(job["id"])
    assert a.enqueue_reindex_batches("u", batch_size=2) == 5


def test_changed_snapshot_and_provider_revision_are_not_discarded(tmp_path):
    s = MemoryStore(tmp_path / "memory.sqlite3")
    s.create_memory("u", "fact", "first")
    assert s.enqueue_reindex_batches("u", revision="one") == 1
    assert s.enqueue_reindex_batches("u", revision="one") == 0
    assert s.enqueue_reindex_batches("u", revision="two") == 1
    s.create_memory("u", "fact", "second")
    assert s.enqueue_reindex_batches("u", revision="two") == 2
    s.create_memory("other", "fact", "separate")
    assert s.enqueue_reindex_batches("other", revision="two") == 1


def test_dream_dedup_survives_retry_and_recovery(tmp_path):
    path = tmp_path / "memory.sqlite3"
    s = MemoryStore(path)
    def enqueue(store=s, **kwargs):
        return store.enqueue("cross_episode_dream", {"episode_ids": ["a"]},
                             owner_id="u", deduplicate=True, **kwargs)
    first = enqueue()
    assert s.claim_job()["id"] == first
    assert enqueue(MemoryStore(path)) == first
    s.recover_running_jobs()
    s.claim_job()
    s.finish_job(first, error=json.dumps({"category": "timeout"}), retry_seconds=0)
    assert enqueue() == first
    s.claim_job()
    s.finish_job(first)
    assert enqueue() != first


def test_diagnostics_are_owner_scoped_and_do_not_expose_legacy_payloads(tmp_path):
    s = MemoryStore(tmp_path / "memory.sqlite3")
    secret = "synthetic-sensitive-value"
    job = s.enqueue("cross_episode_dream", {"private": secret}, owner_id="u")
    s.claim_job()
    s.finish_job(job, error=json.dumps({"category": {"unsafe": secret}, "reason": [secret]}))
    s.enqueue("cross_episode_dream", {}, owner_id="other")
    result = s.stats("u")
    assert result["queued_jobs"] == 0
    assert result["job_counts"] == {"failed": 1}
    assert result["recent_jobs"][0]["category"] == "unclassified"
    assert secret not in json.dumps(result)
    assert "payload" not in json.dumps(result)
