"""写入路径：提取、噪声门控、复现去重。"""

from memory.ingest import Ingestor, passes_noise_gate, parse_candidates
from memory.models import ExtractedMemory


def _ingestor(store, chat, embedder, cfg, clock):
    return Ingestor(store, chat, embedder, cfg, clock)


async def test_extract_allergy_to_staging(store, chat, embedder, cfg, clock, make_msg):
    ing = _ingestor(store, chat, embedder, cfg, clock)
    msgs = [make_msg("g1", "小明", "我对花生过敏，大家点菜注意点", ts=clock.now())]
    res = await ing.ingest_messages(msgs)

    assert res.extracted == 1 and res.inserted == 1
    mems = await store.list_memories(group_id="g1", stage="staging")
    assert len(mems) == 1
    mem = mems[0]
    assert "花生" in mem.content and "小明" in mem.content
    assert mem.kind == "fact"
    assert mem.subject_user_id == "小明"
    assert mem.importance >= 7
    # 嵌入已生成（入库即嵌入，供去重与聚类使用）
    vec = await store.get_embedding(mem.id, embedder.name)
    assert vec is not None and len(vec) == embedder.dim


async def test_chitchat_extracts_nothing(store, chat, embedder, cfg, clock, make_msg):
    ing = _ingestor(store, chat, embedder, cfg, clock)
    msgs = [
        make_msg("g1", "小明", "早上好", ts=clock.now()),
        make_msg("g1", "小红", "吃了吗", ts=clock.now()),
    ]
    res = await ing.ingest_messages(msgs)
    assert res.extracted == 0 and res.inserted == 0
    assert await store.list_memories(group_id="g1") == []


async def test_recurrence_reinforces_instead_of_duplicating(
    store, chat, embedder, cfg, clock, make_msg
):
    ing = _ingestor(store, chat, embedder, cfg, clock)
    msgs = [make_msg("g1", "小明", "我对花生过敏，大家点菜注意点", ts=clock.now())]

    res1 = await ing.ingest_messages(msgs)
    assert res1.inserted == 1

    res2 = await ing.ingest_messages(msgs)
    assert res2.inserted == 0
    assert res2.merged == 1

    mems = await store.list_memories(group_id="g1", status=None)
    assert len(mems) == 1  # 没有重复建条
    assert mems[0].recurrence_count == 1
    assert mems[0].importance == max(res1.new_memories and 8 or 0, 8)  # 重要性保持


def test_noise_gate_rules():
    # 低重要性闲聊情节 → 噪声
    assert not passes_noise_gate(
        ExtractedMemory(content="小明说了句哈哈", kind="episodic", importance=1), 3)
    # 有实体 → 保留
    assert passes_noise_gate(
        ExtractedMemory(content="小明提到花生", kind="episodic", importance=1,
                        entities=["花生"]), 3)
    # 稳定类型（偏好/事实/联结）→ 保留
    assert passes_noise_gate(
        ExtractedMemory(content="小明讨厌香菜", kind="preference", importance=1), 3)
    # 重要性达标 → 保留
    assert passes_noise_gate(
        ExtractedMemory(content="群里炸锅了", kind="event", importance=7), 3)


def test_parse_candidates_tolerates_dirty_json():
    data = {
        "memories": [
            {"content": "小明对花生过敏", "kind": "fact", "subject": "小明",
             "importance": "8", "emotion": 4, "keywords": ["花生"], "entities": ["小明"]},
            {"content": "", "kind": "fact"},                       # 空内容 → 丢弃
            {"content": "x" * 600, "kind": "fact"},                # 超长 → 丢弃
            {"content": "奇怪的kind", "kind": "hacker", "importance": "abc"},  # 容错
            "not-a-dict",
        ]
    }
    cands = parse_candidates(data)
    assert len(cands) == 2
    assert cands[0].importance == 8.0
    assert cands[1].kind == "episodic"  # 未知 kind 回退
    assert cands[1].importance == 5.0   # 坏数字回退默认
