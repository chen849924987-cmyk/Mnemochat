"""平台适配层：把任意群聊平台的事件翻译成 pipeline 调用。

适配器只做三件事：
1. 收到群消息 → pipeline.on_user_message(group_id, user_id, user_name, content)
2. 拿到回复 → 用平台自己的方式发回去
3. 后台跑定时巩固循环（"睡眠"）
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chat.pipeline import BotPipeline
    from config import BotConfig
    from util.clock import Clock

log = logging.getLogger(__name__)


class GroupChatAdapter:
    def __init__(self, pipeline: "BotPipeline", cfg: "BotConfig", clock: "Clock") -> None:
        self.pipeline = pipeline
        self.cfg = cfg
        self.clock = clock

    async def run(self) -> None:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    async def _consolidation_loop(self) -> None:
        interval = max(60.0, self.cfg.consolidation.interval_minutes * 60)
        while True:
            await asyncio.sleep(interval)
            try:
                result = await self.pipeline.consolidate_now()
                if result.promoted or result.semantic_created or result.superseded:
                    log.info("定时巩固：晋升 %d · 取代 %d · 归纳 %d",
                             result.promoted, result.superseded, result.semantic_created)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("定时巩固失败")
