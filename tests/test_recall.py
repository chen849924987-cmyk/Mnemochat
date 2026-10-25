"""检索路径：混合召回、过滤规则、人格边界、检索即强化。"""

import pytest

from memory.recall import Recaller


def _recaller(store, embedder, cfg, clock):
    return Recaller(store, embedder, cfg, clock)


async def test_recall_finds_memory_and_reinforces(
    store, embedder, cfg, clock, persona, make_msg, seed_memory
):
    mem = await seed_memory("小明对花生过敏", entities=["小明", "花生"],
                            keywords=["花生", "过敏"])
    r = _recaller(store, embedder, cfg, clock)

    msgs = [make_msg("g1", "小明", "今晚聚餐吃什么好", ts=clock.now())]
    result = await r.recall("g1", persona, msgs)

    assert result.items, "应当召回花生过敏记忆"
    assert result.items[0].memory.id == mem.id
    assert result.items[0].factors["similarity"] > 0

    got = await store.get_memory(mem.id)
    assert got.access_count == 1                       # 检索即强化
    assert got.strength > mem.strength                 # S×1.25
    assert got.last_accessed_at == pytest.approx(clock.now(), abs=5)


async def test_recall_reinforce_false_leaves_memory_untouched(
    store, embedder, cfg, clock, persona, make_msg, seed_memory
):
    await seed_memory("小明对花生过敏", entities=["小明"])
    r = _recaller(store, embedder, cfg, clock)
    msgs = [make_msg("g1", "小明", "今晚吃什么", ts=clock.now())]
    result = await r.recall("g1", persona, msgs, reinforce=False)
    assert result.items
    mem = result.items[0].memory
    assert mem.access_count == 0


async def test_recall_excludes_superseded_and_fresh_staging(
    store, embedder, cfg, clock, persona, make_msg, seed_memory
):
    await seed_memory("小明对花生过敏", entities=["小明"])
    old = await seed_memory("小明爱吃辣", entities=["小明"], status="superseded")
    fresh = await seed_memory("小明刚说了个笑话", entities=["小明"],
                              stage="staging", created_at=clock.now())

    r = _recaller(store, embedder, cfg, clock)
    msgs = [make_msg("g1", "小明", "聊聊小明的口味和近况", ts=clock.now())]
    result = await r.recall("g1", persona, msgs)

    ids = {item.memory.id for item in result.items}
    assert old.id not in ids, "被取代的事实不应注入"
    assert fresh.id not in ids, "太新的 staging（仍在对话窗口内）不应注入"


async def test_persona_boundary_blocks_other_group(
    store, embedder, cfg, clock, persona, make_msg, seed_memory
):
    await seed_memory("小明对花生过敏", entities=["小明"])
    secret = await seed_memory("小明的群外秘密只有g2知道", entities=["小明"],
                               group_id="g2", kind="episodic")

    r = _recaller(store, embedder, cfg, clock)
    msgs = [make_msg("g1", "小明", "说说你知道的关于小明的事", ts=clock.now())]

    # 默认：跨群记忆被知识边界拦截
    result = await r.recall("g1", persona, msgs)
    ids = {item.memory.id for item in result.items}
    assert secret.id not in ids

    # 角色卡开启跨群共享后放行
    persona.boundary.cross_group = True
    result2 = await r.recall("g1", persona, msgs)
    ids2 = {item.memory.id for item in result2.items}
    assert secret.id in ids2


async def test_global_memory_visible_across_groups(
    store, embedder, cfg, clock, persona, make_msg, seed_memory
):
    world = await seed_memory("今天全国下雨", entities=[], group_id="g9",
                              visibility="global", kind="event")
    r = _recaller(store, embedder, cfg, clock)
    msgs = [make_msg("g1", "小红", "今天全国下雨是真的吗", ts=clock.now())]
    result = await r.recall("g1", persona, msgs)
    assert world.id in {item.memory.id for item in result.items}


async def test_retention_decays_ranking_over_time(
    store, embedder, cfg, clock, persona, make_msg, seed_memory
):
    """同样相关的情况下，久未想起的记忆排名应低于常被想起的记忆。"""
    fresh = await seed_memory("小明喜欢打羽毛球", entities=["小明"], kind="preference")
    import math
    from memory import decay as decay_mod

    old = await seed_memory("小明喜欢游泳", entities=["小明"], kind="preference")
    await store.update_memory(
        old.id,
        strength=2.0,
        last_accessed_at=clock.now() - 30 * 86400,  # 一个月没被想起
    )

    r = _recaller(store, embedder, cfg, clock)
    msgs = [make_msg("g1", "小明", "我喜欢什么运动来着", ts=clock.now())]
    result = await r.recall("g1", persona, msgs, reinforce=False)
    assert len(result.items) >= 2
    top = result.items[0].memory.id
    assert top == fresh.id, "新鲜记忆应排在前"
    r_old = decay_mod.retention(30.0, 2.0)
    assert r_old < math.exp(-30 / 10)  # 远低于常规强度记忆
