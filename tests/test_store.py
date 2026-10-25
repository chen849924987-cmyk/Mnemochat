"""存储层：roundtrip、FTS/BM25、向量缓存、实体索引、强化更新。"""

import numpy as np
import pytest

from memory.models import MemoryRecord
from util.text import fts_query, seg, tokenize


async def test_memory_roundtrip(store):
    mem = MemoryRecord(
        content="小明对花生过敏", group_id="g1", subject_user_id="小明",
        keywords=["花生"], entities=["小明", "花生"], importance=8,
    )
    mem_id = await store.add_memory(mem)
    got = await store.get_memory(mem_id)
    assert got is not None
    assert got.content == "小明对花生过敏"
    assert got.keywords == ["花生"]
    assert got.entities == ["小明", "花生"]
    assert got.stage == "staging" and got.status == "active"
    assert got.source_msg_ids == []


async def test_messages_recent_order(store):
    await store.add_message("g1", "a", "a", "早", 1.0)
    await store.add_message("g1", "b", "b", "晚", 2.0)
    msgs = await store.recent_messages("g1", limit=10)
    assert [m.content for m in msgs] == ["早", "晚"]  # 时间正序
    msgs2 = await store.recent_messages("g1", limit=10, after_ts=1.5)
    assert [m.content for m in msgs2] == ["晚"]


async def test_fts_search_hits_by_keyword(store):
    m1 = MemoryRecord(content="小明对花生过敏", group_id="g1")
    m2 = MemoryRecord(content="小红喜欢打篮球", group_id="g1")
    id1 = await store.add_memory(m1)
    await store.add_memory(m2)

    hits = await store.bm25_search(fts_query("花生 过敏"), 10)
    assert [mid for mid, _ in hits] == [id1]


async def test_fts_excludes_superseded(store):
    mem = MemoryRecord(content="小明爱吃辣", group_id="g1", stage="ltm")
    mid = await store.add_memory(mem)
    assert await store.bm25_search(fts_query("小明 爱吃辣"), 10)
    await store.update_memory(mid, status="superseded")
    assert await store.bm25_search(fts_query("小明 爱吃辣"), 10) == []


async def test_vector_topk_and_group_filter(store, embedder):
    texts = ["小明对花生过敏", "小红喜欢打篮球", "今天天气不错"]
    ids = []
    for t in texts:
        mem = MemoryRecord(content=t, group_id="g1" if t != "今天天气不错" else "g2")
        mid = await store.add_memory(mem)
        vec = (await embedder.embed([t]))[0]
        await store.save_embedding(mid, embedder.name, vec)
        ids.append(mid)

    q = (await embedder.embed(["小明对花生过敏"]))[0]
    hits = await store.vector_topk(embedder.name, q, k=3)
    assert hits[0][0] == ids[0]
    assert hits[0][1] > 0.99
    # 群过滤：g2 的记忆不应出现
    hits_g1 = await store.vector_topk(embedder.name, q, k=3, group_id="g1")
    assert ids[2] not in [mid for mid, _ in hits_g1]


async def test_vector_sims_for_candidates(store, embedder):
    mem = MemoryRecord(content="小明对花生过敏", group_id="g1")
    mid = await store.add_memory(mem)
    vec = (await embedder.embed(["小明对花生过敏"]))[0]
    await store.save_embedding(mid, embedder.name, vec)
    q = (await embedder.embed(["花生过敏"]))[0]
    sims = await store.vector_sims(embedder.name, q, [mid, 99999])
    assert 0.0 < sims[mid] <= 1.0
    assert sims[99999] == 0.0


async def test_apply_reinforcement(store, seed_memory):
    mem = await seed_memory("小明对花生过敏")
    s0 = mem.strength
    await store.apply_reinforcement([mem.id], now=2_000_000.0, factor=1.25, cap=365.0)
    got = await store.get_memory(mem.id)
    assert got.access_count == 1
    assert got.last_accessed_at == 2_000_000.0
    assert got.strength == pytest.approx(s0 * 1.25)


async def test_update_memory_whitelist(store, seed_memory):
    mem = await seed_memory("小明对花生过敏")
    await store.update_memory(mem.id, stage="ltm", recurrence_count=2)
    got = await store.get_memory(mem.id)
    assert got.stage == "ltm" and got.recurrence_count == 2
    with pytest.raises(ValueError):
        await store.update_memory(mem.id, content="篡改内容")  # content 不可变


async def test_entity_index(store):
    mem = MemoryRecord(content="小明对花生过敏", group_id="g1", entities=["小明", "花生"])
    mid = await store.add_memory(mem)
    names = await store.entity_names("g1")
    assert set(names) == {"小明", "花生"}
    hits = await store.memories_by_entities("g1", ["小明"], 10)
    assert hits == [mid]
    assert await store.memories_by_entities("g1", ["不存在的人"], 10) == []


def test_tokenize_and_seg():
    assert "花生" in tokenize("小明对花生过敏")
    assert "的" not in tokenize("我的花生")  # 单字汉字过滤
    assert seg("小明对花生过敏") == "小明 花生 过敏"
    assert fts_query("花生 过敏!") == '"花生" OR "过敏"'
