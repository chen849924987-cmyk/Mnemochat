"""回复主管线：消息 → 行为判定 → 记忆召回 → 组 prompt → LLM → 回复 → 异步入库。

关键设计：
- 不回复 ≠ 不记忆：所有消息（含机器人自己的发言）都进 ingest 缓冲（旁观学习）；
- ingest 完全异步，绝不阻塞回复；召回是纯本地计算；
- 对话窗口可重置（reset_window），重置后机器人只能依靠长期记忆——
  这是验证"记忆是否真的存活"的试金石。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from chat.behavior import Behavior
from memory.ingest import IngestResult, Ingestor, format_window
from memory.consolidate import ConsolidationResult, Consolidator
from memory.models import MessageRecord
from memory.recall import Recaller, RecallResult
from persona.guard import build_reply_system

if TYPE_CHECKING:
    from chat.llm import ChatBackend
    from config import BotConfig
    from embed.base import EmbeddingProvider
    from memory.store import MemoryStore
    from persona.card import PersonaCard
    from util.clock import Clock

log = logging.getLogger(__name__)


class BotPipeline:
    def __init__(self, store: "MemoryStore", chat: "ChatBackend",
                 embedder: "EmbeddingProvider", persona: "PersonaCard",
                 cfg: "BotConfig", clock: "Clock", group_id: str | None = None) -> None:
        self.store = store
        self.chat = chat
        self.embedder = embedder
        self.persona = persona
        self.cfg = cfg
        self.clock = clock
        self.group_id = group_id or cfg.sim_group_id

        self.ingestor = Ingestor(store, chat, embedder, cfg, clock)
        self.recaller = Recaller(store, embedder, cfg, clock)
        self.consolidator = Consolidator(store, chat, embedder, cfg, clock)
        self.behavior = Behavior(cfg.behavior, [persona.name])

        self._pending: list[MessageRecord] = []
        self._last_ingest_ts = 0.0
        self._window_reset_ts: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
        self.last_ingest_result: IngestResult | None = None
        self.last_consolidation_result: ConsolidationResult | None = None

    # ---------- 对外入口 ----------

    async def on_user_message(self, user_id: str, user_name: str, content: str,
                              *, force_reply: bool = False) -> str | None:
        now = self.clock.now()
        msg_id = await self.store.add_message(self.group_id, user_id, user_name, content, now)
        msg = MessageRecord(id=msg_id, group_id=self.group_id, user_id=user_id,
                            user_name=user_name, content=content, ts=now)
        self._pending.append(msg)

        reply: str | None = None
        if force_reply or self.behavior.should_reply(content):
            reply = await self._reply(msg)

        self._schedule_ingest()
        return reply

    async def _reply(self, trigger: MessageRecord) -> str:
        now = self.clock.now()
        window = await self.store.recent_messages(
            self.group_id, self.cfg.window.recent_turns,
            after_ts=self._window_reset_ts.get(self.group_id),
        )
        recall = await self.recaller.recall(self.group_id, self.persona, window)
        system = build_reply_system(self.persona, recall, self.group_id, now)
        user = self._render_window(window, trigger)
        reply = await self.chat.achat([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])

        # 机器人自己的发言也入日志与 ingest 缓冲（约定、互动都是记忆）
        ts = self.clock.now()
        reply_id = await self.store.add_message(
            self.group_id, self.persona.name, self.persona.name, reply, ts
        )
        self._pending.append(MessageRecord(
            id=reply_id, group_id=self.group_id, user_id=self.persona.name,
            user_name=self.persona.name, content=reply, ts=ts,
        ))
        return reply

    def _render_window(self, window: list[MessageRecord], trigger: MessageRecord) -> str:
        lines = format_window(window)
        return (
            "以下是最近的群聊记录（你自己的发言也在其中）：\n"
            f"{lines}\n\n"
            f"最新一条来自 {trigger.user_name}。请以「{self.persona.name}」的身份回复。"
        )

    # ---------- ingest 调度 ----------

    def _schedule_ingest(self) -> None:
        if (len(self._pending) >= self.cfg.ingest.window_size
                and self.clock.now() - self._last_ingest_ts
                >= self.cfg.ingest.min_interval_seconds):
            msgs = self._pending
            self._pending = []
            self._last_ingest_ts = self.clock.now()
            task = asyncio.create_task(self._ingest_safe(msgs))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _ingest_safe(self, msgs: list[MessageRecord]) -> None:
        try:
            self.last_ingest_result = await self.ingestor.ingest_messages(msgs)
        except Exception:
            log.exception("ingest 任务失败")

    async def flush_ingest(self) -> IngestResult | None:
        """等待在途任务并处理剩余缓冲（时间快进/退出前调用）。"""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if not self._pending:
            return self.last_ingest_result
        msgs = self._pending
        self._pending = []
        await self._ingest_safe(msgs)
        return self.last_ingest_result

    # ---------- 巩固 / 窗口 ----------

    async def consolidate_now(self) -> ConsolidationResult:
        self.last_consolidation_result = await self.consolidator.run()
        return self.last_consolidation_result

    def reset_window(self) -> None:
        """清空对话窗口：此后回复只能依靠长期记忆（验收用）。"""
        self._window_reset_ts[self.group_id] = self.clock.now()
