"""人格漂移检测：抽样评审机器人近期发言与角色卡的一致性。

这是"人格存活"的最后防线：记忆系统保住了它知道什么，漂移检测保住它是谁。
每次巩固后抽样最近若干条机器人发言，交给评审模型打贴合度分；
判定漂移时，pipeline 会把"角色校准提醒"注入后续回复，直到下一轮检测恢复。
报告全部落库（drift_reports），人格健康的长期趋势可查。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config import BotConfig

if TYPE_CHECKING:
    from chat.llm import ChatBackend
    from memory.store import MemoryStore
    from persona.card import PersonaCard
    from util.clock import Clock

log = logging.getLogger(__name__)

DRIFT_SYSTEM = """你是角色一致性评审员。下面给出一个群聊机器人的角色设定，以及它最近的若干条真实发言。

评估这些发言是否仍然符合角色设定（口吻、身份感、价值观、口头禅），识别"人格漂移"迹象，例如：
- 变成客服腔 / AI 助手腔（"作为一个AI…""很高兴为您服务"）
- 说话风格突变（突然正式、突然长篇大论、丢失口头禅）
- 遗忘或否认自己的身份与经历

输出 JSON：{"adherence": 0-10, "drifted": true/false, "issues": ["问题描述", ...], "suggestion": "给机器人的一句话校正指令"}
adherence 为贴合度（10 = 完全在线，0 = 完全崩坏）；suggestion 仅在 drifted 时需要给出。"""


@dataclass
class DriftReport:
    ts: float = 0.0
    adherence: float = 0.0
    drifted: bool = False
    issues: list[str] = field(default_factory=list)
    suggestion: str = ""
    sample_size: int = 0
    skipped: bool = False          # 样本不足，本轮未检测


DEFAULT_RE_ANCHOR = (
    "【角色校准提醒】检测到你最近的发言偏离了角色设定，请立刻回到角色："
    "用角色应有的口吻说话，你不是AI助手，不要使用客服腔。"
)


class DriftDetector:
    def __init__(self, store: "MemoryStore", chat: "ChatBackend",
                 persona: "PersonaCard", cfg: BotConfig, clock: "Clock") -> None:
        self.store = store
        self.chat = chat
        self.persona = persona
        self.cfg = cfg
        self.clock = clock

    async def check(self) -> DriftReport:
        dcfg = self.cfg.drift
        now = self.clock.now()
        recent = await self.store.recent_messages_by_user(
            self.persona.name, limit=dcfg.sample_size
        )
        if len(recent) < dcfg.min_samples:
            return DriftReport(ts=now, skipped=True, sample_size=len(recent))

        replies = [m.content for m in reversed(recent)]  # 时间正序
        persona_desc = "\n".join(filter(None, [
            self.persona.identity.strip(),
            f"说话风格：{self.persona.speech_style}" if self.persona.speech_style else "",
            f"口头禅：{'、'.join(self.persona.catchphrases)}" if self.persona.catchphrases else "",
        ]))
        user = (
            f"【角色设定】\n{persona_desc}\n\n"
            f"【最近发言】\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(replies))
        )
        try:
            verdict = await self.chat.extract_json(DRIFT_SYSTEM, user)
        except Exception:
            log.exception("人格漂移检测调用失败，本轮跳过")
            return DriftReport(ts=now, skipped=True, sample_size=len(replies))

        try:
            adherence = min(10.0, max(0.0, float(verdict.get("adherence", 10))))
        except (TypeError, ValueError):
            adherence = 10.0
        issues = [str(x) for x in (verdict.get("issues") or [])][:5]
        drifted = bool(verdict.get("drifted")) or adherence < dcfg.adherence_threshold
        suggestion = str(verdict.get("suggestion", "")).strip()

        report = DriftReport(
            ts=now, adherence=adherence, drifted=drifted,
            issues=issues, suggestion=suggestion,
            sample_size=len(replies),
        )
        await self.store.add_drift_report(
            now, adherence, drifted, issues, suggestion, report.sample_size
        )
        if drifted:
            log.warning("人格漂移告警：贴合度 %.1f，问题：%s", adherence, issues)
        return report
