"""巩固管线（"睡眠"）：staging→LTM 晋升、矛盾取代、情节→语义的睡眠巩固。

设计原则：
- 晋升只改 stage 字段，不复制不删除，全部历史可溯源；
- 矛盾取代（Zep 式时效）：旧事实 status='superseded'，检索不再注入但可查历史；
- 睡眠巩固：语义相近的情节记忆聚类，LLM 抽象为语义记忆，源记忆标 archived
  （保留原文，永不删除）。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from config import BotConfig
from memory.decay import initial_strength
from memory.models import MemoryRecord

if TYPE_CHECKING:
    from chat.llm import ChatBackend
    from embed.base import EmbeddingProvider
    from memory.store import MemoryStore
    from util.clock import Clock

log = logging.getLogger(__name__)

CONFLICT_SYSTEM = """你是记忆矛盾判定器。判断"新记忆"是否与"旧记忆"描述同一主体的同一方面，且新记忆表明状态已经改变、旧信息因此失效。

判定为冲突的例子：
- 旧"小明爱吃辣" vs 新"小明现在不吃辣了" → 冲突（同一方面，状态改变）
判定为不冲突的例子：
- 旧"小明爱吃辣" vs 新"小明爱打篮球" → 不冲突（不同方面）
- 旧"小明爱吃辣" vs 新"小明昨晚吃了辣" → 不冲突（一个是偏好一个是事件）

输出 JSON：{"conflict": true/false, "reason": "一句话理由"}"""

SUMMARY_SYSTEM = """你是记忆归纳器（睡眠巩固）。把几条关于同一主题/同一人的零散观察归纳为一条语义记忆。

要求：
- 第三人称、自包含、含主语
- 保留全部具体细节与出现频次（如"提到过3次"）
- 不要引入观察中不存在的信息

输出 JSON：{"summary": str, "keywords": [str]}"""


@dataclass
class ConsolidationResult:
    promoted: int = 0
    superseded: int = 0
    semantic_created: int = 0
    archived: int = 0
    promoted_contents: list[str] = field(default_factory=list)
    superseded_contents: list[str] = field(default_factory=list)
    semantic_contents: list[str] = field(default_factory=list)


class Consolidator:
    def __init__(self, store: "MemoryStore", chat: "ChatBackend",
                 embedder: "EmbeddingProvider", cfg: BotConfig, clock: "Clock") -> None:
        self.store = store
        self.chat = chat
        self.embedder = embedder
        self.cfg = cfg
        self.clock = clock

    async def run(self) -> ConsolidationResult:
        now = self.clock.now()
        run_id = await self.store.begin_consolidation_run(now)
        result = ConsolidationResult()

        promoted = await self._promote(now)
        result.promoted = len(promoted)
        result.promoted_contents = [m.content for m in promoted]

        result.superseded = await self._resolve_contradictions(promoted, now)
        created, archived = await self._sleep_consolidate(now)
        result.semantic_created = created
        result.archived = archived

        await self.store.finish_consolidation_run(
            run_id, self.clock.now(), result.promoted, 0,
            result.superseded, created,
            notes=f"promoted_contents={result.promoted_contents}",
        )
        return result

    # ---------- 晋升 ----------

    async def _promote(self, now: float) -> list[MemoryRecord]:
        """staging 存活足够久 且（重要性达标 或 被复现强化过）→ 晋升 LTM。"""
        ccfg = self.cfg.consolidation
        cutoff = now - ccfg.min_age_hours * 3600
        staging = await self.store.list_memories(stage="staging", status="active", limit=1000)
        promoted: list[MemoryRecord] = []
        for mem in staging:
            if mem.created_at > cutoff:
                continue
            if mem.importance >= ccfg.min_importance or mem.recurrence_count >= 1:
                await self.store.update_memory(mem.id, stage="ltm")
                promoted.append(mem)
        return promoted

    # ---------- 矛盾取代 ----------

    async def _resolve_contradictions(self, promoted: list[MemoryRecord], now: float) -> int:
        """新晋升的事实/偏好 vs 同主体旧事实：LLM 判定冲突则旧记忆被取代。"""
        count = 0
        ccfg = self.cfg.consolidation
        for mem in promoted:
            if mem.kind not in ("fact", "preference") or not mem.subject_user_id:
                continue
            vec = await self.store.get_embedding(mem.id, self.embedder.name)
            if vec is None:
                continue
            hits = await self.store.vector_topk(
                self.embedder.name, vec, ccfg.supersede_check_topk + 1,
                group_id=mem.group_id,
            )
            for old_id, _sim in hits:
                if old_id == mem.id:
                    continue
                old = await self.store.get_memory(old_id)
                if old is None or old.status != "active" or old.stage != "ltm":
                    continue
                if old.subject_user_id != mem.subject_user_id:
                    continue
                if old.kind not in ("fact", "preference"):
                    continue
                try:
                    verdict = await self.chat.extract_json(
                        CONFLICT_SYSTEM, f"旧：{old.content}\n新：{mem.content}"
                    )
                except Exception:
                    log.exception("矛盾判定失败：%s vs %s", old.content, mem.content)
                    continue
                if verdict.get("conflict"):
                    await self.store.update_memory(
                        old.id, status="superseded", superseded_by=mem.id
                    )
                    count += 1
                    break
        return count

    # ---------- 睡眠巩固（情节 → 语义） ----------

    async def _sleep_consolidate(self, now: float) -> tuple[int, int]:
        """同群内向量相近的 active 情节记忆聚类成语义记忆，源记忆归档。"""
        ccfg = self.cfg.consolidation
        created = archived = 0
        eps = await self.store.list_memories(
            stage="ltm", status="active", kind="episodic", limit=500
        )
        by_group: dict[str, list[MemoryRecord]] = defaultdict(list)
        for m in eps:
            by_group[m.group_id].append(m)

        for group, items in by_group.items():
            if len(items) < ccfg.min_cluster_size:
                continue
            ids, matrix, _ = await self.store.ensure_vec_cache(self.embedder.name)
            if len(ids) == 0:
                continue
            pos = {mid: i for i, mid in enumerate(ids)}
            present = [m for m in items if m.id in pos]
            if len(present) < ccfg.min_cluster_size:
                continue
            idxs = [pos[m.id] for m in present]
            sub = matrix[idxs]
            sims = sub @ sub.T

            # 连通分量聚类
            n = len(present)
            parent = list(range(n))

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            for i in range(n):
                for j in range(i + 1, n):
                    if sims[i, j] >= ccfg.cluster_similarity:
                        ri, rj = find(i), find(j)
                        if ri != rj:
                            parent[ri] = rj

            comps: dict[int, list[int]] = defaultdict(list)
            for i in range(n):
                comps[find(i)].append(i)

            for members_idx in comps.values():
                if not (ccfg.min_cluster_size <= len(members_idx) <= 20):
                    continue
                members = [present[i] for i in members_idx]
                observations = "\n".join(f"- {m.content}" for m in members)
                try:
                    verdict = await self.chat.extract_json(SUMMARY_SYSTEM, observations)
                except Exception:
                    log.exception("睡眠巩固归纳失败：%s", observations[:100])
                    continue
                summary = str(verdict.get("summary", "")).strip()
                if not summary:
                    continue

                svec = (await self.embedder.embed([summary]))[0]
                # 已存在高度相似的语义记忆则跳过（幂等）
                dup = await self.store.vector_topk(self.embedder.name, svec, 1,
                                                   group_id=group)
                if dup and dup[0][1] >= 0.90:
                    continue

                max_imp = max(m.importance for m in members)
                new_mem = MemoryRecord(
                    stage="ltm",
                    kind="semantic",
                    group_id=group,
                    subject_user_id=members[0].subject_user_id,
                    content=summary,
                    keywords=[str(k) for k in verdict.get("keywords", [])][:8],
                    entities=list(dict.fromkeys(
                        e for m in members for e in m.entities
                    ))[:12],
                    importance=max_imp,
                    emotion=max(m.emotion for m in members),
                    visibility=members[0].visibility,
                    created_at=now,
                    strength=initial_strength(max_imp, self.cfg.decay.initial_base_days),
                    source_msg_ids=sorted({sid for m in members for sid in m.source_msg_ids}),
                )
                new_id = await self.store.add_memory(new_mem)
                await self.store.save_embedding(new_id, self.embedder.name, svec)
                for src in members:
                    await self.store.update_memory(
                        src.id, status="archived", archived_into=new_id
                    )
                created += 1
                archived += len(members)
        return created, archived
