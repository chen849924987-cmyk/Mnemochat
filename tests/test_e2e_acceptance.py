"""端到端验收：群聊 → 提取入库 → 两周后巩固 → 清空窗口 → 仅凭长期记忆召回。

这是整套系统的存在意义：时间流逝、对话窗口清空之后，人格还记得。
"""

import pytest

from chat.pipeline import BotPipeline
from embed.mock import HashEmbedder
from memory.models import MemoryRecord


async def test_remember_allergy_after_two_weeks(
    store, chat, embedder, cfg, clock, persona
):
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock, group_id="g1")

    # ---- 第一天：小明随口提到过敏 ----
    reply1 = await pipe.on_user_message(
        "小明", "小明", "我对花生过敏，大家点菜注意点", force_reply=True
    )
    assert reply1  # mock 也会回复
    await pipe.flush_ingest()

    mems = await store.list_memories(group_id="g1", stage="staging")
    assert len(mems) == 1
    assert "花生" in mems[0].content and "小明" in mems[0].content
    mem_id, s0 = mems[0].id, mems[0].strength

    # ---- 两周过去：时间快进 + 巩固（晋升 LTM）----
    clock.warp(14 * 86400)
    await pipe.flush_ingest()
    result = await pipe.consolidate_now()
    assert result.promoted == 1

    # ---- 清空对话窗口：机器人"失忆"了眼前的事 ----
    pipe.reset_window()

    # ---- 小明再聊吃的：只能靠长期记忆 ----
    reply2 = await pipe.on_user_message(
        "小明", "小明", "小满，今晚聚餐吃什么好", force_reply=True
    )

    # 回忆块注入了 system prompt，且带相对时间
    system = chat.last_chat_messages[0]["content"]
    assert "[你的回忆" in system
    assert "花生" in system
    assert "约两周前" in system

    # mock 回复显式引用了记忆（真实模型则表现为自然想起）
    assert "花生" in reply2

    # 检索即强化生效：访问+1、强度增长、衰减时钟重置
    mem = await store.get_memory(mem_id)
    assert mem.stage == "ltm"
    assert mem.access_count == 1
    assert mem.strength > s0
    assert mem.last_accessed_at == pytest.approx(clock.now(), abs=5)


async def test_bot_quiet_when_not_triggered(store, chat, embedder, cfg, clock, persona):
    """不点名时机器人旁观（不回复），但消息照常入库等待提取。"""
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock, group_id="g1")
    cfg.behavior.reply_probability = 0.0

    reply = await pipe.on_user_message("小红", "小红", "今天天气不错呀")
    assert reply is None
    await pipe.flush_ingest()
    # 消息进入了日志（旁观学习）
    msgs = await store.recent_messages("g1", limit=10)
    assert any(m.content == "今天天气不错呀" for m in msgs)


async def test_provider_switch_preserves_old_vectors(store, embedder, seed_memory):
    """切换嵌入模型：旧向量保留，新模型增量补齐后可正常检索。"""

    class OtherEmbedder(HashEmbedder):
        def __init__(self):
            super().__init__(96)
            self.name = "mock:other"

    m1 = await seed_memory("小明对花生过敏", entities=["小明"])
    m2 = await seed_memory("小红喜欢打篮球")

    other = OtherEmbedder()
    missing = await store.memories_without_embedding(other.name)
    assert set(missing) == {m1.id, m2.id}

    # 模拟 --reembed 流程
    for mid in missing:
        row = await store.get_memory(mid)
        vec = (await other.embed([row.content]))[0]
        await store.save_embedding(mid, other.name, vec)

    assert await store.memories_without_embedding(other.name) == []
    # 旧模型向量未受影响
    assert await store.get_embedding(m1.id, embedder.name) is not None
    # 新模型可检索
    q = (await other.embed(["花生过敏"]))[0]
    hits = await store.vector_topk(other.name, q, k=2)
    assert hits and hits[0][0] == m1.id
    # stats 中两个模型并存
    stats = await store.stats()
    assert set(stats["embeddings"]) == {embedder.name, other.name}
