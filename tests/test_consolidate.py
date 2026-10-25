"""巩固管线：晋升规则、矛盾取代、睡眠巩固（情节→语义）。"""

from memory.consolidate import Consolidator


def _consolidator(store, chat, embedder, cfg, clock):
    return Consolidator(store, chat, embedder, cfg, clock)


async def test_promotion_rules(store, chat, embedder, cfg, clock, seed_memory):
    day = 86400.0
    now = clock.now()
    await seed_memory("小明的考研报名开始了", kind="event",
                      stage="staging", created_at=now - 25 * 3600)          # 达标：够老+重要
    await seed_memory("小明刚说的重要事", kind="event", importance=9,
                      stage="staging", created_at=now - 2 * 3600)           # 太新
    await seed_memory("群里无关紧要的闲话", kind="event", importance=3,
                      stage="staging", created_at=now - 25 * 3600)          # 够老但无足轻重
    await seed_memory("小红的小小偏好", kind="event", importance=3,
                      stage="staging", created_at=now - 25 * 3600,
                      recurrence_count=1)                                    # 复现过 → 晋升

    c = _consolidator(store, chat, embedder, cfg, clock)
    result = await c.run()
    assert result.promoted == 2

    ltm = await store.list_memories(stage="ltm")
    contents = {m.content for m in ltm}
    assert "小明的考研报名开始了" in contents
    assert "小红的小小偏好" in contents
    staging_left = {m.content for m in await store.list_memories(stage="staging")}
    assert "小明刚说的重要事" in staging_left
    assert "群里无关紧要的闲话" in staging_left  # 不删除，留在筛选区


async def test_contradiction_supersedes_old_fact(
    store, chat, embedder, cfg, clock, seed_memory
):
    day = 86400.0
    now = clock.now()
    old = await seed_memory("小明爱吃辣", kind="fact", subject_user_id="小明",
                            created_at=now - 30 * day)
    new = await seed_memory("小明现在不吃辣了", kind="fact", subject_user_id="小明",
                            stage="staging", created_at=now - 25 * 3600)

    c = _consolidator(store, chat, embedder, cfg, clock)
    result = await c.run()

    assert result.promoted == 1
    assert result.superseded == 1
    old_after = await store.get_memory(old.id)
    assert old_after.status == "superseded"
    assert old_after.superseded_by == new.id
    new_after = await store.get_memory(new.id)
    assert new_after.status == "active" and new_after.stage == "ltm"
    # 旧事实仍在库中（可溯源），只是不再注入
    assert old_after.content == "小明爱吃辣"


async def test_sleep_consolidation_clusters_episodes(
    store, chat, embedder, cfg, clock, seed_memory
):
    cfg.consolidation.cluster_similarity = 0.60
    now = clock.now()
    m1 = await seed_memory("小明讨厌香菜", kind="episodic",
                           created_at=now - 10 * 86400)
    m2 = await seed_memory("小明讨厌吃香菜", kind="episodic",
                           created_at=now - 5 * 86400)
    # 无关记忆不应被卷入聚类
    await seed_memory("小红换了新工作", kind="episodic",
                      created_at=now - 8 * 86400)

    c = _consolidator(store, chat, embedder, cfg, clock)
    result = await c.run()

    assert result.semantic_created == 1
    semantic = [m for m in await store.list_memories(kind="semantic")]
    assert len(semantic) == 1
    assert "香菜" in semantic[0].content
    assert semantic[0].stage == "ltm"

    a1 = await store.get_memory(m1.id)
    a2 = await store.get_memory(m2.id)
    assert a1.status == "archived" and a1.archived_into == semantic[0].id
    assert a2.status == "archived" and a2.archived_into == semantic[0].id
    # 源记忆内容仍完整保留（只归档不删除）
    assert a1.content == "小明讨厌香菜"

    # 幂等：再跑一次不会重复归纳（源已归档，不再是 active 情节）
    result2 = await c.run()
    assert result2.semantic_created == 0
