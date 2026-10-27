"""检索路径：混合召回（BM25 ∪ 向量 ∪ 实体图）→ 状态/人格准入过滤
→ salience 重排（艾宾浩斯保留率×访问×重要性）→ Top-K → 检索即强化。

纯本地计算（SQL + numpy），不调用 LLM，目标百毫秒级。
"""

from __future__ import annotations

import random
import re

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config import BotConfig
from memory import decay
from memory.models import MemoryRecord, MessageRecord
from util.text import fmt_relative, fts_query

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
    display: str | None = None   # 分级压缩后的注入文本；None=原文（存储层永远原文）


@dataclass
class RecallResult:
    items: list[ScoredMemory]
    query_text: str = ""

    def __bool__(self) -> bool:
        return bool(self.items)


def _compressed_display(mem: MemoryRecord, ret: float) -> str | None:
    """按保留率给注入文本降级：0.45+ 原文；0.18~0.45 记得大概；<0.18 只剩关键词。"""
    if ret >= 0.45:
        return None
    kws = [k for k in (list(mem.keywords) + list(mem.entities)) if k][:4]
    if ret < 0.18 and kws:
        return "（印象模糊，只记得关键词：" + "、".join(kws) + "）"
    head = re.split(r"[。！？；\n]", mem.content, maxsplit=1)[0].strip()
    if len(head) > 42:
        head = head[:42] + "……"
    return "（记得大概）" + head


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

        # 人保底：关于具体的人的记忆在注入里至少保 person_seats 席（人>事，挤不掉的关系）
        top = scored[: rcfg.top_k]
        if rcfg.person_priority:
            have = sum(1 for s in top if s.memory.subject_user_id)
            if have < min(rcfg.person_seats, rcfg.top_k):
                chosen = {s.memory.id for s in top}
                for s in scored:
                    if have >= rcfg.person_seats:
                        break
                    if s.memory.id in chosen or not s.memory.subject_user_id:
                        continue
                    # 换掉 top 末尾一条非人记忆
                    for i in range(len(top) - 1, -1, -1):
                        if not top[i].memory.subject_user_id:
                            chosen.discard(top[i].memory.id)
                            top[i] = s
                            chosen.add(s.memory.id)
                            have += 1
                            break
            top.sort(key=lambda s: s.score, reverse=True)

        # 分级压缩注入：老记忆按保留率降级为"大概/关键词"，存储原文不动
        if rcfg.compress:
            for s in top:
                ret = decay.retention(
                    (now - s.memory.anchor_ts) / 86400.0, s.memory.strength)
                s.display = _compressed_display(s.memory, ret)

        # 检索即强化：越想起越牢固
        if reinforce and top:
            await self.store.apply_reinforcement(
                [s.memory.id for s in top], now,
                self.cfg.decay.reinforce_factor, self.cfg.decay.strength_cap_days,
            )
        return RecallResult(top, query_text)

    async def core_block(self, group_id: str, persona) -> str:
        """核心记忆钉：每轮必带的"长期在意的人与事"（与本次话题无关也带上）。

        候选 = 关系(bond)/偏好(preference)/关于具体的人的记忆，按
        重要性+召回+情绪排序取前 core_top_k 条；同样遵守人格边界与守口如瓶。"""
        rcfg = self.cfg.recall
        if not rcfg.core_memory:
            return ""
        rows = await self.store.list_memories(stage="ltm", limit=400)
        seal = (self.cfg.private_chat.enabled
                and self.cfg.private_chat.effective_secrecy)
        now = self.clock.now()
        cands = []
        for m in rows:
            if m.status != "active":
                continue
            foreign_private = (m.group_id.startswith("private:")
                               and m.group_id != group_id)
            if foreign_private and seal >= 1:
                continue   # 私聊秘密不进核心钉
            if not foreign_private and not persona.allows(m, group_id):
                continue   # 非 0 档的跨会话记忆仍走人格边界
            if not (m.kind in ("bond", "preference") or m.subject_user_id):
                continue
            cands.append(m)
        if not cands:
            return ""
        cands.sort(key=lambda m: (m.importance, m.access_count, m.emotion),
                   reverse=True)
        lines = ["【核心记忆】这些是你长期放在心上的人和事，与当前话题无关也可以自然想起："]
        for m in cands[: rcfg.core_top_k]:
            when = fmt_relative(m.anchor_ts, now)
            tag = {"bond": "关系", "preference": "偏好"}.get(m.kind, "")
            who = f"关于{m.subject_user_id}：" if m.subject_user_id else ""
            lines.append(f"- {who}{m.content}（{tag or '要紧事'}，{when}起就在心上）")
        return "\n".join(lines)
