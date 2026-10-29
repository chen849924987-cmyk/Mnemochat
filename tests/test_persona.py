"""人格层：知识边界、注入格式、system prompt 锚定。"""

from memory.models import MemoryRecord
from memory.recall import RecallResult, ScoredMemory
from persona.card import PersonaCard
from persona.guard import build_reply_system, render_memory_block


def _mem(**kw) -> MemoryRecord:
    params = dict(content="内容", group_id="g1", stage="ltm",
                  created_at=1_000_000.0, strength=10.0,
                  last_accessed_at=1_000_000.0 + 14 * 86400 * 0)
    params.update(kw)
    return MemoryRecord(**params)


def test_allows_matrix(persona):
    same_group = _mem(visibility="group", group_id="g1")
    other_group = _mem(visibility="group", group_id="g2")
    glob = _mem(visibility="global", group_id="g2")
    own = _mem(visibility="self", group_id="g1")

    assert persona.allows(same_group, "g1") is True
    assert persona.allows(other_group, "g1") is False      # 跨群拦截
    assert persona.allows(glob, "g1") is True              # 全局知识放行
    assert persona.allows(own, "g1") is True               # self 默认可用

    persona.boundary.cross_group = True
    assert persona.allows(other_group, "g1") is True       # 卡片显式放行后才可见
    persona.boundary.include_self = False
    assert persona.allows(own, "g1") is False


def test_render_memory_block_relative_time(persona):
    now = 1_000_000.0 + 14 * 86400  # 两周后
    mem = _mem(content="小明对花生过敏", last_accessed_at=1_000_000.0)
    recall = RecallResult(items=[ScoredMemory(memory=mem, score=0.9)], query_text="q")
    block = render_memory_block(recall, persona, now)
    assert "[你的回忆" in block
    assert "约两周前" in block
    assert "小明对花生过敏" in block
    assert "【记忆使用准则】" in block


def test_render_empty_recall_is_empty(persona):
    assert render_memory_block(RecallResult([], ""), persona, 0.0) == ""


def test_build_reply_system_always_anchors_identity(persona, clock):
    now = clock.now()
    # 有记忆：人格 + 回忆块
    mem = _mem(content="小明对花生过敏")
    recall = RecallResult(items=[ScoredMemory(memory=mem, score=0.9)], query_text="q")
    system = build_reply_system(persona, recall, "g1", now)
    assert persona.identity.splitlines()[1].strip()[:10] in system or "小满" in system
    assert "[你的回忆" in system

    # 无记忆：人格依旧完整注入（人格永不衰减）
    system2 = build_reply_system(persona, RecallResult([], ""), "g1", now)
    assert "小满" in system2
    assert "[你的回忆" not in system2


def test_catchphrase_frequency_and_examples():
    """口癖降频（偏好而非每句必带）+ 参考对话块渲染。"""
    card = PersonaCard(
        name="小满", identity="你是小满",
        catchphrases=["诶", "我记得呀"],
        examples=["用户：早上好呀" + chr(10) + "小满：早上好~今天也元气满满哦"],
    )
    prompt = card.build_system_prompt("g1", 0.0)
    assert "自然穿插" in prompt
    assert "把口头禅硬塞进每句话 = 失败" in prompt
    assert "【参考对话" in prompt and "元气满满" in prompt

    plain = PersonaCard(name="小满", identity="你是小满")
    p2 = plain.build_system_prompt("g1", 0.0)
    assert "【口头禅" not in p2 and "【参考对话" not in p2
