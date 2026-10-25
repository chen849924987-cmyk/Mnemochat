"""兴趣度评分：像真人一样决定"要不要接这句话"。

@机器人 / 叫名字 → 必回（在评分之前，见 pipeline）。
其余消息按兴趣分决定，纯本地计算（零 LLM 调用、毫秒级）：

    兴趣分 = 话题基础 + 问句 + 角色兴趣词命中 + 记忆相关 + 情绪抖动 - 冷却惩罚

- 角色兴趣词来自角色卡 interests（修改器可调）——每张人格有自己的兴趣；
- 记忆相关 = 消息提到了机器人记得的实体（人/事）——"对自己记得的东西感兴趣"，
  这正是人的行为方式；
- 情绪抖动 = 0~jitter 的随机分，让同样的消息有时接有时不接，避免机械感；
- 冷却 = 刚回复过则提不起兴致（防刷屏，也像真人）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import BotConfig
    from memory.store import MemoryStore
    from persona.card import PersonaCard
    from util.clock import Clock


@dataclass
class InterestVerdict:
    score: float = 0.0
    factors: dict[str, float] = field(default_factory=dict)
    will_reply: bool = False


class InterestScorer:
    def __init__(self, store: "MemoryStore", cfg: "BotConfig", clock: "Clock",
                 rng: random.Random | None = None) -> None:
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.rng = rng or random.Random()
        self._last_reply: dict[str, float] = {}   # group_id -> 上次回复时刻

    def note_replied(self, group_id: str) -> None:
        """任何原因的回复都应通知：刚说过话，兴致下降。"""
        self._last_reply[group_id] = self.clock.now()

    async def score(self, group_id: str, content: str,
                    persona: "PersonaCard") -> InterestVerdict:
        b = self.cfg.behavior
        factors: dict[str, float] = {}

        if len(content.strip()) >= b.interest_min_length:
            factors["话题"] = 10
        if content.rstrip().endswith(("?", "？")):
            factors["问句"] = b.interest_question_bonus

        hits = [k for k in persona.interests if k and k in content]
        if hits:
            factors["兴趣"] = min(40.0, 20.0 * len(hits))

        try:
            names = await self.store.entity_names(group_id)
            if any(n and n in content for n in names):
                factors["记忆相关"] = b.interest_memory_bonus
        except Exception:
            pass

        jitter = self.rng.random() * b.interest_jitter
        factors["情绪"] = round(jitter)
        score = sum(factors.values())

        last = self._last_reply.get(group_id)
        if (last is not None
                and self.clock.now() - last < b.interest_cooldown_seconds):
            factors["冷却"] = -float(b.interest_cooldown_penalty)
            score -= b.interest_cooldown_penalty

        return InterestVerdict(score=score, factors=factors,
                               will_reply=score >= b.interest_threshold)
