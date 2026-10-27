"""好友私聊：private:<ID> 虚拟群的记忆隔离与守口如瓶。"""

from memory.models import MessageRecord


async def test_private_memory_sealed_from_group(store, cfg, clock, persona, seed_memory, embedder):
    """守口如瓶：私聊记忆绝不注入群聊——即使 cross_group 开着也强制拦截。"""
    from memory.recall import Recaller

    await seed_memory("私下告诉你的小秘密", group_id="private:10001")
    recaller = Recaller(store, embedder, cfg, clock)
    probe = MessageRecord(group_id="g1", user_id="小明", user_name="小明",
                          content="聊聊秘密", ts=clock.now())

    cfg.private_chat.secrecy_level = 2
    persona.boundary.cross_group = True     # cross_group 也压不过守口如瓶
    rec = await recaller.recall("g1", persona, [probe], reinforce=False)
    assert not any("小秘密" in it.memory.content for it in rec.items)

    cfg.private_chat.secrecy_level = 0      # 口无遮拦：无视 cross_group 直接放行
    rec = await recaller.recall("g1", persona, [probe], reinforce=False)
    assert any("小秘密" in it.memory.content for it in rec.items)

    cfg.private_chat.secrecy_level = 0
    persona.boundary.cross_group = False    # 放行不依赖 cross_group
    rec = await recaller.recall("g1", persona, [probe], reinforce=False)
    assert any("小秘密" in it.memory.content for it in rec.items)


async def test_private_chat_recalls_own_conversation(store, cfg, clock, persona, seed_memory, embedder):
    """私聊会话内：自己的私聊记忆正常召回；tight_lipped 不影响本会话。"""
    from memory.recall import Recaller

    await seed_memory(" privately 说喜欢猫", group_id="private:10001",
                      entities=["猫"])
    recaller = Recaller(store, embedder, cfg, clock)
    cfg.private_chat.tight_lipped = True
    probe = MessageRecord(group_id="private:10001", user_id="10001",
                          user_name="路人", content="我家的猫", ts=clock.now())
    rec = await recaller.recall("private:10001", persona, [probe], reinforce=False)
    assert any("猫" in it.memory.content for it in rec.items)


def test_parse_private_message():
    """OneBot 私聊事件 → private:<QQ号> 虚拟群消息；群事件不误判。"""
    from adapter.onebot import parse_private_message, parse_group_message

    ev = {"post_type": "message", "message_type": "private", "user_id": 10001,
          "message": [{"type": "text", "data": {"text": "在吗"}}],
          "sender": {"nickname": "路人"}}
    gm = parse_private_message(ev)
    assert gm is not None and gm.private
    assert gm.group_id == "private:10001" and gm.private_user_id == "10001"
    assert gm.text == "在吗" and gm.mentioned_bot

    group_ev = {"post_type": "message", "message_type": "group", "group_id": 555,
                "user_id": 10001, "message": [{"type": "text", "data": {"text": "hi"}}]}
    assert parse_private_message(group_ev) is None
    assert parse_group_message(ev, None) is None       # 群解析器不吞私聊


async def test_secrecy_level_1_probabilistic_leak(store, cfg, clock, persona,
                                                  seed_memory, embedder, monkeypatch):
    """偶尔说漏嘴：骰子没中 → 拦住；中了 → 放行进群聊上下文。"""
    from memory.recall import Recaller

    await seed_memory("私下说想学吉他", group_id="private:10001")
    recaller = Recaller(store, embedder, cfg, clock)
    probe = MessageRecord(group_id="g1", user_id="小明", user_name="小明",
                          content="吉他", ts=clock.now())
    persona.boundary.cross_group = False    # 说漏不走 cross_group，走骰子
    cfg.private_chat.secrecy_level = 1

    monkeypatch.setattr("random.random", lambda: 0.9)
    rec = await recaller.recall("g1", persona, [probe], reinforce=False)
    assert not any("吉他" in it.memory.content for it in rec.items)

    monkeypatch.setattr("random.random", lambda: 0.1)
    rec = await recaller.recall("g1", persona, [probe], reinforce=False)
    assert any("吉他" in it.memory.content for it in rec.items)


def test_secrecy_legacy_compat():
    """旧字段 tight_lipped 仍兼容：false → 0 档；显式 secrecy_level 优先。"""
    from config import PrivateChatConfig

    assert PrivateChatConfig().effective_secrecy == 2
    assert PrivateChatConfig(tight_lipped=False).effective_secrecy == 0
    assert PrivateChatConfig(tight_lipped=False, secrecy_level=1).effective_secrecy == 1
