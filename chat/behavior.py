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

    def should_reply(self, content: str, rng: random.Random | None = None) -> bool:
        rng = rng or random
        if self.cfg.reply_on_mention:
            for name in self.names:
                if name in content or f"@{name}" in content:
                    return True
        if self.cfg.reply_on_question and content.rstrip().endswith(("?", "？")):
            return True
        if self.cfg.reply_probability > 0 and rng.random() < self.cfg.reply_probability:
            return True
        return False
