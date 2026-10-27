"""注入端拟人化：分级压缩、人保底、核心记忆钉。"""

from memory.models import MessageRecord
from memory.recall import Recaller


async def _recall(recaller, store, cfg, clock, persona, query):
    probe = MessageRecord(group_id="g1", user_id="小明",
                          user_name="小明", content=query, ts=clock.now())
    return await recaller.recall("g1", persona, [probe], reinforce=False)


async def test_graded_compression(store, cfg, clock, persona, seed_memory, embedder):
    """新鲜记忆注入原文；R 低的老记忆降级为"记得大概/关键词"，存储原文不动。"""
    fresh = await seed_memory("小明报名了下周的摄影比赛")
    old_kw = await seed_memory("小明的硬盘修复记录", keywords=["硬盘", "修复"])
    old_vague = await seed_memory("小红去年夏天在青岛海边拍日落")
    for mem, days in ((old_kw, 120), (old_vague, 200)):
        await store.update_memory(mem.id, last_accessed_at=clock.now() - days * 86400,
                                  strength=3.0)
    recaller = Recaller(store, embedder, cfg, clock)
    rec = await _recall(recaller, store, cfg, clock, persona, "小明 摄影 硬盘 修复")

    by_id = {it.memory.id: it for it in rec.items}
    assert by_id[fresh.id].display is None                       # 新鲜 → 原文
    assert by_id[old_kw.id].display.startswith("（印象模糊")      # 极老 → 关键词
    assert "硬盘" in by_id[old_kw.id].display
    assert by_id[old_vague.id].display.startswith("（记得大概）")  # 很老 → 一句
    # 存储层原文一字不动
    assert (await store.get_memory(old_kw.id)).content == "小明的硬盘修复记录"


async def test_person_priority_keeps_seat(store, cfg, clock, persona,
                                          seed_memory, embedder):
    """人保底：关于具体的人的记忆挤不出去——即使相似度排名垫底。"""
    from memory.models import MemoryRecord

    persona.interests = []
    for i in range(6):
        await seed_memory(f"量子物理研讨会第{i}期的安排细则", subject_user_id=None)
    await seed_memory("小红怕打雷", subject_user_id="小红")         # 关于人的记忆，与查询无关
    cfg.recall.top_k = 4
    recaller = Recaller(store, embedder, cfg, clock)
    rec = await _recall(recaller, store, cfg, clock, persona, "量子物理研讨会安排")

    subjects = [it.memory.subject_user_id for it in rec.items]
    assert "小红" in subjects                                  # 保底席位生效
    assert len(rec.items) == cfg.recall.top_k                  # 总席位不变


async def test_core_memory_block(store, cfg, clock, persona, seed_memory, embedder):
    """核心记忆钉：关系/偏好/关于人的记忆每轮必带；跨群按边界、私聊按守口如瓶。"""
    recaller = Recaller(store, embedder, cfg, clock)
    await seed_memory("小红是机器人最信任的朋友", kind="bond", importance=9)
    await seed_memory("小明无辣不欢", kind="preference", importance=8)
    await seed_memory("上周群聚餐吃了火锅", subject_user_id=None)  # 普通事件，不进核心

    block = await recaller.core_block("g1", persona)
    assert "核心记忆" in block and "信任的朋友" in block and "无辣不欢" in block
    assert "火锅" not in block

    # 跨群隔离：g2 的核心钉看不到 g1 的群内记忆；global 可见性才通行
    g2 = await recaller.core_block("g2", persona)
    assert "信任的朋友" not in g2

    # 私聊记忆：守口如瓶时不进其他会话的核心钉
    await seed_memory("私下说喜欢弹吉他", kind="preference", group_id="private:10001",
                      importance=9)
    cfg.private_chat.secrecy_level = 2
    assert "吉他" not in await recaller.core_block("g1", persona)
    cfg.private_chat.secrecy_level = 0
    assert "吉他" in await recaller.core_block("g1", persona)

    # 私聊会话自己的核心钉正常
    own = await recaller.core_block("private:10001", persona)
    assert "吉他" in own
