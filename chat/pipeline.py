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
import os
from pathlib import Path
from typing import TYPE_CHECKING

from chat.behavior import Behavior
from chat.interest import InterestScorer, InterestVerdict
from memory.ingest import IngestResult, Ingestor, format_window
from memory.consolidate import ConsolidationResult, Consolidator
from memory.models import MessageRecord
from memory.recall import Recaller, RecallResult
from persona.drift import DEFAULT_RE_ANCHOR, DriftDetector, DriftReport
from persona.guard import build_reply_system

if TYPE_CHECKING:
    from chat.llm import ChatBackend
    from config import BotConfig
    from embed.base import EmbeddingProvider
    from memory.store import MemoryStore
    from persona.card import PersonaCard
    from stickers.library import StickerLibrary
    from stickers.tagger import StickerTagger, VisionDescriber
    from util.clock import Clock

log = logging.getLogger(__name__)


class BotPipeline:
    def __init__(self, store: "MemoryStore", chat: "ChatBackend",
                 embedder: "EmbeddingProvider", persona: "PersonaCard",
                 cfg: "BotConfig", clock: "Clock",
                 persona_file: str | Path | None = None,
                 sticker_lib: "StickerLibrary | None" = None,
                 vision: "VisionDescriber | None" = None,
                 mock: bool = False) -> None:
        self.store = store
        self.chat = chat
        self.embedder = embedder
        self.persona = persona
        self.cfg = cfg
        self.clock = clock
        # 人格卡热重载：修改器保存后，下一条消息自动生效（人格永远在线更新）
        self.persona_file = Path(persona_file) if persona_file else None
        self._persona_mtime = (
            self.persona_file.stat().st_mtime
            if self.persona_file and self.persona_file.exists() else None
        )

        self.ingestor = Ingestor(store, chat, embedder, cfg, clock)
        self.recaller = Recaller(store, embedder, cfg, clock)
        self.consolidator = Consolidator(store, chat, embedder, cfg, clock)
        self.behavior = Behavior(cfg.behavior, [persona.name])
        self._interest = InterestScorer(store, cfg, clock)
        self.last_interest: InterestVerdict | None = None

        # 多群安全：每群独立的待入库缓冲与窗口重置点
        self._pending: dict[str, list[MessageRecord]] = {}
        self._last_ingest_ts = 0.0
        self._window_reset_ts: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
        self.last_ingest_result: IngestResult | None = None
        self.last_consolidation_result: ConsolidationResult | None = None
        self.last_drift_report: DriftReport | None = None
        self.last_stickers_pruned: int = 0   # 每轮遗忘清理的表情包数
        self.drift_hint: str | None = None   # 漂移期间注入的角色校准提醒
        self._drift = (DriftDetector(store, chat, persona, cfg, clock)
                       if cfg.drift.enabled else None)
        # 表情包子系统（可选）
        self.sticker_lib = sticker_lib
        # 独立表情包共享库（per_persona_memory / sticker.database 分库时非空，由入口设置）
        self.sticker_store = None
        self._sticker_tagger = None
        # 串门：适配器可注入群发送器（QQ 顶号模式：客人发言经同一账号发出）
        self.mock = mock
        self.social_sender = None
        if sticker_lib is not None:
            from stickers.tagger import StickerTagger

            self._sticker_tagger = StickerTagger(sticker_lib, chat, vision, cfg, clock)

    # ---------- 对外入口 ----------

    async def on_user_message(self, group_id: str, user_id: str, user_name: str,
                              content: str, *, force_reply: bool = False) -> str | None:
        self._maybe_reload_persona()
        now = self.clock.now()
        msg_id = await self.store.add_message(group_id, user_id, user_name, content, now)
        msg = MessageRecord(id=msg_id, group_id=group_id, user_id=user_id,
                            user_name=user_name, content=content, ts=now)
        self._pending.setdefault(group_id, []).append(msg)

        # 回复决策：@/叫名字必回 → 问句（配置）→ 兴趣评分 → 概率兜底
        reply: str | None = None
        if force_reply or self.behavior.is_addressed(content):
            reply = await self._reply(group_id, msg)
        elif self.cfg.behavior.reply_on_question and content.rstrip().endswith(("?", "？")):
            reply = await self._reply(group_id, msg)
        elif self.cfg.behavior.interest_enabled:
            verdict = await self._interest.score(group_id, content, self.persona)
            self.last_interest = verdict
            if verdict.will_reply:
                reply = await self._reply(group_id, msg)
        elif self.behavior.chance_reply(content):
            reply = await self._reply(group_id, msg)
        if reply is not None:
            self._interest.note_replied(group_id)

        self._schedule_ingest(group_id)
        return reply

    def _maybe_reload_persona(self) -> None:
        """角色卡文件变化 → 热重载人格与唤醒词。"""
        if not self.persona_file or not self.persona_file.exists():
            return
        try:
            mtime = self.persona_file.stat().st_mtime
        except OSError:
            return
        if mtime == self._persona_mtime:
            return
        self._persona_mtime = mtime
        try:
            from persona.card import PersonaCard

            self.persona = PersonaCard.load_yaml(self.persona_file)
            self.behavior = Behavior(self.cfg.behavior, [self.persona.name])
            self._drift = (DriftDetector(self.store, self.chat, self.persona,
                                         self.cfg, self.clock)
                           if self.cfg.drift.enabled and self._drift is not None
                           else self._drift)
            log.info("人格卡已热重载：%s", self.persona.name)
        except Exception:
            log.exception("人格卡热重载失败，沿用旧版本")

    async def _reply(self, group_id: str, trigger: MessageRecord) -> str:
        now = self.clock.now()
        window = await self.store.recent_messages(
            group_id, self.cfg.window.recent_turns,
            after_ts=self._window_reset_ts.get(group_id),
        )
        recall = await self.recaller.recall(group_id, self.persona, window)
        system = build_reply_system(self.persona, recall, group_id, now,
                                    extra_hint=self.drift_hint or "")
        if self.sticker_lib is not None:
            block = await self.sticker_lib.prompt_block(group_id)
            if block:
                system = f"{system}\n\n{block}"
        user = self._render_window(window, trigger)
        reply = await self.chat.achat([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])

        # 机器人自己的发言也入日志与 ingest 缓冲（约定、互动都是记忆）
        ts = self.clock.now()
        reply_id = await self.store.add_message(
            group_id, self.persona.name, self.persona.name, reply, ts
        )
        self._pending.setdefault(group_id, []).append(MessageRecord(
            id=reply_id, group_id=group_id, user_id=self.persona.name,
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

    def _schedule_ingest(self, group_id: str) -> None:
        pending = self._pending.get(group_id)
        if (pending is not None and len(pending) >= self.cfg.ingest.window_size
                and self.clock.now() - self._last_ingest_ts
                >= self.cfg.ingest.min_interval_seconds):
            self._pending[group_id] = []
            self._last_ingest_ts = self.clock.now()
            task = asyncio.create_task(self._ingest_safe(pending))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _ingest_safe(self, msgs: list[MessageRecord]) -> None:
        try:
            self.last_ingest_result = await self.ingestor.ingest_messages(msgs)
        except Exception:
            log.exception("ingest 任务失败")

    async def flush_ingest(self) -> IngestResult | None:
        """等待在途任务并处理所有群的剩余缓冲（退出/时间快进前调用）。"""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for group_id, msgs in list(self._pending.items()):
            if msgs:
                self._pending[group_id] = []
                await self._ingest_safe(msgs)
        return self.last_ingest_result

    # ---------- 巩固 / 窗口 ----------

    async def consolidate_now(self) -> ConsolidationResult:
        self.last_consolidation_result = await self.consolidator.run()
        if self._drift is not None:
            try:
                report = await self._drift.check()
                self.last_drift_report = report
                if report.skipped:
                    pass
                elif report.drifted:
                    self.drift_hint = report.suggestion or DEFAULT_RE_ANCHOR
                else:
                    self.drift_hint = None   # 恢复在线，撤销校准提醒
            except Exception:
                log.exception("人格漂移检测失败（不影响巩固结果）")
        if self._sticker_tagger is not None:
            try:
                await self._sticker_tagger.run()
            except Exception:
                log.exception("表情包打标失败（不影响巩固结果）")
            if self.cfg.sticker.retain_days > 0 and self.sticker_lib is not None:
                try:
                    self.last_stickers_pruned = await self.sticker_lib.prune(
                        self.clock.now(), self.cfg.sticker.retain_days)
                except Exception:
                    log.exception("表情包遗忘清理失败（不影响巩固结果）")
        return self.last_consolidation_result

    def reset_window(self, group_id: str) -> None:
        """清空该群的对话窗口：此后回复只能依靠长期记忆（验收用）。"""
        self._window_reset_ts[group_id] = self.clock.now()
