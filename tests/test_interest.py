"""兴趣度评分：决定"要不要接这句话"。"""

import random

from chat.interest import InterestScorer
from chat.pipeline import BotPipeline


def _scorer(store, cfg, clock, seed=0) -> InterestScorer:
    return InterestScorer(store, cfg, clock, rng=random.Random(seed))


async def test_chitchat_stays_silent(store, cfg, clock, persona):
    s = _scorer(store, cfg, clock, seed=1)
    v = await s.score("g1", "哈哈哈", persona)      # 太短，不构成话题
    assert not v.will_reply and v.score < cfg.behavior.interest_threshold


async def test_interest_keywords_and_question(store, cfg, clock, persona):
    persona.interests = ["AI", "模型"]
    s = _scorer(store, cfg, clock, seed=2)
    v = await s.score("g1", "这个新出的AI模型怎么回事，靠谱吗？", persona)
    assert v.factors.get("兴趣") == 40           # 两个兴趣词命中封顶
    assert v.factors.get("问句") == cfg.behavior.interest_question_bonus
    assert v.score >= cfg.behavior.interest_threshold
    assert v.will_reply


async def test_memory_relevance_boosts(store, cfg, clock, persona, seed_memory, embedder):
    await seed_memory("小明对花生过敏", entities=["小明", "花生"])
    s = _scorer(store, cfg, clock, seed=3)
    v = await s.score("g1", "小明最近还好吗？", persona)
    assert v.factors.get("记忆相关") == cfg.behavior.interest_memory_bonus
    assert v.will_reply


async def test_cooldown_kills_mood(store, cfg, clock, persona):
    persona.interests = ["AI"]
    s = _scorer(store, cfg, clock, seed=4)
    v1 = await s.score("g1", "聊聊AI吧？", persona)
    assert v1.will_reply
    s.note_replied("g1")
    v2 = await s.score("g1", "再聊聊AI吧？", persona)
    assert v2.factors.get("冷却") == -cfg.behavior.interest_cooldown_penalty
    assert not v2.will_reply


async def test_pipeline_interest_flow(store, chat, embedder, cfg, clock, persona):
    """整合：兴趣高→插话；冷却→潜水；@→必回不受影响。"""
    cfg.behavior.reply_probability = 0.0
    persona.interests = ["量子物理"]
    pipe = BotPipeline(store, chat, embedder, persona, cfg, clock)

    # 兴趣词+问句：话题10 + 兴趣20 + 问句20 ≥ 50 ≥ 45 → 主动接话
    r1 = await pipe.on_user_message("g1", "小明", "小明", "有人研究过量子物理吗？")
    assert r1 is not None

    # 紧接着相似话题：冷却惩罚压制 → 潜水，且能看到评分明细
    r2 = await pipe.on_user_message("g1", "小红", "小红", "量子物理好难学啊？")
    assert r2 is None
    assert pipe.last_interest is not None
    assert pipe.last_interest.factors.get("冷却")

    # @必回优先级最高，不受冷却影响
    r3 = await pipe.on_user_message("g1", "小明", "小明", f"@{persona.name} 在吗")
    assert r3 is not None
