"""多群隔离：同一管线服务多个群，记忆与窗口互不串扰。"""

from chat.pipeline import BotPipeline
from memory.models import MessageRecord


async def test_two_groups_isolated(store, chat, embedder, cfg, clock, persona):
    cfg.behavior.reply_probability = 0.0
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock)

    await pipe.on_user_message(
        "g1", "小明", "小明", "我对花生过敏，大家点菜注意点", force_reply=True
    )
    await pipe.on_user_message("g2", "小红", "小红", "大家好呀")
    await pipe.flush_ingest()

    # g1 产生了记忆，g2 没有
    mems_g1 = await store.list_memories(group_id="g1")
    mems_g2 = await store.list_memories(group_id="g2")
    assert len(mems_g1) == 1 and len(mems_g2) == 0

    # g2 里小明同名出现，知识边界拦截 g1 的群内记忆
    probe = [MessageRecord(group_id="g2", user_id="小明", user_name="小明",
                           content="今晚吃什么好", ts=clock.now())]
    rec = await pipe.recaller.recall("g2", persona, probe, reinforce=False)
    assert not any("花生" in item.memory.content for item in rec.items), \
        "跨群记忆不应泄漏"

    # 窗口重置按群隔离
    pipe.reset_window("g1")
    w1 = await store.recent_messages("g1", limit=10)
    assert w1  # g1 仍有历史消息（重置只影响组 prompt 的截断点）
    w2 = await store.recent_messages("g2", limit=10)
    assert any(m.content == "大家好呀" for m in w2)
    assert pipe._window_reset_ts.get("g2") is None


async def test_pending_buffers_per_group(store, chat, embedder, cfg, clock, persona):
    cfg.behavior.reply_probability = 0.0
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock)
    await pipe.on_user_message("g1", "小明", "小明", "g1的消息")
    await pipe.on_user_message("g2", "小红", "小红", "g2的消息")
    assert set(pipe._pending.keys()) == {"g1", "g2"}
    await pipe.flush_ingest()
    assert all(not v for v in pipe._pending.values())
