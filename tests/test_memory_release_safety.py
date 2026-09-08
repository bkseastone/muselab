"""Governance/index ordering and complete, strictly validated memory restores."""
import asyncio
import copy

import pytest


@pytest.fixture
def memory_instance(tmp_path):
    from backend.memory_config import MemoryConfig
    from backend.memory_engine import MemoryEngine
    from backend.memory_store import MemoryStore

    instance = MemoryEngine(MemoryStore(tmp_path / "registry.sqlite3"))
    cfg = MemoryConfig.model_validate({
        "mode": "active", "generation_model": "test:model",
        "embedding": {"base_url": "http://embed.invalid", "model": "test", "dimensions": 3},
        "vector": {"url": "http://vector.invalid", "collection": "test"},
    })
    instance.config = lambda: cfg
    return instance


class _Vectors:
    def __init__(self, gate=None, entered=None):
        self.points = {}
        self.gate, self.entered = gate, entered

    async def ensure(self, _dimensions):
        pass

    async def upsert_many(self, items):
        if self.entered:
            self.entered.set()
            await self.gate.wait()
        for mid, _vector, payload in items:
            self.points[mid] = payload

    async def delete(self, mid):
        self.points.pop(mid, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["embedding", "upsert"])
@pytest.mark.parametrize("action", ["forget", "correct"])
async def test_retirement_wins_over_inflight_index(
    memory_instance, monkeypatch, stage, action,
):
    from backend import memory_engine as module

    instance = memory_instance
    item = instance.store.create_memory("default", "fact", "original synthetic memory")
    entered, release = asyncio.Event(), asyncio.Event()
    vectors = _Vectors(release if stage == "upsert" else None,
                       entered if stage == "upsert" else None)
    vectors.points[item["id"]] = {"status": "active"}

    async def embed(_self, texts):
        if stage == "embedding":
            entered.set()
            await release.wait()
        return [[1., 0., 0.] for _ in texts]

    monkeypatch.setattr(module.EmbeddingProvider, "embed", embed)
    monkeypatch.setattr(module, "vector_store", lambda _cfg: vectors)
    indexing = asyncio.create_task(instance._index_memory(item["id"]))
    await asyncio.wait_for(entered.wait(), 2)
    operation = (instance.forget_memory(item["id"]) if action == "forget"
                 else instance.correct_memory(item["id"], "replacement synthetic memory"))
    retiring = asyncio.create_task(operation)
    if stage == "embedding":
        await asyncio.wait_for(retiring, 2)
    else:
        await asyncio.sleep(0)
        assert not retiring.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(indexing, retiring), 2)
    row = instance.store.memory(item["id"])
    assert row["status"] == ("deleted" if action == "forget" else "superseded")
    assert row["embedding_state"] == "pending"
    assert item["id"] not in vectors.points
    await instance.stop()


@pytest.mark.asyncio
async def test_cancelled_index_keeps_fence_until_upsert_finishes(memory_instance, monkeypatch):
    from backend import memory_engine as module

    instance = memory_instance
    item = instance.store.create_memory("default", "fact", "cancelled index")
    entered, release = asyncio.Event(), asyncio.Event()
    vectors = _Vectors(release, entered)

    async def embed(_self, texts):
        return [[1., 0., 0.] for _ in texts]

    monkeypatch.setattr(module.EmbeddingProvider, "embed", embed)
    monkeypatch.setattr(module, "vector_store", lambda _cfg: vectors)
    indexing = asyncio.create_task(instance._index_memory(item["id"]))
    await asyncio.wait_for(entered.wait(), 2)
    indexing.cancel()
    await asyncio.sleep(0)
    assert not indexing.done()
    retiring = asyncio.create_task(instance.forget_memory(item["id"]))
    await asyncio.sleep(0)
    assert not retiring.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await indexing
    assert await retiring
    assert item["id"] not in vectors.points
    await instance.stop()


@pytest.mark.asyncio
async def test_cancelled_forget_finishes_registry_and_vector_retirement(memory_instance, monkeypatch):
    from backend import memory_engine as module

    instance = memory_instance
    item = instance.store.create_memory("default", "fact", "cancelled forget")
    entered, release = asyncio.Event(), asyncio.Event()
    vectors = _Vectors()
    vectors.points[item["id"]] = {"status": "active"}

    async def delete(mid):
        entered.set()
        await release.wait()
        vectors.points.pop(mid, None)

    vectors.delete = delete
    monkeypatch.setattr(module, "vector_store", lambda _cfg: vectors)
    retiring = asyncio.create_task(instance.forget_memory(item["id"]))
    await asyncio.wait_for(entered.wait(), 2)
    retiring.cancel()
    await asyncio.sleep(0)
    assert not retiring.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await retiring
    assert instance.store.memory(item["id"])["status"] == "deleted"
    assert item["id"] not in vectors.points
    await instance.stop()


@pytest.mark.asyncio
async def test_reindex_all_includes_10001_rows_in_bounded_durable_batches(memory_instance):
    instance = memory_instance
    ids = {f"memory-{index:05d}" for index in range(10001)}
    with instance.store._write_tx() as conn:
        conn.executemany(
            "INSERT INTO memories(id,owner_id,kind,content,created_at,updated_at) "
            "VALUES (?, 'default', 'fact', 'synthetic', 1, 1)",
            [(mid,) for mid in ids],
        )
        conn.execute("INSERT INTO memories(id,owner_id,kind,content,status,created_at,updated_at) "
                     "VALUES ('retired', 'default', 'fact', 'synthetic', 'deleted', 1, 1)")
    assert await instance.reindex_all() == 10001
    jobs = instance.store.list_jobs(limit=500)
    batches = [job["payload"]["memory_ids"] for job in jobs]
    assert len(jobs) == 40
    assert all(0 < len(batch) <= 256 for batch in batches)
    assert {mid for batch in batches for mid in batch} == ids
    assert sum(map(len, batches)) == len(ids)
    from backend.memory_store import MemoryStore
    reopened = MemoryStore(instance.store.path)
    assert len(reopened.list_jobs(limit=500)) == 40
    await instance.stop()


@pytest.mark.parametrize("field,value", [
    ("content", None), ("confidence", "not-a-number"),
    ("confidence", float("inf")), ("confidence", float("nan")),
    ("confidence", True), ("confidence", 1.1), ("created_at", 10**400),
    ("status", "unknown"), ("kind", "unknown"),
    ("attributes", []), ("tags", {}), ("version", 0),
])
def test_v2_bad_row_rolls_back_all_tables(tmp_path, field, value):
    from backend.memory_store import MemoryStore, SnapshotValidationError

    source = MemoryStore(tmp_path / "source.sqlite3")
    source.add_evidence("default", "synthetic-session", "user", "synthetic evidence")
    source.create_memory("default", "fact", "first valid row")
    source.create_memory("default", "fact", "bad row")
    snapshot = source.export_snapshot("default")
    if field == "content":
        snapshot["memories"][1].pop(field)
    else:
        snapshot["memories"][1][field] = value
    target = MemoryStore(tmp_path / "target.sqlite3")
    with pytest.raises(SnapshotValidationError) as error:
        target.import_snapshot(snapshot, "default")
    assert error.value.detail["table"] == "memories"
    assert error.value.detail["row"] == 2
    assert error.value.detail["field"] == field
    assert target.export_snapshot("default")["memories"] == []
    assert target.export_snapshot("default")["evidence"] == []


def test_v2_invalid_reference_rolls_back_and_valid_replay_preserves_governance(tmp_path):
    from backend.memory_store import MemoryStore, SnapshotValidationError

    source = MemoryStore(tmp_path / "source.sqlite3")
    eid = source.add_evidence("default", "session", "user", "synthetic source")
    episode = source.get_or_create_episode("default", "session", idle_seconds=60)
    source.attach_evidence(episode["id"], [eid])
    item = source.create_memory("default", "fact", "synthetic fact")
    snapshot = source.export_snapshot("default")
    broken = copy.deepcopy(snapshot)
    broken["episode_evidence"][0]["evidence_id"] = "missing-evidence"
    target = MemoryStore(tmp_path / "target.sqlite3")
    with pytest.raises(SnapshotValidationError, match="reference_or_unique_conflict"):
        target.import_snapshot(broken, "default")
    assert target.export_snapshot("default")["evidence"] == []
    assert target.import_snapshot(snapshot, "default")["memories"] == 1
    target.delete_memory(item["id"], "default")
    assert target.import_snapshot(snapshot, "default")["memories"] == 0
    assert target.memory(item["id"])["status"] == "deleted"


def test_v2_import_api_returns_safe_row_diagnostic(client, auth, tmp_path):
    from backend.memory_store import MemoryStore

    source = MemoryStore(tmp_path / "portable.sqlite3")
    source.create_memory("default", "fact", "private synthetic content")
    snapshot = {"schema": "muselab-memory-export-v2", **source.export_snapshot("default")}
    snapshot["memories"][0]["confidence"] = "private synthetic invalid value"
    response = client.post("/api/memory/import", headers=auth, json=snapshot)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "category": "invalid_snapshot", "table": "memories", "row": 1,
        "field": "confidence", "reason": "invalid_number",
    }
    assert "private synthetic" not in response.text
    assert client.get("/api/memory/export", headers=auth).json()["memories"] == []
