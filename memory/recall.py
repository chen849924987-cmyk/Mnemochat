"""检索路径：混合召回（BM25 ∪ 向量 ∪ 实体图）→ 状态/人格准入过滤
→ salience 重排（艾宾浩斯保留率×访问×重要性）→ Top-K → 检索即强化。

纯本地计算（SQL + numpy），不调用 LLM，目标百毫秒级。
"""

from __future__ import annotations

import random

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config import BotConfig
from memory import decay
from memory.models import MemoryRecord, MessageRecord
from util.text import fts_query

if TYPE_CHECKING:
    from embed.base import EmbeddingProvider
    from memory.store import MemoryStore
    from util.clock import Clock

log = logging.getLogger(__name__)


@dataclass
class ScoredMemory:
    memory: MemoryRecord
    score: float
    factors: dict[str, float] = field(default_factory=dict)


@dataclass
class RecallResult:
    items: list[ScoredMemory]
    query_text: str = ""

    def __bool__(self) -> bool:
        return bool(self.items)


class Recaller:
    def __init__(self, store: "MemoryStore", embedder: "EmbeddingProvider",
                 cfg: BotConfig, clock: "Clock") -> None:
        self.store = store
        self.embedder = embedder
        self.cfg = cfg
        self.clock = clock

    async def recall(
        self,
        group_id: str,
        persona: Any,               # PersonaCard（duck-typed: allows(mem, group)）
        recent_msgs: list[MessageRecord],
        reinforce: bool = True,
    ) -> RecallResult:
        now = self.clock.now()
        rcfg = self.cfg.recall
        if not recent_msgs:
            return RecallResult([], "")

        window = recent_msgs[-rcfg.query_recent_messages:]
        # 查询串带上发言人昵称：谁在说话，关于谁的记忆就更容易浮上来
        query_text = " ".join(f"{m.user_name} {m.content}" for m in window)

        # 1) BM25 关键词召回
        bm25_raw = dict(await self.store.bm25_search(fts_query(query_text), rcfg.bm25_candidates))
        # 2) 向量语义召回
        qvec = (await self.embedder.embed([query_text]))[0]
        vec_sims = dict(
            await self.store.vector_topk(self.embedder.name, qvec, rcfg.vector_candidates,
                                         group_id=group_id)
        )
        # 3) 实体图一跳邻居
        names = await self.store.entity_names(group_id)
        mentioned = [
            n for n in names
            if any(n in m.content or n == m.user_name for m in window)
        ]
        ent_ids = await self.store.memories_by_entities(group_id, mentioned,
                                                        rcfg.entity_candidates)

        cand_ids = set(bm25_raw) | set(vec_sims) | set(ent_ids)
        if not cand_ids:
            return RecallResult([], query_text)
        rows = await self.store.get_memories_by_ids(list(cand_ids))

        # 过滤：active 状态 + staging 时限 + 保密等级 + 人格知识边界
        stage_cutoff = now - rcfg.staging_min_age_hours * 3600
        seal = (self.cfg.private_chat.enabled
                and self.cfg.private_chat.effective_secrecy)
        private_leak_ok = False
        if seal == 1 and group_id.startswith("private:") is False:
            # 偶尔说漏嘴：本次回复掷一次骰子（约 1/3 概率漏进群聊上下文）
            private_leak_ok = random.random() < 0.35
        allowed: list[MemoryRecord] = []
        for mem in rows.values():
            if mem.status != "active":
                continue
            if mem.stage != "ltm" and mem.created_at > stage_cutoff:
                continue
            if (mem.group_id.startswith("private:")
                    and group_id != mem.group_id):
                if seal == 2:
                    continue   # 守口如瓶：绝不注入任何其他会话（优先级最高）
                if seal == 1:
                    if not private_leak_ok:
                        continue   # 这次没说漏
                    allowed.append(mem)   # 说漏：骰子即放行，不再看 cross_group
                    continue
                if seal == 0:
                    allowed.append(mem)   # 口无遮拦：无视 cross_group 直接放行
                    continue
            if not persona.allows(mem, group_id):
                continue
            allowed.append(mem)
        if not allowed:
            return RecallResult([], query_text)

        sims = await self.store.vector_sims(self.embedder.name, qvec, [m.id for m in allowed])
        # bm25() 为负值（越接近 0 越相关），min-max 归一到 [0,1]
        bm25_ids = [m.id for m in allowed if m.id in bm25_raw]
        bm25_norm = dict(zip(bm25_ids, decay.normalize([bm25_raw[i] for i in bm25_ids])))

        weights = decay.SalienceWeights(**rcfg.weights.model_dump())
        scored: list[ScoredMemory] = []
        for mem in allowed:
            ret = decay.retention((now - mem.anchor_ts) / 86400.0, mem.strength)
            factors = {
                "similarity": sims.get(mem.id, 0.0),
                "bm25": bm25_norm.get(mem.id, 0.0),
                "retention": ret,
                "access_count": float(mem.access_count),
                "importance": mem.importance,
                "emotion": mem.emotion,
            }
            score = decay.salience(
                factors["similarity"], factors["bm25"], ret,
                mem.access_count, mem.importance, mem.emotion, weights,
            )
            scored.append(ScoredMemory(mem, score, factors))

        scored.sort(key=lambda s: s.score, reverse=True)
        top = scored[: rcfg.top_k]

        # 检索即强化：越想起越牢固
        if reinforce and top:
            await self.store.apply_reinforcement(
                [s.memory.id for s in top], now,
                self.cfg.decay.reinforce_factor, self.cfg.decay.strength_cap_days,
            )
        return RecallResult(top, query_text)
