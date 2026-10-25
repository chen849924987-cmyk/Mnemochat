"""群聊触发策略：机器人什么时候开口。

默认：被 @、被叫名字必回；小概率自然插话；问句可选。
不回复 ≠ 不记忆——所有消息照常进入 ingest 缓冲（旁观学习）。
"""

from __future__ import annotations

import random

from config import BehaviorConfig


class Behavior:
    def __init__(self, cfg: BehaviorConfig, bot_names: list[str]) -> None:
        self.cfg = cfg
        self.names = [n for n in {name for name in bot_names + list(cfg.names) if name}]

    def is_addressed(self, content: str) -> bool:
        """被 @ 或被叫名字：硬触发，必回。"""
        if not self.cfg.reply_on_mention:
            return False
        for name in self.names:
            if name in content or f"@{name}" in content:
                return True
        return False

    def chance_reply(self, content: str, rng: random.Random | None = None) -> bool:
        """纯概率兜底（interest_enabled=false 时的插话策略）。"""
        rng = rng or random
        return self.cfg.reply_probability > 0 and rng.random() < self.cfg.reply_probability

    def should_reply(self, content: str, rng: random.Random | None = None) -> bool:
        """旧接口：地址触发 + 可选问句 + 概率（兼容无兴趣评分的场景）。"""
        rng = rng or random
        if self.is_addressed(content):
            return True
        if self.cfg.reply_on_question and content.rstrip().endswith(("?", "？")):
            return True
        return self.chance_reply(content, rng)
